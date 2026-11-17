"""트립루티 추천 지수(scripts/rec_index.py) — 맞추기 품질·점수·파일 모양·coverage 9절. 네트워크 없음."""
import gzip, json, os, random, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.path.insert(0, os.path.join(ROOT, 'scripts', 'collect'))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import rec_index as R  # noqa: E402
import coverage as COV  # noqa: E402
from common import norm_name  # noqa: E402
from process import to_layer  # noqa: E402

CHAINS = ['Starbucks', 'Paris Baguette', 'CU', 'GS25', 'Olive Young', 'Ediya Coffee', 'Lotteria', 'Kyochon']
WORDS = ['Garden', 'Palace', 'House', 'Kitchen', 'Table', 'Hill', 'River', 'Moon', 'Sun', 'Stone', 'Blue', 'Green', 'Old', 'New', 'Royal']
CATS = ['restaurant', 'cafe', 'hotel', 'museum', 'park', 'bakery']


def dense_city(n=3000, seed=7):
    """1.5 km × 1.5 km 에 n 곳(체인 이름이 150 m 마다 되풀이되는 도심 밀도 — 서울 명동 표본 2 km² 9,687곳과 비슷한 밀도)."""
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        if rnd.random() < 0.25:
            nm = rnd.choice(CHAINS)
        else:
            nm = f"{rnd.choice(WORDS)} {rnd.choice(WORDS)} {rnd.choice(CATS).title()} {i % 97}"
        out.append({'name': nm, 'la': 37.56 + rnd.random() * 0.0135, 'lo': 126.97 + rnd.random() * 0.017, 'c': rnd.choice(CATS)})
    return out


def jitter(p, rnd, m=25):
    return p['la'] + rnd.uniform(-m, m) / 111000, p['lo'] + rnd.uniform(-m, m) / 88000


def variant(nm, rnd):
    k = rnd.random()
    if k < 0.3:
        return nm.upper()
    if k < 0.5:
        return nm.replace(' ', '')
    if k < 0.65:
        return nm + ' Myeongdong'
    return nm


class MatchQuality(unittest.TestCase):
    def test_precision_recall_dense(self):
        """같은 곳(좌표 ±25 m·이름 변형) 은 찾고, 다른 곳(같은 체인 이름 이웃 포함) 은 잇지 않는다. 기준: 정밀도 ≥ 0.98 · 재현율 ≥ 0.90."""
        rnd = random.Random(1)
        base = dense_city()
        ovt, ours, truth = [], [], {}
        for i, p in enumerate(base):
            ovt.append({'id': f'o{i}', 'nm': norm_name(p['name']), 'la': p['la'], 'lo': p['lo'], 't': COV.type_of(p['c']), 'fsq': []})
            if rnd.random() < 0.7:  # 우리 쪽에는 70% 만 있음(나머지는 새 장소)
                la, lo = jitter(p, rnd)
                truth[len(ours)] = i
                ours.append({'id': f'x:{i}', 'nm': norm_name(variant(p['name'], rnd)), 'la': la, 'lo': lo, 't': COV.type_of(p['c'])})
        for k in range(300):  # 우리에게만 있는 곳(Overture 에 없음) — 체인 이름이 섞여 틀린 짝이 생기기 쉬운 쪽
            p = base[rnd.randrange(len(base))]
            ours.append({'id': f'y:{k}', 'nm': norm_name(rnd.choice(CHAINS) if rnd.random() < 0.5 else f'Only Here {k}'),
                         'la': 37.56 + rnd.random() * 0.0135, 'lo': 126.97 + rnd.random() * 0.017, 't': COV.type_of(rnd.choice(CATS))})
        m = R.match(ovt, ours)
        tp = sum(1 for j, (i, _) in m.items() if truth.get(j) == i)
        prec, rec = tp / max(len(m), 1), tp / len(truth)
        print(f'\n[맞추기 품질·합성 도심] 짝 {len(m)} · 정밀도 {prec:.3f} · 재현율 {rec:.3f}')
        self.assertGreaterEqual(prec, 0.98)
        self.assertGreaterEqual(rec, 0.90)

    def test_keys_first(self):
        ovt = [{'id': 'a', 'nm': 'abc', 'la': 37.0, 'lo': 127.0, 't': 'food', 'fsq': ['F1']},
               {'id': 'b', 'nm': 'zzz', 'la': 37.0, 'lo': 127.0, 't': 'sight', 'q': 'Q5', 'fsq': []}]
        ours = [{'id': 'fsq_os_places:F1', 'nm': 'totallydifferent', 'la': 37.003, 'lo': 127.0, 't': 'food'},
                {'id': 'wikivoyage:x', 'nm': 'other', 'la': 37.01, 'lo': 127.0, 't': 'sight', 'q': 'Q5'}]
        self.assertEqual(R.match(ovt, ours), {0: (0, 'fsq'), 1: (1, 'wd')})

    def test_rejects(self):
        o = [{'id': 'a', 'nm': norm_name('Seoul Tower'), 'la': 37.0, 'lo': 127.0, 't': 'sight', 'fsq': []}]
        far = [{'id': 'x:1', 'nm': norm_name('Seoul Tower'), 'la': 37.004, 'lo': 127.0, 't': 'sight'}]  # 약 445 m
        cat = [{'id': 'x:2', 'nm': norm_name('Seoul Tower'), 'la': 37.0001, 'lo': 127.0, 't': 'lodging'}]
        self.assertEqual(R.match(o, far), {})
        self.assertEqual(R.match(o, cat), {})
        near_exact_sight = [{'id': 'x:3', 'nm': norm_name('SEOUL  tower'), 'la': 37.0024, 'lo': 127.0, 't': 'sight'}]  # 약 267 m, 명소·같은 이름
        self.assertEqual(R.match(o, near_exact_sight), {0: (0, 'name')})

    def test_one_to_one(self):
        o = [{'id': 'a', 'nm': 'starbucks', 'la': 37.0, 'lo': 127.0, 't': 'food', 'fsq': []}]
        ours = [{'id': 'x:1', 'nm': 'starbucks', 'la': 37.0001, 'lo': 127.0, 't': 'food'},
                {'id': 'x:2', 'nm': 'starbucks', 'la': 37.0002, 'lo': 127.0, 't': 'food'}]
        self.assertEqual(len(R.match(o, ours)), 1)


