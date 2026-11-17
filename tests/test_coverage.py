"""커버리지 측정(scripts/coverage.py) 시험 — 종류 가르기·속성 채움·도시 상자·분모(합집합 범위)·보고서 글."""
import gzip, json, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, os.path.join(ROOT, 'scripts')); sys.path.insert(0, os.path.join(ROOT, 'scripts', 'collect'))
import coverage as V  # noqa: E402
import common as K  # noqa: E402
import process as P  # noqa: E402


def m(i, cat, lat, lon, **kw):
    return {'id': f'aa:{i}', 'nm': f'곳 {i}', 'lat': lat, 'lon': lon, 'c': cat, 'src': 'aa', 'srcs': kw.pop('srcs', ['aa']), 'tr': kw.pop('tr', 1), 'lic': 'CC0', **kw}


ROWS = [m(1, 'museum', 37.55, 126.98, ko='박물관', en='Museum', h='Mo-Su 09:00-18:00', url='https://example.org', tr=2, srcs=['aa', 'bb']),
        m(2, 'sushirestaurant', 37.56, 126.99, tel='+82-2-000-0000'),
        m(3, 'hotel', 35.10, 129.04, en='Hotel', wid='w9'),
        m(4, 'busstop', 35.11, 129.05),
        m(5, 'parking', 33.0, 124.0)]


def write(d, name, obj):
    open(os.path.join(d, name), 'wb').write(gzip.compress(json.dumps(obj).encode()))


