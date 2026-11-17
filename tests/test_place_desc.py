"""장소 소개 층(place_desc.py · desc_osm.py · TourAPI overview · 대만 dsc · coverage 7절) 시험 — 네트워크·키 없음.
고정 시험: gen_fix3 표본 47곳(tests/desc_sample47.json, QID 와 Wikidata 설명만)."""
import gzip, json, os, re, sys, tempfile, unittest, urllib.parse, io, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
for p in ('scripts', 'scripts/collect', 'tools'):
    sys.path.insert(0, os.path.join(ROOT, p))
import place_desc as PD  # noqa: E402
import desc_osm as DO  # noqa: E402
import publish_layers as PL  # noqa: E402
import coverage as CV  # noqa: E402
import process as P  # noqa: E402
import collectors as C  # noqa: E402
from common import Store, forbidden  # noqa: E402

SAMPLE = json.load(open(os.path.join(HERE, 'desc_sample47.json'), encoding='utf-8'))['rows']
SLOT_TAGS = {'ko': {'ko'}, 'en': {'en'}, 'ja': {'ja'}, 'zh_Hans': {'zh-hans', 'zh-cn', 'zh'}, 'zh_Hant': {'zh-hant', 'zh-tw', 'zh'}}


def gz(path, obj):
    with open(path, 'wb') as f:
        f.write(gzip.compress(json.dumps(obj, ensure_ascii=False).encode()))


class FakeHttp:
    """QLever 흉내: 주소의 VALUES 에서 QID 를 읽어 desc(표본) → SPARQL JSON."""

    def __init__(self, data):
        self.data, self.calls, self.asked = data, 0, []

    def get(self, url, accept=None):
        assert url.startswith('https://qlever.dev/') and accept == 'application/sparql-results+json'
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)['query'][0]
        qs = re.findall(r'wd:(Q\d+)', q.split('VALUES')[1].split('}')[0])
        assert 'Q4167410' in q and len(qs) <= PD.BATCH
        self.calls += 1; self.asked += qs
        b = []
        for x in qs:
            for k, kind in (('d', 'desc'), ('l', 'label')):
                for t, v in (self.data.get(x, {}).get(kind) or {}).items():
                    b.append({'item': {'type': 'uri', 'value': 'http://www.wikidata.org/entity/' + x}, 'k': {'value': k}, 'v': {'value': v, 'xml:lang': t}})
        return json.dumps({'results': {'bindings': b}}).encode()


class TestSample47(unittest.TestCase):
    def test_fixed_sample_counts_and_no_substitution(self):
        n = {k: 0 for k in PD.SLOTS}
        for r in SAMPLE:
            sl = PD.pick_slots(r['desc'])
            for k, v in sl.items():
                if v:
                    n[k] += 1
                    self.assertIn(v, {r['desc'][t] for t in SLOT_TAGS[k] if t in r['desc']}, (r['qid'], k))  # 그 언어 원문 그대로
                    self.assertNotIn('추정', v)
        self.assertEqual(sum(1 for r in SAMPLE if r['qid']), 17)
        self.assertEqual(n, {'ko': 9, 'en': 16, 'ja': 14, 'zh_Hans': 5, 'zh_Hant': 4})  # en 'zoo'(3자)는 버림
        food = [r for r in SAMPLE if r['kind'] in ('맛집', '카페')]
        self.assertLessEqual(sum(1 for r in food if PD.pick_slots(r['desc'])['ko']), 1)