class Score(unittest.TestCase):
    def test_range_and_order(self):
        lo = {'cross': 1, 'conf': None, 'sitelinks': None, 'wiki': 0, 'kto': None, 'mention': None}
        hi = {'cross': 5, 'conf': 1.0, 'sitelinks': 150, 'wiki': 1, 'kto': None, 'mention': 1.0}
        self.assertEqual(R.evidence(lo), 0.0)
        self.assertAlmostEqual(R.evidence(hi), 1.0)
        fin = R.finalize({'sight': [(R.evidence(lo), 'a'), (R.evidence(hi), 'b'), (0.5, 'c')]})
        self.assertEqual(fin['a'], (0, 0)); self.assertEqual(fin['b'], (100, 100))
        self.assertTrue(0 < fin['c'][0] < 100)

    def test_unavailable_signal_renormalized(self):
        """관광공사 신호는 한국 밖에서 None — 빼고 다시 나눈다(한국 밖 장소가 10점 손해를 보지 않음)."""
        a = {'cross': 4, 'conf': 1.0, 'sitelinks': 100, 'wiki': 1, 'kto': None, 'mention': 1.0}
        self.assertAlmostEqual(R.evidence(a), 1.0)
        self.assertLess(R.evidence(dict(a, kto=0)), 1.0)

    def test_n_signals_same_as_coverage(self):
        s = {'cross': 2, 'conf': 0.9, 'sitelinks': 3, 'wiki': 1, 'kto': 0, 'mention': None}
        v = [50, 2, 90, 3, 1, 0, None, 40]
        self.assertEqual(R.n_signals(s), COV.rec_signals(v))


