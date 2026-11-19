"""Commons SDC 덤프 판정(scripts/commons_dump.py) · Wikidata 덤프 도시 항목(scripts/wd_dump.py) — 네트워크 없이. (2026-11-20, 270기)"""
import json, os, sys, unittest

ROOT = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, os.path.join(ROOT, 'scripts')); sys.path.insert(0, os.path.join(ROOT, 'scripts', 'collect'))
import commons_dump as CD  # noqa: E402
import wd_dump as WD  # noqa: E402


def mi(title, lic, an=None, un=None):
    q = {}
    if an:
        q['P2093'] = [{'snaktype': 'value', 'datavalue': {'value': an, 'type': 'string'}}]
    if un:
        q['P4174'] = [{'snaktype': 'value', 'datavalue': {'value': un, 'type': 'string'}}]
    st = {'P275': [{'mainsnak': {'datavalue': {'value': {'id': lic}}}, 'rank': 'normal'}], 'P170': [{'mainsnak': {'snaktype': 'somevalue'}, 'qualifiers': q}]}
    return json.dumps({'type': 'mediainfo', 'id': 'M1', 'statements': st, 'title': 'File:' + title}).encode() + b','


class T(unittest.TestCase):
    def test_url_same_as_p18(self):
        self.assertEqual(CD.url_of('Tokyo Tower (2020).jpg'), WD.FILEPATH + 'Tokyo%20Tower%20%282020%29.jpg')

    def test_sink_judges_only_wanted(self):
        want = {CD.url_of('A b.jpg'), CD.url_of('NC.jpg'), CD.url_of('Anon.jpg')}
        s = CD.Sink(want, '2026-11-17', False)
        s.feed(mi('A b.jpg', 'Q18199165', an='Kim') + b'\n' + mi('Other.jpg', 'Q18199165', an='X') + b'\n' + mi('NC.jpg', 'Q99999', an='Y') + b'\n'
               + mi('Anon.jpg', 'Q6938433') + b'\n')
        self.assertEqual(s.out[CD.url_of('A b.jpg')], ['2026-11-17', {'lic': 'Q18199165', 'by': 'Kim'}])
        self.assertEqual(s.out[CD.url_of('NC.jpg')][1], {})    # 비자유 라이선스 → 싣지 않음
        self.assertEqual(s.out[CD.url_of('Anon.jpg')][1], {})  # 저작자 없음 → 싣지 않음
        self.assertNotIn(CD.url_of('Other.jpg'), s.out)

    def test_city_hit(self):
        WD.city_setup([{'key': 'tokyo', 'bbox': [139.0, 35.5, 139.95, 35.9]}, {'key': 'paris', 'bbox': [2.1, 48.7, 2.6, 49.0]}])

        def ent(q, name, la, lo, sl):
            return json.dumps({'type': 'item', 'id': q, 'labels': {'en': {'language': 'en', 'value': name}}, 'sitelinks': {f's{i}': {} for i in range(sl)},
                               'claims': {'P625': [{'mainsnak': {'datavalue': {'value': {'latitude': la, 'longitude': lo}}}, 'rank': 'normal'}],
                                          'P18': [{'mainsnak': {'datavalue': {'value': name + '.jpg'}}, 'rank': 'normal'}]}}).encode() + b','
        L = WD.Lines(set(), '2026-11-17', False, set())
        L.feed(ent('Q1490', 'Tokyo', 35.68, 139.76, 300) + b'\n' + ent('Q7', 'Tokyo', 35.68, 139.76, 5) + b'\n'
               + ent('Q8', 'Paris', 33.66, -95.55, 900) + b'\n' + ent('Q90', 'Paris', 48.85, 2.35, 400) + b'\n')
        c = L.out['_city']
        self.assertEqual(c['tokyo'][:2], [300, 'Q1490']); self.assertEqual(c['paris'][1], 'Q90')  # 상자 밖 Paris(텍사스) 는 버림
        self.assertEqual(c['paris'][2], WD.FILEPATH + 'Paris.jpg')


    def test_tile_cov_counts_non_qid_rows(self):
        import photo_pri as PP
        d = {'id': ['fsq:a', 'h:b', 'Q5', 'fsq:out'], '_lat': [33.4, 33.41, 33.42, 40.0], '_lon': [126.5, 126.5, 126.5, 126.5], 's': [1, 9, 5, 99]}
        ids = PP.city_tile_ids([d, d], [126.14, 33.19, 126.98, 33.58])
        self.assertEqual(ids, ['h:b', 'Q5', 'fsq:a'])  # 점수 순 · 상자 밖 제외 · 중복 없음
        self.assertEqual(PP.tile_cov({'jeju': ids}, {'h:b': {}, 'x': {}}), {'jeju': [3, 1, 1]})
        L = PP.report_lines({'p18_asked': 0, 'p18_left': 0, 'p18_stop': None, 'commons_asked': 0, 'commons_left': 0, 'commons_stop': None, 'rows': 1,
                             'cities': 0, 'unsplash': 0, 'by_city': {}, 'tile_cov': {'jeju': [3, 1, 1]}}, [{'rank': 1, 'key': 'jeju'}])
        self.assertIn('| 1 | jeju | 0 | 0 | 3 | 1 | 33.3% | 1/3 |', L)

    def test_city_items_skip_qlever(self):
        import photo_pri as PP
        cs = {}
        rows, files = PP.city_rows([{'key': 'paris', 'bbox': [2.1, 48.7, 2.6, 49.0]}], cs, {}, None, 0, items={'paris': {'q': 'Q90', 'p': WD.FILEPATH + 'P.jpg'}}, now_day='2026-11-20')
        self.assertEqual((cs['paris']['q'], files), ('Q90', [WD.FILEPATH + 'P.jpg']))  # 판정 전 → 판정할 파일로
        rows, _ = PP.city_rows([{'key': 'paris', 'bbox': [2.1, 48.7, 2.6, 49.0]}], cs, {WD.FILEPATH + 'P.jpg': ['2026-11-17', {'lic': 'Q18199165', 'by': 'A'}]}, None, 0,
                               items={'paris': {'q': 'Q90', 'p': WD.FILEPATH + 'P.jpg'}}, now_day='2026-11-20')
        self.assertEqual(rows['city:paris']['photo_license'], 'CC BY-SA 4.0')


if __name__ == '__main__':
    unittest.main()
