"""climate_normals 모의 시험(네트워크 없음): 월 평균 계산·빠진 값·점검·이어받기·출력 크기.  python3 -m unittest tests/test_climate_normals.py"""
import calendar, json, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts'))
import climate_normals as C  # noqa: E402
import climate_cities as CC  # noqa: E402


def daily(tx=lambda y, m, d: 10.0 + m, tn=lambda y, m, d: m * 1.0, pr=lambda y, m, d: 2.0 if d <= 10 else 0.0, skip=lambda y, m, d: False):
    P = {'T2M_MAX': {}, 'T2M_MIN': {}, 'PRECTOTCORR': {}}
    for y in range(C.Y0, C.Y1 + 1):
        for m in range(1, 13):
            for d in range(1, calendar.monthrange(y, m)[1] + 1):
                k = f'{y}{m:02d}{d:02d}'
                miss = skip(y, m, d)
                P['T2M_MAX'][k] = C.FILL if miss else tx(y, m, d)
                P['T2M_MIN'][k] = C.FILL if miss else tn(y, m, d)
                P['PRECTOTCORR'][k] = C.FILL if miss else pr(y, m, d)
    return P


def resp(P):
    return json.dumps({'properties': {'parameter': P}}).encode()


CITY = {'id': 1, 'name': 'Testville', 'cc': 'TT', 'lat': 35.69, 'lon': 139.69}


class Agg(unittest.TestCase):
    def test_means(self):
        r = C.aggregate(daily())
        self.assertEqual(r['tx'], [11.0 + i for i in range(12)])
        self.assertEqual(r['tn'], [1.0 + i for i in range(12)])
        self.assertEqual(r['pr'], [20] * 12)          # 2 mm × 10일
        self.assertEqual(r['wd'], [10.0] * 12)
        self.assertEqual(C.check(r), '')

    def test_wet_threshold(self):
        r = C.aggregate(daily(pr=lambda y, m, d: 0.9 if d <= 5 else (1.0 if d <= 8 else 0.0)))
        self.assertEqual(r['wd'][0], 3.0)             # 0.9 는 세지 않고 1.0 은 센다
        self.assertEqual(r['pr'][0], 8)               # 0.9×5 + 1.0×3 = 7.5 → 8

    def test_february_leap(self):
        r = C.aggregate(daily(pr=lambda y, m, d: 1.0))
        feb = sum(calendar.monthrange(y, 2)[1] for y in range(C.Y0, C.Y1 + 1)) / 30
        self.assertEqual(r['pr'][1], round(feb)); self.assertEqual(r['wd'][1], round(feb, 1))
        self.assertEqual(r['pr'][0], 31)

    def test_missing_days_scaled_and_years_dropped(self):
        # 1월: 해마다 2일 빠짐(93% → 보정) / 3월: 7개 해가 통째로 빠짐(23년 → null) / 5월: 6개 해 빠짐(24년 → 값)
        sk = lambda y, m, d: (m == 1 and d <= 2) or (m == 3 and y < C.Y0 + 7) or (m == 5 and y < C.Y0 + 6)
        r = C.aggregate(daily(pr=lambda y, m, d: 1.0, skip=sk))
        self.assertEqual(r['pr'][0], 31); self.assertIsNone(r['pr'][2]); self.assertIsNone(r['wd'][2])
        self.assertEqual(r['pr'][4], 31); self.assertEqual(r['tx'][0], 11.0)

    def test_fill_never_leaks(self):
        r = C.aggregate(daily(skip=lambda y, m, d: True))
        self.assertTrue(all(v is None for k in ('tx', 'tn', 'pr', 'wd') for v in r[k]))
        self.assertEqual(C.check(r), '자료 없음')

    def test_check_rejects(self):
        ok = C.aggregate(daily())
        for k, v, why in (('tx', -5.0, '최고<최저'), ('tx', 70.0, '기온'), ('pr', 5000, '강수'), ('wd', 40.0, '강수')):
            bad = {a: list(b) for a, b in ok.items()}; bad[k][3] = v
            self.assertIn(why, C.check(bad))
        short = {a: list(b) for a, b in ok.items()}; short['pr'] = short['pr'][:11]
        self.assertIn('12칸', C.check(short))


class Fetch(unittest.TestCase):
    def test_url_has_no_key(self):
        u = C.url_for(35.689, 139.692)
        self.assertIn('latitude=35.69', u); self.assertIn('longitude=139.69', u); self.assertIn('start=19910101&end=20201231', u)
        self.assertNotIn('key', u.lower()); self.assertNotIn('token', u.lower())

    def test_retry_then_ok(self):
        n = {'c': 0}; naps = []
        def f(u):
            n['c'] += 1
            if n['c'] < 3:
                raise OSError('429')
            return resp(daily())
        r = C.city_normals(CITY, fetch=f, sleep=naps.append)
        self.assertEqual(r['pr'][0], 20); self.assertEqual(naps, [5, 10])

    def test_gives_up(self):
        def f(u): raise OSError('down')
        with self.assertRaises(RuntimeError):
            C.city_normals(CITY, fetch=f, sleep=lambda s: None)

    def test_bad_data_not_retried(self):
        n = {'c': 0}
        def f(u):
            n['c'] += 1; return resp(daily(skip=lambda y, m, d: True))
        with self.assertRaises(ValueError):
            C.city_normals(CITY, fetch=f, sleep=lambda s: None)
        self.assertEqual(n['c'], 1)


