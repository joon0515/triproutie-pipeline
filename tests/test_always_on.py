"""상시 가동(11/18 233기) 시험 — 저장 상한·추세 표·증분 갱신 주기·지역 우선순위·사슬 끊김 방지·출처별 속도 제한. 네트워크 없음."""
import json, os, shutil, sys, tempfile, unittest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, os.path.join(ROOT, 'scripts')); sys.path.insert(0, os.path.join(ROOT, 'scripts', 'collect'))
import common as K  # noqa: E402
import coverage_trend as CT  # noqa: E402
import run as R  # noqa: E402
import storage_guard as SG  # noqa: E402
WF = os.path.join(ROOT, '.github', 'workflows')
G = 2 ** 30


class StorageGuard(unittest.TestCase):
    def test_under_warn(self):
        u, alert = SG.evaluate({'raw': 100 * G}, 100 * G, env={})
        self.assertEqual((u['warn'], u['pause_collect'], alert), (False, False, None))
        self.assertEqual(u['total_gib'], 100.0)
        self.assertAlmostEqual(u['est_usd_month'], 2.6)

    def test_warn_at_80_percent(self):
        u, alert = SG.evaluate({'raw': 410 * G}, 410 * G, env={})
        self.assertTrue(u['warn']); self.assertFalse(u['pause_collect'])
        self.assertIn('410.0 GiB', alert)

    def test_pause_at_cap_and_cost_cap_counts(self):
        u, alert = SG.evaluate({'raw': 520 * G}, 520 * G, env={})
        self.assertTrue(u['pause_collect']); self.assertIn('새 원본 수집 멈춤', alert)
        u, _ = SG.evaluate({'raw': 100 * G}, 100 * G, env={'COST_CAP_USD_MONTH': '2'})  # 용량은 20% 지만 비용이 상한 넘음
        self.assertTrue(u['pause_collect'])

    def test_unmeasured_never_pauses(self):
        u, alert = SG.evaluate({'raw': None}, None, env={})
        self.assertEqual((u['ratio'], u['pause_collect'], alert), (None, False, None))

    def test_plan_pauses_when_over_cap(self):
        d = tempfile.mkdtemp()
        try:
            st = K.Store(d)
            st.put_json('guards/usage.json', {'pause_collect': True, 'total_gib': 600, 'cap_gib': 500})
            sys.argv = ['run.py', 'plan', '--store', d]
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                R.main()
            self.assertIn('any=false', buf.getvalue())
        finally:
            shutil.rmtree(d)


