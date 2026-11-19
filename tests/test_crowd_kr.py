"""한국 혼잡 예측 층(scripts/crowd_kr.py) — 네트워크 없이. (2026-11-20, 262기)"""
import gzip, json, os, sys, tempfile, unittest, urllib.parse
ROOT = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import crowd_kr as C  # noqa: E402
import publish_layers as P  # noqa: E402


class Crowd(unittest.TestCase):
    def test_targets_from_kto_inside_priority_city(self):
        d = tempfile.mkdtemp()
        rows = {'kto:1': {'n': '해운대해수욕장', 'sg': '350', 'lat': 35.158, 'lon': 129.16}, 'kto:2': {'n': '먼 곳', 'sg': '999', 'lat': 37.9, 'lon': 128.9},
                'kto:3': {'n': '좌표 없음', 'sg': '110'}}
        with gzip.open(os.path.join(d, 'kto_26.json.gz'), 'wt') as f:
            json.dump({'schema': 'tourapi-1', 'area': '26', 'rows': rows}, f)
        cities = [{'rank': 1, 'key': 'busan', 'bbox': [128.8, 35.0, 129.3, 35.4]}]
        self.assertEqual(C.sigungu_targets(d, cities), [('26', '26350')])
        self.assertTrue(any(c['key'] == 'seoul' for c in C.kr_cities()))
        self.assertFalse(any(c['key'] == 'tokyo' for c in C.kr_cities()))

    def test_build_rows_and_key_not_logged(self):
        seen = []

        def opener(u):
            seen.append(u)
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)
            self.assertEqual(q['areaCd'], ['26']); self.assertEqual(q['signguCd'], ['26350'])
            return json.dumps({'response': {'body': {'items': {'item': [
                {'tAtsNm': '해운대 해수욕장', 'baseYmd': '20261121', 'cnctrRate': '72.5'},
                {'tAtsNm': '해운대 해수욕장', 'baseYmd': '20261120', 'cnctrRate': '35'},
                {'tAtsNm': '이상한 값', 'baseYmd': '2026', 'cnctrRate': '5'},
                {'tAtsNm': '범위 밖', 'baseYmd': '20261120', 'cnctrRate': '500'}]}}}}).encode()
        rows, asked, why = C.build(C.Client('SECRET', opener=opener, sleep=lambda s: None), [('26', '26350')])
        self.assertEqual((asked, why), (1, None))
        self.assertEqual(rows, {'해운대해수욕장': [[20261120, 35.0], [20261121, 72.5]]})
        self.assertNotIn('SECRET', json.dumps(C.doc(rows, '2026-11-20')))

    def test_quota_stops(self):
        def opener(u):
            import urllib.error
            raise urllib.error.HTTPError(u, 429, 'x', {}, None)
        rows, asked, why = C.build(C.Client('K', opener=opener, sleep=lambda s: None), [('11', '11110'), ('11', '11140')])
        self.assertEqual((rows, asked, why), ({}, 0, 'HTTP 429'))

    def test_publish_knows_crowd(self):
        self.assertEqual(P.OK_SCHEMA['crowd-1'], 'crowd')
        self.assertIn('crowd', P.LEGACY_KINDS)


if __name__ == '__main__':
    unittest.main()