def col(cc, rows):
    return to_layer(cc, rows)


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        rows = [
            {'id': 'fsq_os_places:F1', 'nm': 'Gyeongbokgung', 'lat': 37.5796, 'lon': 126.977, 'c': 'palace', 'src': 'fsq_os_places', 'wd': 'Q482435', 'lic': 'Apache-2.0'},
            {'id': 'fsq_os_places:F2', 'nm': 'Cafe Onion', 'lat': 37.58, 'lon': 126.98, 'c': 'cafe', 'src': 'fsq_os_places', 'en': 'Cafe Onion', 'lic': 'Apache-2.0'},
            {'id': 'fsq_os_places:F3', 'nm': 'Bus stop 12', 'lat': 37.581, 'lon': 126.981, 'c': 'busstop', 'src': 'fsq_os_places', 'lic': 'Apache-2.0'},
        ]
        self.layer = col('KR', rows)
        self.ovt = [
            {'id': 'o1', 'name': 'Gyeongbokgung Palace', 'names': {'ko': '경복궁', 'ja': '景福宮', 'zh_Hant': '景福宮'}, 'c': 'palace', 'cf': 0.98,
             'la': 37.5797, 'lo': 126.9771, 'srcs': ['meta', 'Foursquare'], 'fsq': ['F1'], 'lic': ['Apache-2.0', 'CDLA-Permissive-2.0']},
            {'id': 'o2', 'name': 'Cafe Onion Anguk', 'names': {'en': 'Cafe Onion Anguk'}, 'c': 'cafe', 'cf': 0.7, 'la': 37.58005, 'lo': 126.98, 'srcs': ['meta'], 'fsq': [],
             'lic': ['CDLA-Permissive-2.0']},
            {'id': 'o3', 'name': 'New Museum', 'names': {'en': 'New Museum'}, 'c': 'museum', 'cf': 0.91, 'la': 37.59, 'lo': 126.99, 'srcs': ['meta', 'AllThePlaces'], 'fsq': [],
             'lic': ['CC0-1.0', 'CDLA-Permissive-2.0']},
            {'id': 'o4', 'name': 'Weak Place', 'names': {}, 'c': 'restaurant', 'cf': 0.6, 'la': 37.595, 'lo': 126.995, 'srcs': ['meta'], 'fsq': [], 'lic': ['CDLA-Permissive-2.0']},
        ]
        self.kto = [{'id': 'kto:1', 'nm': norm_name('Gyeongbokgung'), 'la': 37.5795, 'lo': 126.977}]

    def test_build_cc(self):
        rec, names, adds, st = R.build_cc('KR', [self.layer], self.ovt, {'Q482435': (180, 1)}, self.kto, {'KR': {'sight': 1.0, 'food': 0.6}})
        self.assertEqual(st['ours'], 2)  # 버스 정류장은 일정 대상이 아님
        self.assertEqual((st['m_fsq'], st['m_name'], st['kto']), (1, 1, 1))
        self.assertEqual([a['id'] for a in adds], ['overture_places:o3'])  # conf 0.6 은 더하지 않음
        g = rec['fsq_os_places:F1']
        self.assertEqual(len(g), len(R.FIELDS)); self.assertEqual(g[2], 98); self.assertEqual(g[3], 180); self.assertEqual(g[5], 1)
        self.assertGreater(g[0], rec['fsq_os_places:F2'][0])
        self.assertEqual(names['fsq_os_places:F1']['ko'], '경복궁')
        self.assertNotIn('en', {k for k, v in names.get('fsq_os_places:F2', {}).items() if v})  # 우리에게 이미 영어 이름이 있음
        self.assertTrue(all(0 <= v[0] <= 100 for v in rec.values()))
        # 다음 실행에 가공이 같은 id 를 실으면 다시 더하지 않음
        _, _, adds2, _ = R.build_cc('KR', [self.layer], self.ovt, {}, [], {}, prev_ids={'overture_places:o3'})
        self.assertEqual(adds2, [])

    def test_files_and_coverage(self):
        rec, names, adds, _ = R.build_cc('KR', [self.layer], self.ovt, {'Q482435': (180, 1)}, self.kto, {})
        fs = R.emit(self.d, 'KR', rec, names, adds)
        self.assertEqual(sorted(os.path.basename(f) for f in fs), ['col_ovtadd_KR-00.json.gz', 'names-ovt-KR-00.json.gz', 'rec-KR-00.json.gz'])
        j = json.loads(gzip.decompress(open(os.path.join(self.d, 'rec-KR-00.json.gz'), 'rb').read()))
        self.assertEqual(j['schema'], 'rec-1'); self.assertEqual(j['label']['ko'], '트립루티 추천 지수')
        self.assertIn('CDLA-Permissive-2.0', json.dumps(j['licenses'])); self.assertIn('Apache-2.0', json.dumps(j['licenses']))
        a = json.loads(gzip.decompress(open(os.path.join(self.d, 'col_ovtadd_KR-00.json.gz'), 'rb').read()))
        self.assertTrue(all(a['lic']) and all(a['src']))
        for bad in ('평점', '방문자 평균', '추정', 'rating', 'review'):
            self.assertNotIn(bad, json.dumps(j, ensure_ascii=False))
        open(os.path.join(self.d, 'col_KR.json.gz'), 'wb').write(gzip.compress(json.dumps(self.layer).encode()))
        r = COV.measure_dir(self.d, {})
        self.assertEqual(r['rec']['itinerary']['n'], 3)       # 일정 장소 2 + 새로 더한 1
        self.assertEqual(r['rec']['itinerary']['rec'], 3)
        self.assertEqual(r['rec']['itinerary']['added'], 1)
        self.assertEqual(r['itinerary']['ja'], 1)             # Overture 이름 층(names-2)이 일본어 칸을 채움
        md = COV.to_md(COV.build({}, r, date='2026-11-18'))
        self.assertIn('### 9. 트립루티 추천 지수 채움률', md)

    def test_split_under_cap(self):
        old = R.MAX_GZ
        try:
            R.MAX_GZ = 3000
            rec = {f'x:{i}': [i % 100, 2, 90, 3, 1, None, None, 50] for i in range(5000)}
            fs = R.write_split(self.d, 'rec-ZZ', sorted(rec), lambda ks: {'rows': {k: rec[k] for k in ks}}, 4000)
            self.assertGreater(len(fs), 1)
            got = {}
            for f in fs:
                self.assertLessEqual(os.path.getsize(f), 3000 * 4)  # 1000 행 아래로는 더 나누지 않음
                got.update(json.loads(gzip.decompress(open(f, 'rb').read()))['rows'])
            self.assertEqual(len(got), 5000)
        finally:
            R.MAX_GZ = old

    def test_publish_accepts(self):
        import publish_layers as PL
        rec, names, adds, _ = R.build_cc('KR', [self.layer], self.ovt, {}, [], {})
        fs = R.emit(self.d, 'KR', rec, names, adds)
        kinds = sorted(PL.describe(f)['kind'] for f in fs)
        self.assertEqual(kinds, ['collect', 'names5', 'rec'])


