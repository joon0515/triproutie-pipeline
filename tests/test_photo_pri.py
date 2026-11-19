"""사진 우선 층(scripts/photo_pri.py) · TourAPI https 바꾸기(tools/photo_layer.py) — 네트워크 없이. (2026-11-20, 262기)"""
import gzip, json, os, sys, tempfile, unittest, urllib.parse

ROOT = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, os.path.join(ROOT, 'scripts')); sys.path.insert(0, os.path.join(ROOT, 'scripts', 'collect')); sys.path.insert(0, os.path.join(ROOT, 'tools'))
import photo_pri as PP  # noqa: E402
import photo_layer as PL  # noqa: E402
from common import Store  # noqa: E402

FP = 'http://commons.wikimedia.org/wiki/Special:FilePath/'


def pack(ids, pts, s):
    la = lo = 0
    dla, dlo = [], []
    for a, b in pts:
        A, B = round(a * 1e5), round(b * 1e5)
        dla.append(A - la); dlo.append(B - lo); la, lo = A, B
    return {'n': len(ids), 'id': ids, 'dla': dla, 'dlo': dlo, 's': s}


class FakeHttp:
    """QLever 두 끝점 흉내. 묻은 주소를 남긴다."""

    def __init__(self, p18=None, lic=None, city=None):
        self.p18, self.lic, self.city, self.urls = p18 or {}, lic or {}, city, []

    def get(self, url, accept=None):
        self.urls.append(url)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)['query'][0]
        if 'wikimedia-commons' in url:
            b = []
            for u, (lic, an) in self.lic.items():
                if '<' + u + '>' in q:
                    b.append({'u': {'value': u}, 'lic': {'value': 'http://www.wikidata.org/entity/' + lic}, 'an': {'value': an}})
            return json.dumps({'results': {'bindings': b}}).encode()
        if 'rdfs:label' in q:
            return json.dumps(self.city or {'results': {'bindings': []}}).encode()
        b = [{'q': {'value': 'http://www.wikidata.org/entity/' + k}, 'p': {'value': v}} for k, v in self.p18.items() if 'wd:' + k + ' ' in q + ' ']
        return json.dumps({'results': {'bindings': b}}).encode()