class TestText(unittest.TestCase):
    def test_script_and_generic_zh(self):
        self.assertEqual(PD.han_script('位于日本东京都港区的电波塔'), 'Hans')
        self.assertEqual(PD.han_script('位於日本東京都中央區的商業區'), 'Hant')
        self.assertEqual(PD.han_script('日本神社'), 'neutral')
        self.assertEqual(PD.pick_slots({'zh': '位於日本東京都中央區的商業區'}), {'ko': None, 'en': None, 'ja': None, 'zh_Hans': None, 'zh_Hant': '位於日本東京都中央區的商業區'})
        self.assertIsNone(PD.pick_slots({'zh': '东京都の區'})['zh_Hans'])  # 가나 섞임
        self.assertIsNone(PD.pick_slots({'zh-hant': '位于日本东京都港区的电波塔'})['zh_Hant'])  # 꼬리표와 글자가 어긋남
        sl = PD.pick_slots({'en': 'tower in Tokyo, Japan'})
        self.assertEqual([k for k, v in sl.items() if v], ['en'])  # 없는 언어는 비움

    def test_filters(self):
        bad = ['zoo', '서울에 있는 것으로 추정되는 고분', 'Wikimedia disambiguation page', '위키미디어 분류', 'see https://example.org', 'estimated 3 km trail', '約3kmの遊歩道', 'Ã©cole de musique']
        for s in bad:
            self.assertIsNone(PD.ok_text(s), s)
        self.assertIsNone(PD.ok_text('Tokyo Tower', ['tokyo tower']))
        self.assertIsNone(PD.pick_slots({'ko': '도쿄 타워'}, {'ko': '도쿄 타워'})['ko'])  # 라벨과 같음
        self.assertEqual(PD.ok_text(' 일본  도쿄도의\n전파탑 '), '일본 도쿄도의 전파탑')

    def test_first_sentence(self):
        self.assertEqual(PD.first_sentence('성산일출봉은 화산이다. 유네스코 세계유산이다.'), '성산일출봉은 화산이다.')
        self.assertEqual(PD.first_sentence('太魯閣是峽谷。景色很美。'), '太魯閣是峽谷。')
        self.assertEqual(PD.first_sentence('A park in Osaka. It has 300 trees.'), 'A park in Osaka.')
        self.assertEqual(PD.first_sentence('제주의 <b>폭포</b>입니다.<br>물이 떨어집니다.'), '제주의 폭포입니다.')
        self.assertIsNone(PD.first_sentence('가' * 130 + '다.'))  # 중간에서 자르지 않음
        self.assertIsNone(PD.first_sentence(None))


def col_layer(rows):
    return P.to_layer('JP', [dict({'lat': 35.0 + i * 0.001, 'lon': 139.0, 'src': 'x', 'lic': 'CC0', 'c': 'museum'}, **r) for i, r in enumerate(rows)])


class TestRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, 'store'))
        lay = col_layer([{'id': 'wikivoyage:1', 'nm': '東京タワー', 'wd': 'Q183536', 'wid': 'w-tile:1'},
                         {'id': 'taiwan_tourism:C1', 'nm': '太魯閣', 'dsc': '太魯閣國家公園位於花蓮縣。', 'wid': 'w-tile:9'},
                         {'id': 'x:3', 'nm': 'ramen'}])
        self.store.put_json('processed/out/r1/col_JP_0.json.gz', lay)
        self.store.put_json('processed/latest.json', {'run': 'r1', 'files': ['col_JP_0.json.gz'], 'paths': {'col_JP_0.json.gz': 'processed/out/r1/col_JP_0.json.gz'}})
        self.extra = os.path.join(self.tmp, 'extra'); os.makedirs(self.extra)
        gz(os.path.join(self.extra, 'hours-japan.json.gz'), {'schema': 'hours-1', 'rows': {'w-tile:2': {'q': 'Q287165', 'src': 'osm'}}})
        gz(os.path.join(self.extra, 'kto_50.json.gz'), {'schema': 'tourapi-1', 'rows': {'kto:1': {'n': '성산일출봉', 'ov': '성산일출봉은 제주도 동쪽에 있는 화산이다. 둘째 문장.'},
                                                                                     'kto:2': {'n': '가게'}}})
        gz(os.path.join(self.extra, 'w-1.json.gz'), {'id': ['kowiki:Q626162', 'ovt:abc']})
        self.data = {r['qid']: {'desc': r['desc']} for r in SAMPLE if r['qid']}
        self.data['Q183536']['label'] = {'en': 'Tokyo Tower'}

    def go(self, http, budget=10, clock=None):
        out = os.path.join(self.tmp, 'out')
        kw = {'clock': clock} if clock else {}
        return PD.run(self.store, None, self.extra, out, budget, http=http, **kw), out

    def test_end_to_end(self):
        h = FakeHttp(self.data)
        (rep, last), out = self.go(h)
        self.assertEqual(sorted(h.asked), ['Q183536', 'Q287165', 'Q626162'])
        files = sorted(os.listdir(out))
        self.assertIn('desc-kto.json.gz', files); self.assertIn('desc-tw.json.gz', files)
        wd = {}
        for f in files:
            j = json.loads(gzip.decompress(open(os.path.join(out, f), 'rb').read()))
            self.assertEqual(j['schema'], 'desc-1')
            self.assertEqual(PL.describe(os.path.join(out, f))['kind'], 'desc')
            for rid, v in j['rows'].items():
                self.assertEqual(set(v) - {'q'}, set(PD.SLOTS))  # 5개 칸이 늘 있음(없는 것은 null)
            if f.startswith('desc-wd-'):
                self.assertEqual((j['license'], j['credit']), ('CC0-1.0', 'Wikidata (CC0)'))
                wd.update(j['rows'])
            elif f == 'desc-kto.json.gz':
                self.assertEqual(j['license'], 'KOGL-1')
                self.assertEqual(j['rows'], {'kto:1': {'ko': '성산일출봉은 제주도 동쪽에 있는 화산이다.', 'en': None, 'ja': None, 'zh_Hans': None, 'zh_Hant': None}})
            elif f == 'desc-tw.json.gz':
                self.assertEqual(set(j['rows']), {'taiwan_tourism:C1', 'w-tile:9'})
                self.assertEqual(j['rows']['w-tile:9']['zh_Hant'], '太魯閣國家公園位於花蓮縣。')
                self.assertIsNone(j['rows']['w-tile:9']['zh_Hans'])
        self.assertEqual(set(wd), {'wikivoyage:1', 'w-tile:1', 'w-tile:2', 'kowiki:Q626162'})  # 수집 층 id·타일 id 둘 다
        self.assertEqual(wd['w-tile:1']['ko'], '일본 도쿄도 미나토구에 위치한 전파탑')
        self.assertEqual(wd['kowiki:Q626162']['zh_Hans'], None)
        self.assertEqual(last['left'], 0)
        h2 = FakeHttp(self.data)
        self.go(h2)
        self.assertEqual(h2.calls, 0)  # 캐시(30일) — 다시 묻지 않음

    def test_budget_resumes(self):
        t = [0.0]
        clock = lambda: t[0]
        PD.BATCH, old = 1, PD.BATCH
        try:
            class Slow(FakeHttp):
                def get(s, url, accept=None):
                    t[0] += 400
                    return FakeHttp.get(s, url, accept)
            h = Slow(self.data)
            (_, last), _ = self.go(h, budget=10, clock=clock)
            self.assertGreater(last['left'], 0); self.assertEqual(last['stop'], 'TIME')
            h2 = FakeHttp(self.data)
            (_, last2), _ = self.go(h2, budget=10)
            self.assertEqual(last2['left'], 0); self.assertEqual(len(set(h.asked) & set(h2.asked)), 0)
        finally:
            PD.BATCH = old

    def test_manifest_part(self):
        (_, _), out = self.go(FakeHttp(self.data))
        fs = sorted(os.path.join(out, f) for f in os.listdir(out))
        man = PL.build_manifest('t1', fs)
        root, blobs = PL.split_manifest(man, PL.canonical if hasattr(PL, 'canonical') else __import__('canonical_fix').canonical)
        self.assertFalse([e for e in root['layers'] if e['kind'] == 'desc'])  # 앱 연결 전: 뿌리 목록 밖(2단 목록)
        self.assertTrue(any(k.startswith('desc-') for k in blobs))


class TestCoverage(unittest.TestCase):
    def test_desc_section(self):
        d = tempfile.mkdtemp()
        gz(os.path.join(d, 'col_JP_0.json.gz'), col_layer([{'id': 'a:1', 'nm': 'x1', 'c': 'museum'}, {'id': 'a:2', 'nm': 'x2', 'wid': 'w:2'}, {'id': 'a:3', 'nm': 'x3', 'c': 'restaurant'}]))
        PD.write_layer(os.path.join(d, 'desc-wd-00.json.gz'), PD.layer_doc('wikidata', {'a:1': {'ko': '박물관이다', 'en': 'a museum', 'ja': None, 'zh_Hans': None, 'zh_Hant': None, 'q': 'Q1'},
                                                                                     'w:2': {'ko': None, 'en': 'a big museum', 'ja': None, 'zh_Hans': None, 'zh_Hant': None, 'q': 'Q2'}}))
        lay = CV.measure_dir(d, {})
        a = lay['desc']['all']
        self.assertEqual((a['n'], a['any'], a['ko'], a['en'], a['ja']), (3, 2, 1, 2, 0))
        self.assertEqual(lay['desc']['files']['wikidata']['rows'], 2)
        md = CV.to_md(CV.build({}, lay, date='2026-11-17'))
        self.assertIn('### 7. 장소 소개', md); self.assertIn('| 전체 | 3 | 66.7% | 33.3% | 66.7% | 0.0% |', md)