class Inputs(unittest.TestCase):
    def test_lang_names(self):
        rules = [{'variant': 'language', 'language': 'ja', 'value': '東京タワー'}, {'variant': 'language', 'language': 'en', 'value': 'Tokyo Tower'},
                 {'variant': 'short', 'language': 'en', 'value': 'TT'}, {'variant': 'language', 'language': 'zh', 'value': '东京塔'}]
        self.assertEqual(R.lang_names(rules), {'ja': '東京タワー', 'en': 'Tokyo Tower', 'zh_Hans': '东京塔'})

    def test_overture_collector_rule_names(self):
        import collectors as C
        self.assertEqual(C._rule_names([{'variant': 'language', 'language': 'ko', 'value': '남산타워'}]), {'ko': '남산타워'})
        src = open(os.path.join(ROOT, 'scripts', 'collect', 'collectors.py'), encoding='utf-8').read()
        self.assertNotIn('categories.primary', src)  # 2026-10 릴리스에 없는 칸(0행 원인)

    def test_wd_site_stats(self):
        import wd_dump as WD
        self.assertEqual(WD.site_stats({'sitelinks': {'enwiki': {}, 'kowiki': {}, 'commonswiki': {}, 'enwikivoyage': {}}}), (4, True))
        self.assertEqual(WD.site_stats({'sitelinks': {'commonswiki': {}}}), (1, False))

    def test_mention_share(self):
        pat = {'groups': {'cc': {'kr': {'all': {}, 'food': {'stops_per_day': {'n': 120}}, 'museum': {'stops_per_day': {'n': 60}}, 'lodging': {'x': {'n': 5}}}}}}
        self.assertEqual(R.mention_share(pat), {'KR': {'food': 1.0, 'sight': 0.5, 'lodging': 0.042}})
        self.assertEqual(R.mention_share({'groups': {'cc': {'jp': {'food': {'a': {'n': 10}}}}}}), {})

    def test_doc_matches_code(self):
        doc = open(os.path.join(ROOT, 'docs', 'RECOMMEND_INDEX.md'), encoding='utf-8').read()
        self.assertIn(R.VERSION, doc)
        for k, w in R.W.items():
            self.assertIn(f'`{k}` | {w}', doc)
        self.assertNotIn('방문자 평균', doc.replace("'방문자 평균'이라는 말은 쓰지 않는다", ''))


if __name__ == '__main__':
    unittest.main()