class Build(unittest.TestCase):
    def cities(self, n):
        return [{'id': i + 1, 'name': f'C{i}', 'cc': 'TT', 'lat': 10.0 + i, 'lon': 100.0 + i} for i in range(n)]

    def test_resume_and_partial_failure(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            def f(u):
                calls.append(u)
                if 'latitude=11.00' in u:
                    raise OSError('down')
                return resp(daily())
            rows, failed = C.build(self.cities(3), d, fetch=f, sleep=lambda s: None, log=lambda s: None)
            self.assertEqual([r['id'] for r in rows], [1, 3]); self.assertEqual(failed, [2])
            before = len(calls)
            rows, failed = C.build(self.cities(3), d, fetch=lambda u: resp(daily()), sleep=lambda s: None, log=lambda s: None)
            self.assertEqual(len(rows), 3); self.assertEqual(failed, [])
            self.assertEqual(before, 1 + 3 + 1)       # 첫 판: 성공 1 + 실패 3회 시도 + 성공 1
            self.assertFalse([x for x in os.listdir(os.path.join(d, 'cache')) if x.endswith('.tmp')])

    def test_cache_invalid_when_city_moves(self):
        with tempfile.TemporaryDirectory() as d:
            n = {'c': 0}
            def f(u): n['c'] += 1; return resp(daily())
            cs = self.cities(1)
            C.build(cs, d, fetch=f, sleep=lambda s: None); C.build(cs, d, fetch=f, sleep=lambda s: None)
            self.assertEqual(n['c'], 1)
            cs[0]['lat'] = 50.0
            C.build(cs, d, fetch=f, sleep=lambda s: None)
            self.assertEqual(n['c'], 2)

    def test_stops_asking_after_many_failures(self):
        with tempfile.TemporaryDirectory() as d:
            n = {'c': 0}
            def f(u): n['c'] += 1; raise OSError('down')
            rows, failed = C.build(self.cities(30), d, fetch=f, sleep=lambda s: None, log=lambda s: None, max_fail=5)
            self.assertEqual(len(failed), 30); self.assertEqual(n['c'], 15)

    def test_output_small_and_shaped(self):
        with tempfile.TemporaryDirectory() as d:
            rows, _ = C.build(self.cities(720), d, fetch=lambda u: resp(daily()), sleep=lambda s: None)
            js = C.to_json(rows, '2026-11-17')
            self.assertLess(len(js.encode()), C.MAX_BYTES)
            doc = json.loads(js)
            self.assertEqual(doc['period'], [1991, 2020]); self.assertEqual(len(doc['cities']), 720)
            self.assertIn('NASA POWER', doc['source']['name']); self.assertIn('not a station', doc['source']['grid'])
            dart = C.to_dart(rows, '2026-11-17')
            self.assertIn("const bool kTlP7HasWet = false;", dart)
            line = dart.split("'''\n")[1].split('\n')[0].split('|')
            self.assertEqual(len(line), 8); self.assertEqual(line[5].split(',')[0], '110')
            self.assertEqual(len(C.to_dart(rows, 'x', wet=True).split("'''\n")[1].split('\n')[0].split('|')), 9)

    def test_dart_name_cannot_break_string(self):
        rows = [{'id': 1, 'n': "A'b|c$d\\e", 'cc': 'TT', 'la': 1.0, 'lo': 2.0, 'tx': [None] * 12, 'tn': [1.0] * 12, 'pr': [3] * 12, 'wd': [1.0] * 12}]
        body = C.to_dart(rows, 'x').split("'''\n")[1]
        for ch in "'$\\":
            self.assertNotIn(ch, body.split("\n'''")[0])
        self.assertEqual(body.split('\n')[0].split('|')[5], ',' * 11)


class Cities(unittest.TestCase):
    def row(self, gid, name, lat, lon, fcode, cc, pop):
        f = [''] * 19; f[0], f[2], f[4], f[5], f[7], f[8], f[14] = str(gid), name, str(lat), str(lon), fcode, cc, str(pop)
        return '\t'.join(f)

    def test_pick_rules(self):
        rows = [self.row(1, 'Tokyo', 35.69, 139.69, 'PPLC', 'JP', 8_000_000), self.row(2, 'Smalltown', 36.5, 138.0, 'PPL', 'JP', 50_000),
                self.row(3, 'Takayama', 36.13, 137.25, 'PPL', 'JP', 60_000), self.row(4, 'Midcity', 48.0, 2.0, 'PPL', 'FR', 900_000),
                self.row(5, 'Shinjuku', 35.70, 139.70, 'PPLA2', 'JP', 300_000), self.row(6, 'Tinycap', 1.0, 1.0, 'PPLC', 'ZZ', 900), 'short\tline']
        got = CC.pick(rows)
        self.assertEqual([g[0] for g in got], [1, 3, 6])   # 작은 도시·기준 미달 빠짐, 같은 칸은 인구 큰 쪽

    def test_committed_list(self):
        cs = C.read_cities(os.path.join(HERE, '..', 'sources', 'climate_cities.csv'))
        self.assertTrue(300 <= len(cs) <= 900)
        self.assertEqual(len({c['id'] for c in cs}), len(cs))
        self.assertTrue(all(-90 <= c['lat'] <= 90 and -180 <= c['lon'] <= 180 and len(c['cc']) == 2 for c in cs))
        self.assertTrue({'Tokyo', 'Seoul', 'Taipei', 'Bangkok'} <= {c['name'] for c in cs})


if __name__ == '__main__':
    unittest.main()