class Coverage(unittest.TestCase):
    def test_type_of(self):
        want = {'museum': 'sight', 'sight': 'sight', 'park': 'sight', 'parking': 'other', 'food': 'food', 'cafe': 'food', 'bar': 'food', 'winebar': 'food',
                'coffeeshop': 'food', 'lodging': 'lodging', 'hotel': 'lodging', 'transport': 'transport', 'trainstation': 'transport', 'gasstation': 'other',
                'shopping': 'other', '': 'other', None: 'other', 'ramenrestaurant': 'food', 'hostel': 'lodging', 'airport': 'transport'}
        self.assertEqual({k: V.type_of(k) for k in want}, want)

    def test_measure_dir(self):
        d = tempfile.mkdtemp()
        write(d, 'col_KR_0.json.gz', P.to_layer('KR', ROWS))
        write(d, 'hours_x.json.gz', {'schema': 'hours-1', 'rows': {'aa:2': {'oh': 'Mo-Su 11:00-22:00'}, 'aa:4': {}}})
        write(d, 'photos_x.json.gz', {'schema': 'photos-1', 'rows': {'w9': {'photo': 'x.jpg'}}})
        cities = {'seoul': [126.76, 37.41, 127.19, 37.71], 'busan': [128.85, 35.03, 129.31, 35.33]}
        r = V.measure_dir(d, cities)
        self.assertEqual(r['places']['n'], 5)
        self.assertEqual({t: b['n'] for t, b in r['by_type'].items()}, {'sight': 1, 'food': 1, 'lodging': 1, 'transport': 1, 'other': 1})
        self.assertEqual((r['places']['h'], r['places']['photo'], r['places']['tel'], r['places']['url'], r['places']['multi']), (2, 1, 1, 1, 1))  # 영업시간: 제 칸 1 + 층 1, 사진: wid 로 맞춤
        self.assertEqual((r['places']['ko'], r['places']['en']), (1, 2))
        self.assertEqual({c: b['n'] for c, b in r['by_city'].items()}, {'seoul': 2, 'busan': 2})
        self.assertEqual(r['by_cc_type']['KR']['lodging'], 1); self.assertEqual(r['by_source'], {'aa': 5, 'bb': 1})

    def test_measure_store_matches_dir(self):
        d = tempfile.mkdtemp(); s = K.Store(os.path.join(d, 's'))
        s.put_json('processed/out/r1/col_KR_0.json.gz', P.to_layer('KR', ROWS[:3]))
        s.put_json('processed/out/r0/col_JP_0.json.gz', P.to_layer('JP', ROWS[3:]))
        s.put_json('processed/out/r1/col_info_holidays.json.gz', {'schema': 'collect-1', 'dataset': 'holidays', 'rows': {'a': {}}})
        s.put_json('processed/latest.json', {'run': 'r1', 'files': ['col_KR_0.json.gz', 'col_JP_0.json.gz', 'col_info_holidays.json.gz'],
                                             'paths': {'col_KR_0.json.gz': 'processed/out/r1/col_KR_0.json.gz', 'col_JP_0.json.gz': 'processed/out/r0/col_JP_0.json.gz',
                                                       'col_info_holidays.json.gz': 'processed/out/r1/col_info_holidays.json.gz'}, 'stats': {'pending': {'raw_files': 3, 'countries': ['US']}}})
        r = V.measure_store(s, {}, batch=1)
        self.assertEqual((r['places']['n'], sorted(r['by_cc']), r['layer_files'], r['pending']['raw_files']), (5, ['JP', 'KR'], 2, 3))
        self.assertIsNone(V.measure_store(K.Store(os.path.join(d, 'empty')), {}))

    def test_gap_and_report(self):
        status = {'updated': 'x', 'sources': {'fsq_os_places': {'shards': 149, 'rows_last': 90, 'failing': 0}, 'overture_places': {'shards': 2, 'rows_last': 10, 'failing': 1}}}
        tg = {'sources': [{'id': 'overture_places', 'count': 100, 'layer': 'main'}, {'id': 'fsq_os_places', 'count': None, 'layer': 'main'},
                          {'id': 'osm', 'count': 999, 'layer': 'share-alike 분리'}], 'google_reference': {'count': 400}}
        rep = V.build(status, None, tg, date='2026-11-17')
        g = rep['gap']
        self.assertEqual((g['union_low'], g['union_high'], g['raw_rows'], g['raw_vs_union_high_pct']), (100, 190, 100, 52.6))  # 총수 미확인 출처는 받은 행으로, 분리 층은 분모에서 뺌
        self.assertIsNone(g['published'])
        self.assertEqual(rep['collect']['fsq_os_places']['shards_total'], 229)
        md = V.to_md(rep)
        self.assertIn('미측정', md); self.assertIn('| overture_places | 2/229 | 10 | 100 | 10.0% | 1 |', md)
        lay = V.Meter({}); lay.add_layer(P.to_layer('KR', ROWS))
        rep2 = V.build(status, lay.result(), tg, date='2026-11-18')
        self.assertEqual((rep2['gap']['published_vs_union_low_pct'], rep2['gap']['published_vs_google_ref_pct']), (5.0, 1.2))
        prev = {'layers': {'places': {'n': 3}}}
        md2 = V.to_md(rep2, prev)
        self.assertIn('어제보다 +2', md2); self.assertIn('비교 불가', md2); self.assertIn('| 명소 | 1 |', md2)

    def test_targets_file(self):
        t = json.load(open(os.path.join(ROOT, 'sources', 'coverage_targets.json'), encoding='utf-8'))
        for s in t['sources']:
            self.assertTrue(s['url'].startswith('https://')); self.assertIn('license', s)
            if s['count'] is not None:
                self.assertTrue(s.get('as_of') and s.get('quote'), s['id'])  # 숫자는 날짜·원문과 함께
            if any(x in s['license'] for x in ('ODbL', 'SA')):
                self.assertNotEqual(s['layer'], 'main', s['id'])  # share-alike 는 분리 층
        self.assertTrue(t['google_reference']['url'].startswith('https://mapsplatform.google.com/'))  # 공개 문구 인용만
        c = json.load(open(os.path.join(ROOT, 'sources', 'coverage_cities.json'), encoding='utf-8'))['cities']
        self.assertTrue(all(b[0] < b[2] and b[1] < b[3] and -180 <= b[0] and b[2] <= 180 for b in c.values())); self.assertGreaterEqual(len(c), 30)

    def test_workflow_keeps_data_out_of_github(self):
        txt = open(os.path.join(ROOT, '.github', 'workflows', 'coverage-report.yml'), encoding='utf-8').read()
        self.assertNotIn('upload-artifact', txt); self.assertNotIn('actions/cache', txt); self.assertNotIn('pull_request_target', txt)
        self.assertIn('issues: write', txt); self.assertIn("github.repository == 'joon0515/triproutie-pipeline'", txt)


if __name__ == '__main__':
    unittest.main()