def rep(date, n, ko, h, photo, desc_any=None):
    lay = {'places': {'n': n, 'ko': ko, 'en': n // 2, 'ja': 0, 'zh_Hans': 0, 'zh_Hant': 0, 'h': h, 'photo': photo}}
    if desc_any is not None:
        lay['desc'] = {'all': {'n': n, 'any': desc_any, 'ko': desc_any, 'en': 0, 'ja': 0, 'zh_Hans': 0, 'zh_Hant': 0}}
    return {'date': date, 'layers': lay}


class Trend(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        for r in (rep('2026-11-10', 1000, 100, 10, 5), rep('2026-11-17', 2000, 300, 40, 20, 100), rep('2026-11-18', 2000, 400, 60, 20, 200)):
            json.dump(r, open(os.path.join(self.d, r['date'] + '.json'), 'w'))
        open(os.path.join(self.d, 'junk.txt'), 'w').write('x')

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_table_rates_and_trend(self):
        pts = CT.load(self.d)
        self.assertEqual([p['date'] for p in pts], ['2026-11-10', '2026-11-17', '2026-11-18'])
        md = CT.table(pts, now='T')
        self.assertIn('| 2026-11-18 | 2,000 | 20.0% | 50.0% | 0.0% | 0.0% | 0.0% | 3.0% | 1.0% | 10.0% |', md)
        self.assertIn('| 이름 ko | 20.0% | +5.0%p | +10.0%p | - |', md)  # 1일 전 15%, 7일 전(11/10 이하 가장 가까운 날) 10%, 30일 전 없음
        self.assertIn('| 게시 장소 수 | 2,000 | +0 | +1,000 | - |', md)
        self.assertIn('| 소개(1개 언어+) | 10.0% | +5.0%p | - | - |', md)
        self.assertTrue(md.startswith(CT.START) and md.rstrip().endswith(CT.END))

    def test_update_replaces_only_block(self):
        doc = '# 제목\n\n본문\n'
        a = CT.update_md(doc, CT.table(CT.load(self.d), now='T1'))
        b = CT.update_md(a + '\n꼬리\n', CT.table(CT.load(self.d), now='T2'))
        self.assertEqual(b.count(CT.START), 1)
        self.assertIn('갱신 T2', b); self.assertNotIn('갱신 T1', b)
        self.assertTrue(b.startswith('# 제목\n\n본문\n')); self.assertTrue(b.rstrip().endswith('꼬리'))

    def test_empty(self):
        self.assertIn('아직 측정값이 없다', CT.table([], now='T'))

    def test_coverage_doc_has_block(self):
        md = open(os.path.join(ROOT, 'docs', 'COVERAGE.md'), encoding='utf-8').read()
        self.assertEqual((md.count(CT.START), md.count(CT.END)), (1, 1))


class Incremental(unittest.TestCase):
    def test_policy_short_for_hours_long_for_names(self):
        d = {k: K.refresh_days(k, -1) for k in ('hours', 'desc', 'photos', 'names', 'datasets', 'gtfs')}
        self.assertNotIn(-1, d.values())
        self.assertLess(d['hours'], d['desc']); self.assertLessEqual(d['desc'], d['photos']); self.assertLess(d['photos'], d['names'])
        self.assertEqual(K.refresh_days('없는칸', 7), 7)

    def test_scripts_use_policy(self):
        import place_desc as PD, place_names as PN
        self.assertEqual(PD.REFRESH_DAYS, K.refresh_days('desc', 0))
        self.assertEqual(PN.C_REFRESH, K.refresh_days('photos', 0))

    def test_crawl_skips_fresh_and_refreshes_stale_hours(self):
        import collectors as C
        seeds = {'Q1': {'url': 'https://a.example/', 'nm': 'A', 'lat': 1, 'lon': 1, 'c': 'museum', 'cc': 'FR'},
                 'Q2': {'url': 'https://b.example/', 'nm': 'B', 'lat': 1, 'lon': 1, 'c': 'museum', 'cc': 'JP'},
                 'Q3': {'url': 'https://c.example/', 'nm': 'C', 'lat': 1, 'lon': 1, 'c': 'museum', 'cc': 'KR'}}
        import datetime
        t = datetime.date.fromisoformat(K.today())
        old, fresh = (t - datetime.timedelta(days=K.refresh_days('hours', 14))).isoformat(), (t - datetime.timedelta(days=2)).isoformat()
        visited = []

        class Ctx:
            shard = '0'
            st = {'done': {'Q1': old, 'Q3': fresh}}

            class store:
                @staticmethod
                def get_json(k, d=None):
                    return seeds

            def tick(self): pass
            def emit(self, *a): pass
            def note(self, m): pass
        orig = C.crawl_site
        C.crawl_site = lambda ctx, qid, seed, max_pages=15: (visited.append(qid), (None, []))[1]
        try:
            C.crawl(Ctx())
        finally:
            C.crawl_site = orig
        self.assertEqual(visited, ['Q2', 'Q1'])  # 처음 보는 곳 → 14일 지난 곳. 2일 전에 본 곳은 건너뜀(변경분만)


class Priority(unittest.TestCase):
    def test_rank_tiers_and_demand(self):
        self.assertEqual(K.region_rank('KR'), 0); self.assertEqual(K.region_rank('FR'), 1)
        self.assertEqual(K.region_rank('BR'), 2); self.assertEqual(K.region_rank(None), 0)
        self.assertLess(K.region_rank('BR', {'BR': 50, 'JP': 10}), K.region_rank('JP', {'BR': 50, 'JP': 10}))
        self.assertLess(K.region_rank('JP', {'BR': 50, 'JP': 10}), K.region_rank('KR', {'BR': 50, 'JP': 10}))  # 검색 기록이 있는 나라가 단계보다 앞
        self.assertEqual((K.shard_cc('japan:JP'), K.shard_cc('en'), K.shard_cc('0')), ('JP', None, None))

    def test_plan_first_pass_goes_by_demand(self):
        reg = {'sources': [{'id': 'overture_places', 'status': 'active', 'script': 'overture', 'refresh': 'monthly', 'daily_limit': 1,
                            'shards': None}]}
        orig = R.shards
        R.shards = lambda s: ['europe_west:FR', 'south_america:BR', 'japan:JP', 'korea:KR']
        try:
            p = [x['shard'] for x in R.plan(reg, {}, env={}, max_jobs=3)]
            self.assertEqual(p, ['japan:JP', 'korea:KR', 'europe_west:FR'])
            p = [x['shard'] for x in R.plan(reg, {}, env={}, max_jobs=2, demand={'BR': 9})]
            self.assertEqual(p, ['south_america:BR', 'japan:JP'])
            idx = {'overture_places/japan:JP': {'last_ok': '2026-11-01'}}  # 이미 받은 조각은 뒤로(오래된 순 갱신)
            self.assertEqual([x['shard'] for x in R.plan(reg, idx, env={}, max_jobs=4)][-1], 'japan:JP')
        finally:
            R.shards = orig


class Chain(unittest.TestCase):
    def steps(self):
        y = yaml.safe_load(open(os.path.join(WF, 'collect.yml'), encoding='utf-8'))
        return {s['name'].split(' ')[0]: s for s in y['jobs']['chain']['steps']}, y

    def test_chain_cannot_break_on_optional_steps(self):
        st, y = self.steps()
        self.assertTrue(st['가공(process)']['continue-on-error'])
        nxt = st['다음']['run']
        self.assertIn('for i in 1 2 3 4 5', nxt)
        self.assertIn('|| true', st['1시간']['run'])  # 시작 시각 조회 실패로 단계가 죽지 않음
        names = [s['name'] for s in y['jobs']['chain']['steps']]
        after = names[names.index(st['다음']['name']) + 1:]
        for s in y['jobs']['chain']['steps']:
            if s['name'] in after:
                self.assertTrue(s.get('continue-on-error'), s['name'])  # 다음 수집을 건 뒤 단계는 실패해도 실행을 실패로 만들지 않음
        self.assertIn('watchdog.yml', st['watchdog']['run'])
        self.assertIn('always()', y['jobs']['chain']['if'])

    def test_watchdog_workflow(self):
        y = yaml.safe_load(open(os.path.join(WF, 'watchdog.yml'), encoding='utf-8'))
        on = y.get('on', y.get(True))
        self.assertNotEqual(on['schedule'][0]['cron'].split()[0], '0')  # 정각(부하) 피함
        self.assertIn('workflow_dispatch', on)
        self.assertEqual(y['permissions'], {'contents': 'read', 'actions': 'write', 'issues': 'write'})
        self.assertFalse(y['concurrency']['cancel-in-progress'])

    def test_no_graphql_issue_calls_in_new_steps(self):
        for f in ('failure-digest.yml', 'coverage-report.yml', 'watchdog.yml'):
            self.assertNotIn('gh issue ', open(os.path.join(WF, f), encoding='utf-8').read(), f)


class RateLimits(unittest.TestCase):
    def test_every_source_we_call_declares_limits(self):
        reg = yaml.safe_load(open(os.path.join(ROOT, 'sources', 'registry.yaml'), encoding='utf-8'))
        for s in reg['sources']:
            if s.get('status') == 'active' or s['id'] in ('tourapi', 'osm', 'commons_sdc'):
                self.assertGreaterEqual(int(s.get('daily_limit') or 0), 1, s['id'])
                self.assertTrue(s.get('interval_s') or s.get('max_parallel'), s['id'])
            if s.get('status') == 'active':
                self.assertGreaterEqual(float(s['interval_s']), 1, s['id'])  # 사이트당 초당 1회 이하

    def test_tourapi_min_interval(self):
        import tourapi_daily as T
        clock, slept = [100.0], []

        def sleep(x):
            slept.append(round(x, 2)); clock[0] += x
        body = json.dumps({'response': {'header': {'resultCode': '0000'}, 'body': {'items': {'item': [{'a': 1}]}}}})
        c = T.Client('k', {}, 10, opener=lambda u: body, sleep=sleep, interval=1.0, clock=lambda: clock[0])
        c.get('x'); clock[0] += 0.25; c.get('x'); c.get('x')
        self.assertEqual(slept, [0.75, 1.0])


if __name__ == '__main__':
    unittest.main()