class TestOsm(unittest.TestCase):
    def test_slots(self):
        s = DO.slots_of({'description:en': 'Old castle tower. Rebuilt in 1931.', 'description': '오래된 성의 망루', 'description:zh': '大阪城的天守阁'}, ['Osaka Castle'])
        self.assertEqual(s, {'ko': '오래된 성의 망루', 'en': 'Old castle tower.', 'ja': None, 'zh_Hans': '大阪城的天守阁', 'zh_Hant': None})
        self.assertFalse(any(DO.slots_of({'description': 'Entrée par la rue'}).values()))  # 언어를 모르는 라틴 문자 글은 싣지 않음
        self.assertEqual(DO.slots_of({'description': '大阪城の天守閣です'})['ja'], '大阪城の天守閣です')

    def test_rows_of(self):
        d = {'id': ['t:1', 't:2'], '_lat': [34.6873, 35.0], '_lon': [135.5262, 135.0], 'nm': ['大阪城', 'x'], 'ko': [None, None], 'en': [None, None], 'ja': [None, None], 'zh': [None, None]}
        osm = [(34.6874, 135.5262, ['大阪城'], {'ko': None, 'en': 'Castle keep tower', 'ja': None, 'zh_Hans': None, 'zh_Hant': None}, None, 'way/42', True)]
        self.assertEqual(DO.rows_of(d, osm), {'t:1': {'ko': None, 'en': 'Castle keep tower', 'ja': None, 'zh_Hans': None, 'zh_Hant': None, 'osm': 'way/42'}})
        doc = PD.layer_doc('osm', DO.rows_of(d, osm), {'region': 'japan'})
        self.assertEqual((doc['license'], doc['page']), ('ODbL-1.0', 'https://www.openstreetmap.org/{osm}'))


class TestSources(unittest.TestCase):
    def test_taiwan_first_sentence(self):
        z = io.BytesIO()
        with zipfile.ZipFile(z, 'w') as zz:
            zz.writestr('a.json', json.dumps({'XML_Head': {'Infos': {'Info': [
                {'Id': 'C1_1', 'Name': '太魯閣', 'Px': 121.6, 'Py': 24.16, 'Toldescribe': '太魯閣國家公園以峽谷聞名。園區很大。'},
                {'Id': 'C1_2', 'Name': '某地', 'Px': 121.5, 'Py': 25.0, 'Description': '長' * 200}]}}}, ensure_ascii=False))
        rows = C.parse_taiwan(z.getvalue(), {'id': 'taiwan_tourism', 'license': 'OGDL-Taiwan-1.0'})
        self.assertEqual(rows[0]['dsc'], '太魯閣國家公園以峽谷聞名。'); self.assertNotIn('dsc', rows[1])
        lay = P.to_layer('TW', [dict(r, c='sight') for r in rows])
        self.assertEqual(lay['dsc'], ['太魯閣國家公園以峽谷聞名。', None])
        self.assertNotIn('dsc', P.to_layer('JP', [{'id': 'a:1', 'nm': 'a', 'lat': 1, 'lon': 1, 'src': 'a', 'lic': 'CC0'}]))

    def test_registry_and_no_banned(self):
        reg = PD.load_registry()
        wd = next(s for s in reg['sources'] if s['id'] == 'wikidata_official')
        self.assertTrue(wd['url'].startswith('https://qlever.dev/') and 'descriptions' in wd['fields'])
        wp = next(s for s in reg['sources'] if s['id'] == 'wikipedia_lead')
        self.assertEqual(wp['status'], 'candidate')  # 위키백과 본문은 받지 않음(판정: 보류)
        src = open(os.path.join(ROOT, 'scripts', 'place_desc.py'), encoding='utf-8').read() + open(os.path.join(ROOT, 'tools', 'desc_osm.py'), encoding='utf-8').read()
        for u in re.findall(r'https?://[^\s\'"`)]+', src):
            self.assertFalse(forbidden(u), u)
        self.assertNotIn('wikipedia.org/w/api.php', src); self.assertNotIn("'https://www.wikidata.org/w/api.php", src)


if __name__ == '__main__':
    unittest.main()