class PhotoPri(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tiles = os.path.join(self.tmp, 'tiles'); os.makedirs(self.tiles)
        # 오사카 상자 안 3곳(점수 다름) + 상자 밖 1곳 + QID 아닌 행 1곳
        d = pack(['Q1', 'Q2', 'Q3', 'Q9', 'fsq:abc'], [(34.69, 135.50), (34.70, 135.51), (34.66, 135.43), (35.0, 136.5), (34.68, 135.50)], [10, 90, 50, 99, 80])
        with gzip.open(os.path.join(self.tiles, 'w-osaka_JP.json.gz'), 'wt') as f:
            json.dump(d, f)
        self.store = Store(os.path.join(self.tmp, 'store'))

    def test_order_city_rank_then_score_inside_box(self):
        cities = [{'rank': 1, 'key': 'osaka', 'bbox': [135.35, 34.55, 135.65, 34.8]}]
        _, tf = PP.local_tiles_for(self.tiles)
        pr = PP.prio_order(cities, tf)
        self.assertEqual(pr, [('osaka', ['Q2', 'Q3', 'Q1'])])  # 점수 높은 순, 상자 밖 Q9·QID 아닌 행 빠짐

    def test_prefixed_tile_ids_kowiki_Q(self):  # 263기: 실제 앱 타일 행 id 는 'kowiki:Q123' — 맨몸 Q 만 보면 일정 후보 0(사진 0% 의 둘째 원인)
        d = pack(['kowiki:Q7', 'fsq:abc', 'Q8'], [(34.69, 135.50), (34.68, 135.50), (34.70, 135.51)], [90, 99, 10])
        tmp = os.path.join(self.tmp, 't2'); os.makedirs(tmp)
        with gzip.open(os.path.join(tmp, 'w-x_JP.json.gz'), 'wt') as f:
            json.dump(d, f)
        _, tf = PP.local_tiles_for(tmp)
        keys = {}
        pr = PP.prio_order([{'rank': 1, 'key': 'osaka', 'bbox': [135.35, 34.55, 135.65, 34.8]}], tf, keys)
        self.assertEqual(pr, [('osaka', ['Q7', 'Q8'])])
        self.assertEqual(keys, {'Q7': {'kowiki:Q7'}})  # 사진 행은 앱 타일 행 id 로도 싣는다

    def test_p18_from_dump_first_then_endpoint(self):
        self.store.put_json('processed/desc/wd_dump.json.gz', {'Q2': ['2026-11-11', {}, {'l': {}, 'p': FP + 'A.jpg'}]})
        h = FakeHttp(p18={'Q3': FP + 'B.jpg'}, lic={FP + 'A.jpg': ('Q18199165', 'Kim'), FP + 'B.jpg': ('Q6938433', 'Lee')})
        out = os.path.join(self.tmp, 'out')
        rep, made = PP.run(self.store, tiles_dir=self.tiles, out=out, http_wd=h, http_c=h, budget_min=5)
        asked = [u for u in h.urls if 'api/wikidata' in u and 'rdfs:label' not in urllib.parse.unquote_plus(u)]
        q = urllib.parse.unquote_plus(asked[0])
        self.assertNotIn('wd:Q2 ', q)  # 덤프에 있는 것은 다시 묻지 않음
        self.assertIn('wd:Q3', q)
        allrows = {}
        for f in made:
            doc = json.loads(gzip.decompress(open(os.path.join(out, f), 'rb').read()))
            self.assertEqual(doc['schema'], 'photos-1')
            allrows.update(doc['rows'])
        self.assertEqual(set(allrows), {'Q2', 'Q3'})
        r = allrows['Q2']
        self.assertEqual(r['photo_license'], 'CC BY-SA 4.0'); self.assertEqual(r['photo_author'], 'Kim')
        self.assertTrue(r['photo_thumb'].startswith('https://commons.wikimedia.org/'))
        self.assertTrue(r['photo_page'].startswith('https://commons.wikimedia.org/wiki/File:'))
        self.assertEqual(rep['by_city']['osaka'], (3, 2))
        # 판정 캐시는 place_names 가 같이 읽는 곳에
        self.assertIn(FP + 'A.jpg', self.store.get_json('processed/photos/commons.json.gz'))

    def test_no_author_or_bad_license_not_loaded(self):
        self.store.put_json('processed/desc/wd.json.gz', {'Q2': ['2026-11-11', {}, {'l': {}, 'p': FP + 'A.jpg'}], 'Q3': ['2026-11-11', {}, {'l': {}, 'p': FP + 'B.jpg'}]})
        h = FakeHttp(lic={FP + 'A.jpg': ('Q999', 'Kim'), FP + 'B.jpg': ('Q18199165', '')})
        rep, made = PP.run(self.store, tiles_dir=self.tiles, out=os.path.join(self.tmp, 'o'), http_wd=h, http_c=h, budget_min=5)
        self.assertEqual(rep['rows'], 0)

    def test_unsplash_row_hotlink_credit_utm_and_download_ping(self):
        calls = []

        def opener(req):
            calls.append(req.full_url)
            self.assertTrue(req.get_header('Authorization').startswith('Client-ID '))
            if '/search/photos' in req.full_url:
                return {'results': [{'id': 'abc', 'urls': {'regular': 'https://images.unsplash.com/photo-1?w=1080', 'small': 'https://images.unsplash.com/photo-1?w=400'},
                                     'user': {'name': 'Jane Doe', 'username': 'jd', 'links': {'html': 'https://unsplash.com/@jd'}},
                                     'links': {'html': 'https://unsplash.com/photos/abc', 'download_location': 'https://api.unsplash.com/photos/abc/download?ixid=1'}}]}
            return {'url': 'x'}
        row, why = PP.unsplash_pick('Osaka', 'KEY', opener)
        self.assertIsNone(why)
        self.assertEqual(row['photo_url'], 'https://images.unsplash.com/photo-1?w=1080')  # API 주소 그대로(핫링크)
        self.assertEqual(row['photo_author_url'], 'https://unsplash.com/@jd?utm_source=triproutie&utm_medium=referral')
        self.assertEqual(row['photo_page'], 'https://unsplash.com/photos/abc?utm_source=triproutie&utm_medium=referral')
        self.assertEqual(row['photo_license'], 'Unsplash License')
        self.assertEqual(sum('/download' in c for c in calls), 1)
        self.assertNotIn('KEY', json.dumps(row))

    def test_city_rows_commons_without_key_and_unsplash_with_key(self):
        cities = [{'rank': 1, 'key': 'osaka', 'bbox': [135.35, 34.55, 135.65, 34.8]}]
        city = {'results': {'bindings': [
            {'c': {'value': 'http://www.wikidata.org/entity/Q35765'}, 'p': {'value': FP + 'Osaka.jpg'}, 'co': {'value': 'Point(135.50 34.69)'}, 'sl': {'value': '200'}},
            {'c': {'value': 'http://www.wikidata.org/entity/Q777'}, 'p': {'value': FP + 'Far.jpg'}, 'co': {'value': 'Point(-80 30)'}, 'sl': {'value': '500'}}]}}
        h = FakeHttp(city=city)
        cst = {}
        rows, files = PP.city_rows(cities, cst, {}, h, 1e18)
        self.assertEqual(files, [FP + 'Osaka.jpg'])  # 상자 밖 항목은 사이트 링크가 많아도 버림
        rows, _ = PP.city_rows(cities, cst, {FP + 'Osaka.jpg': ['2026-11-20', {'lic': 'Q6938433', 'by': 'Park'}]}, None, 0)
        self.assertEqual(rows['city:osaka']['photo_license'], 'CC0 1.0')
        ph = {'results': [{'id': 'z', 'urls': {'regular': 'https://images.unsplash.com/z'}, 'user': {'name': 'A', 'links': {'html': 'https://unsplash.com/@a'}},
                           'links': {'html': 'https://unsplash.com/photos/z', 'download_location': 'https://api.unsplash.com/photos/z/download'}}]}
        rows, _ = PP.city_rows(cities, {}, {}, None, 1e18, key='K', opener=lambda r: ph if 'search' in r.full_url else {})
        self.assertEqual(rows['city:osaka']['photo_src'], 'unsplash')

    def test_unsplash_bad_rows_skipped(self):
        self.assertIsNone(PP.unsplash_row({'urls': {'regular': 'http://x'}, 'user': {'name': 'a', 'links': {'html': 'https://u'}}, 'links': {'html': 'https://p'}}))
        self.assertIsNone(PP.unsplash_row({}))

    def test_kto_https_and_license_url(self):
        self.assertEqual(PL.kto_https('http://tong.visitkorea.or.kr/cms/resource/1.jpg'), 'https://tong.visitkorea.or.kr/cms/resource/1.jpg')
        self.assertEqual(PL.kto_https('http://other.example/1.jpg'), 'http://other.example/1.jpg')
        grid = {(int(37.5 * 200), int(127.0 * 200)): [(37.5, 127.0, PL.nrm('경복궁'), {'url': 'http://tong.visitkorea.or.kr/a.jpg', 'license': 'KOGL-3'})]}
        d = {'_lat': [37.5], '_lon': [127.0], 'nm': ['경복궁'], 'ko': [None]}
        v = PL.from_kto(d, grid, 0)
        self.assertEqual(v['photo_url'], 'https://tong.visitkorea.or.kr/a.jpg')
        self.assertTrue(v['no_modify'])
        self.assertEqual(v['photo_license_url'], 'https://www.kogl.or.kr/info/licenseType3.do')
        grid2 = {k: [(a, b, n, dict(p, url='http://other.example/a.jpg'))] for k, [(a, b, n, p)] in grid.items()}
        self.assertIsNone(PL.from_kto(d, grid2, 0))  # https 로 바꿀 수 없는 주소는 싣지 않음

    def test_prio_map_from_place_desc_keys_rows_by_wid(self):
        import place_desc as PD
        S = PD.Seeds()
        S.cities = [{'rank': 1, 'key': 'osaka', 'bbox': [135.35, 34.55, 135.65, 34.8]}]
        S.city_q = {'osaka': {}}
        S.add_col({'schema': 'collect-1', 'dataset': 'places', 'id': ['c1', 'c2'], 'wid': ['w1', None], 'wd': ['Q2', 'Q9'], 'nm': ['a', 'b'],
                   'dla': [3469000, 100000], 'dlo': [13550000, 100000]})
        self.assertEqual(S.city_q['osaka'], {'Q2': {'c1', 'w1'}})  # 두 번째 행은 상자 밖(35.69, 136.5)
        self.store.put_json('processed/photos/prio_map.json.gz', {'built': 'x', 'cities': [['osaka', [['Q2', ['c1', 'w1']]]]]})
        self.store.put_json('processed/desc/wd.json.gz', {'Q2': ['2026-11-11', {}, {'l': {}, 'p': FP + 'A.jpg'}]})
        h = FakeHttp(lic={FP + 'A.jpg': ('Q18199165', 'Kim')})
        # 시험 모드(tiles_dir)는 지도를 안 쓰므로 gs 경로 대신 local_tiles_for 를 바꿔 끼운다
        orig = PP.gcs_tiles_for
        PP.gcs_tiles_for = lambda b, wd: ('v1', lambda bb: [])
        try:
            rep, made = PP.run(self.store, bucket=None, out=os.path.join(self.tmp, 'o2'), http_wd=h, http_c=h, budget_min=5, dry_run=True)
        finally:
            PP.gcs_tiles_for = orig
        got = set()
        for f in made:
            got |= set(json.loads(gzip.decompress(open(os.path.join(self.tmp, 'o2', f), 'rb').read()))['rows'])
        self.assertEqual(got, {'Q2', 'c1', 'w1'})
        self.assertEqual(rep['by_city']['osaka'], (1, 1))

    def test_tile_box_from_center(self):
        self.assertEqual(PP.tile_box({'bbox': [1, 2, 3, 4]}), [1, 2, 3, 4])
        self.assertEqual(PP.tile_box({'center': [34.7, 135.5], 'res': 5}), [135.3, 34.5, 135.7, 34.900000000000006])
        self.assertIsNone(PP.tile_box({}))

    def test_tile_box_from_real_manifest_entry(self):  # 263기: 실제 항목 모양 {file, tile, cc, h3r5:[셀 id], n, size, sha256} — bbox·center 없음 → 사진 0% 의 원인
        import h3
        cell = h3.latlng_to_cell(34.69, 135.50, 5)
        bb = PP.tile_box({'file': 'w-x_JP.json.gz', 'cc': 'JP', 'h3r5': [cell], 'n': 5})
        self.assertTrue(bb and PP.overlap(bb, [135.49, 34.68, 135.51, 34.70]))
        self.assertIsNone(PP.tile_box({'file': 'w-x', 'h3r5': []}))

    def test_no_banned_hosts(self):
        src = open(os.path.join(ROOT, 'scripts', 'photo_pri.py'), encoding='utf-8').read().lower()
        for h in ('instagram', 'naver', 'kakao', 'google.', 'tabelog', 'tripadvisor', 'youtube', 'tiktok'):
            self.assertNotIn(h, src, h)


if __name__ == '__main__':
    unittest.main()
