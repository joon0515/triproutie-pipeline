"""이름 5개 언어(names-2)·영업시간 검사·사진(Commons SDC) 층과 coverage 0절 시험 — 네트워크·키 없음. (2026-11-18, 223기)"""
import gzip, json, os, re, sys, tempfile, unittest, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
for p in ('scripts', 'scripts/collect', 'tools'):
    sys.path.insert(0, os.path.join(ROOT, p))
import place_desc as PD  # noqa: E402
import place_names as PN  # noqa: E402
import names5_osm as N5  # noqa: E402
import names_osm as NO  # noqa: E402
import hours_merge as HM  # noqa: E402
import publish_layers as PL  # noqa: E402
import coverage as CV  # noqa: E402
import process as P  # noqa: E402
from oh_check import oh_ok  # noqa: E402
from common import Store  # noqa: E402


def gz(path, obj):
    with open(path, 'wb') as f:
        f.write(gzip.compress(json.dumps(obj, ensure_ascii=False).encode()))


def load(path):
    return json.loads(gzip.decompress(open(path, 'rb').read()))


class TestNameSlots(unittest.TestCase):
    def test_wikidata_labels(self):
        s = PN.wd_label_slots({'ko': '도쿄 타워', 'en': 'Tokyo Tower', 'ja': '東京タワー', 'zh-hans': '东京塔', 'zh-hant': '東京鐵塔', 'zh': '东京塔'})
        self.assertEqual(s, {'ko': '도쿄 타워', 'en': 'Tokyo Tower', 'ja': '東京タワー', 'zh_Hans': '东京塔', 'zh_Hant': '東京鐵塔'})
        # 번체만 있으면 간체 칸은 비운다(변환하지 않음) · 꼬리표 없는 zh 는 글자로 갈릴 때만
        self.assertEqual(PN.wd_label_slots({'zh-tw': '臺北車站'})['zh_Hans'], None)
        self.assertEqual(PN.wd_label_slots({'zh': '台北车站'})['zh_Hans'], '台北车站')
        self.assertEqual(PN.wd_label_slots({'zh': '大阪城'}), {k: None for k in PN.SLOTS})  # 갈리는 글자가 없으면 모름 → 비움
        self.assertIsNone(PN.wd_label_slots({'zh-hans': '東京鐵塔'})['zh_Hans'])  # 꼬리표와 글자가 어긋남
        self.assertIsNone(PN.wd_label_slots({'zh-hant': 'とうきょう'})['zh_Hant'])  # 가나

    def test_name_ok(self):
        self.assertEqual(PN.name_ok('  Tokyo   Tower '), 'Tokyo Tower')
        for bad in ('', 'a;b', 'http://x', 'x' * 121, None, 3):
            self.assertIsNone(PN.name_ok(bad))

    def test_osm_tags(self):
        s = N5.slots_of({'name': '大阪城', 'name:en': 'Osaka Castle', 'name:ko': '오사카성', 'name:zh-Hant': '大阪城', 'name:zh': '大阪城', 'name:zh-TW': '大坂城'})
        self.assertEqual(s, {'ko': '오사카성', 'en': 'Osaka Castle', 'ja': None, 'zh_Hans': None, 'zh_Hant': '大阪城'})
        self.assertEqual(N5.slots_of({'name': 'x', 'name:zh-CN': '东京塔'})['zh_Hans'], '东京塔')
        self.assertFalse(any(N5.slots_of({'name': 'Café'}).values()))  # 언어 표시 없는 name 은 쓰지 않음

    def test_osm_rows(self):
        d = {'id': ['t:1', 't:2'], '_lat': [34.6873, 35.0], '_lon': [135.5262, 135.0], 'nm': ['大阪城', 'x'], 'ko': [None, None], 'en': [None, None], 'ja': [None, None], 'zh': [None, None]}
        osm = [(34.6874, 135.5262, ['大阪城', 'Osaka Castle'], {'ko': '오사카성', 'en': 'Osaka Castle', 'ja': None, 'zh_Hans': None, 'zh_Hant': None}, None, 'way/42', True)]
        rows = N5.rows_of(d, osm)
        self.assertEqual(rows, {'t:1': {'ko': '오사카성', 'en': 'Osaka Castle', 'ja': None, 'zh_Hans': None, 'zh_Hant': None, 'osm': 'way/42'}})
        out = tempfile.mkdtemp()
        made = PN.write_sharded(out, 'names-osm-jp-kansai', rows, lambda p, part: PN.names_doc('osm', p, {'region': 'jp-kansai', 'part': part}), n=1)
        e = PL.describe(os.path.join(out, sorted(made)[0]))
        self.assertEqual((e['kind'], e['license'], e['src'], e['langs']['ko']), ('names5', 'ODbL-1.0', 'osm', 1))
        man = PL.build_manifest('t', [os.path.join(out, f) for f in made])
        root, blobs = PL.split_manifest(man, __import__('canonical_fix').canonical)
        self.assertFalse([x for x in root['layers'] if x['kind'] == 'names5'])  # 앱 연결 전: 2단 목록
        self.assertTrue(any(k.startswith('names5-') for k in blobs))

    def test_names_osm_tiles_without_ko(self):
        d = tempfile.mkdtemp()
        for k in (1, 2):
            gz(os.path.join(d, f'w-{k}.json.gz'), {'id': [f'w:{k}'], 'nm': ['大阪城'], 'c': [0], 'cats': ['castle'], 'dla': [3468730], 'dlo': [13552620]})
        out = NO.load_packs([os.path.join(d, 'w-1.json.gz'), os.path.join(d, 'w-2.json.gz')])  # 11/17 TypeError: ko 열이 없는 타일
        self.assertEqual(out['ko'], [None, None])
        self.assertEqual(NO.targets(NO.load_pack(os.path.join(d, 'w-1.json.gz'))), [0])


class TestHours(unittest.TestCase):
    def test_oh_ok(self):
        for s in ('24/7', 'Mo-Fr 09:00-18:00; Sa 10:00-14:00; PH off', 'Mo-Fr 08:00-12:00, Sa 10:00-12:00', 'Fr-Sa 18:00-02:00', 'Apr-Oct: Mo-Su 09:00-19:00',
                  'Mo-Fr 10:00+', 'sunrise-sunset', 'Mo-Fr 09:00-18:00 "appointment"', 'Su[1] 10:00-12:00'):
            self.assertEqual(oh_ok(s), s)
        for s in ('9시~6시', 'Mo-Fr 9am-5pm', 'Mon-Fri 09:00-17:00', 'Mo 25:00-26:00', 'Mo 09:60-10:00', 'week 01-53 Mo 10:00-12:00', 'raw:open daily', 'x' * 300, None):
            self.assertIsNone(oh_ok(s))

    def test_merge_drops_unparsed_and_splits(self):
        t = tempfile.mkdtemp()
        osm, col, out = (os.path.join(t, k) for k in ('osm', 'col', 'out'))
        for k in (osm, col):
            os.makedirs(k)
        rows = {f'w:{i}': {'oh': 'Mo-Fr 09:00-18:00', 'src': 'osm'} for i in range(400)}
        rows['w:bad'] = {'oh': 'Mo-Fr 9am-5pm', 'src': 'osm'}
        rows['w:badq'] = {'oh': 'whenever', 'src': 'osm', 'q': 'Q1'}
        gz(os.path.join(osm, 'hours-eu-de.json.gz'), {'schema': 'hours-1', 'bbox': [5, 47, 15, 55], 'rows': rows})
        gz(os.path.join(col, 'col_DE_0.json.gz'), P.to_layer('DE', [{'id': 'a:1', 'nm': 'x', 'lat': 50.0, 'lon': 8.0, 'src': 'official_sites', 'rank': 'official', 'lic': 'x',
                                                                    'wid': 'w:1', 'h': 'Mo-Fr 10:00-17:00'},
                                                                   {'id': 'a:2', 'nm': 'y', 'lat': 50.0, 'lon': 8.1, 'src': 'wikivoyage', 'lic': 'x', 'wid': 'w:new', 'h': 'raw:daily'}]))
        old = HM.FILE_MAX
        HM.FILE_MAX = 900
        try:
            HM.merge(osm, col, out)
        finally:
            HM.FILE_MAX = old
        fs = sorted(os.listdir(out))
        self.assertTrue(len(fs) >= 2 and all(re.fullmatch(r'hours-eu-de-p\d\d\.json\.gz', f) for f in fs), fs)
        allrows = {}
        for f in fs:
            j = load(os.path.join(out, f))
            self.assertEqual(j['schema'], 'hours-1'); self.assertEqual(PL.describe(os.path.join(out, f))['kind'], 'hours')
            allrows.update(j['rows'])
        self.assertNotIn('w:bad', allrows); self.assertNotIn('oh', allrows['w:badq']); self.assertNotIn('w:new', allrows)
        self.assertEqual((allrows['w:1']['oh'], allrows['w:1']['src'], allrows['w:1'].get('hd')), ('Mo-Fr 10:00-17:00', 'official', 1))
        self.assertEqual(len(allrows), 401)


class FakeQlever:
    """wikidata: 라벨·P18, wikimedia-commons: 라이선스·저작자."""

    def __init__(self, labels, p18, sdc):
        self.labels, self.p18, self.sdc, self.calls = labels, p18, sdc, []

    def get(self, url, accept=None):
        assert url.startswith('https://qlever.dev/api/') and accept == 'application/sparql-results+json'
        assert 'wikidata.org/w/' not in url and 'commons.wikimedia.org/w/' not in url
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)['query'][0]
        self.calls.append(url.split('?')[0])
        b = []
        if url.startswith(PN.COMMONS_EP):
            for u in re.findall(r'<(http://commons[^>]+)>', q):
                for lic, an, un in self.sdc.get(u, ()):
                    x = {'u': {'value': u}, 'lic': {'value': 'http://www.wikidata.org/entity/' + lic}}
                    if an:
                        x['an'] = {'value': an}
                    if un:
                        x['un'] = {'value': un}
                    b.append(x)
            return json.dumps({'results': {'bindings': b}}).encode()
        for x in re.findall(r'wd:(Q\d+)', q.split('VALUES')[1].split('}')[0]):
            it = {'type': 'uri', 'value': 'http://www.wikidata.org/entity/' + x}
            for t, v in self.labels.get(x, {}).items():
                b.append({'item': it, 'k': {'value': 'l'}, 'v': {'value': v, 'xml:lang': t}})
            if x in self.p18:
                b.append({'item': it, 'k': {'value': 'p'}, 'v': {'type': 'uri', 'value': self.p18[x]}})
        return json.dumps({'results': {'bindings': b}}).encode()


F1 = PN.FILEPATH + 'Tokyo%20Tower.jpg'
F2 = PN.FILEPATH + 'No%20author.jpg'
F3 = PN.FILEPATH + 'Odd%20license.jpg'


class TestRunShared(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, 'store'))
        lay = P.to_layer('JP', [dict({'lat': 35.0 + i * 0.001, 'lon': 139.0, 'src': 'x', 'lic': 'CC0', 'c': 'museum'}, **r) for i, r in enumerate([
            {'id': 'wikivoyage:1', 'nm': '東京タワー', 'wd': 'Q1', 'wid': 'w-tile:1'}, {'id': 'x:2', 'nm': 'a', 'wd': 'Q2'}, {'id': 'x:3', 'nm': 'b', 'wd': 'Q3'}])])
        self.store.put_json('processed/out/r1/col_JP_0.json.gz', lay)
        self.store.put_json('processed/latest.json', {'run': 'r1', 'files': ['col_JP_0.json.gz'], 'paths': {'col_JP_0.json.gz': 'processed/out/r1/col_JP_0.json.gz'}})
        self.extra = os.path.join(self.tmp, 'extra'); os.makedirs(self.extra)
        self.http = FakeQlever({'Q1': {'en': 'Tokyo Tower', 'ko': '도쿄 타워', 'zh-hant': '東京鐵塔'}, 'Q2': {'en': 'Place B'}},
                               {'Q1': F1, 'Q2': F2, 'Q3': F3},
                               {F1: [('Q14946043', 'Morio', 'Morio'), ('Q6938433', 'Morio', 'Morio')], F2: [('Q20007257', None, None)], F3: [('Q98592850', 'X', None)]})

    def run_once(self):
        out = os.path.join(self.tmp, 'out')
        old = PN.Http
        PN.Http = lambda *a, **k: self.http  # Commons 쪽도 같은 가짜 끝점
        try:
            PD.run(self.store, None, self.extra, out, 10, http=self.http)
        finally:
            PN.Http = old
        return out

    def test_shared_lookup_names_and_photos(self):
        out = self.run_once()
        cache = self.store.get_json('processed/desc/wd.json.gz')
        self.assertEqual(cache['Q1'][2], {'l': {'ko': '도쿄 타워', 'en': 'Tokyo Tower', 'zh_Hant': '東京鐵塔'}, 'p': F1})  # 한 번 물어 설명·라벨·사진 파일을 함께
        names, photos = {}, {}
        for f in sorted(os.listdir(out)):
            if f.startswith('names-wd-'):
                j = load(os.path.join(out, f))
                self.assertEqual((j['schema'], j['license'], j['src']), ('names-2', 'CC0-1.0', 'wikidata'))
                names.update(j['rows'])
            elif f.startswith('photos-wd-'):
                self.assertEqual(PL.describe(os.path.join(out, f))['kind'], 'photos')
                photos.update(load(os.path.join(out, f))['rows'])
        self.assertEqual(names['w-tile:1'], {'ko': '도쿄 타워', 'en': 'Tokyo Tower', 'ja': None, 'zh_Hans': None, 'zh_Hant': '東京鐵塔', 'q': 'Q1'})
        self.assertEqual(names['wikivoyage:1']['zh_Hant'], '東京鐵塔')
        self.assertEqual(names['x:2']['en'], 'Place B'); self.assertNotIn('x:3', names)
        self.assertEqual(set(photos), {'wikivoyage:1', 'w-tile:1'})  # 저작자 없는 것·목록 밖 라이선스는 싣지 않음
        p = photos['w-tile:1']
        self.assertEqual((p['photo_license'], p['photo_author'], p['photo_src']), ('CC0 1.0', 'Morio', 'commons'))
        self.assertEqual(p['photo_page'], 'https://commons.wikimedia.org/wiki/File:Tokyo_Tower.jpg')
        self.assertIn('Morio', p['photo_credit']); self.assertIn('CC0 1.0', p['photo_credit'])
        self.assertNotIn('추정', json.dumps(photos, ensure_ascii=False) + json.dumps(names, ensure_ascii=False))
        n = len(self.http.calls)
        self.run_once()
        self.assertEqual(len(self.http.calls), n)  # 캐시: 다시 묻지 않음

    def test_old_cache_is_reasked(self):
        self.store.put_json('processed/desc/wd.json.gz', {'Q1': [PN.today(), {}], 'Q2': [PN.today(), {}], 'Q3': [PN.today(), {}]})  # 11/17 판 캐시(라벨 칸 없음)
        self.run_once()
        self.assertEqual(len(self.store.get_json('processed/desc/wd.json.gz')['Q1']), 3)

    def test_judge(self):
        self.assertEqual(PN.judge({'lic': {'Q14946043', 'Q6938433'}, 'an': {'A'}, 'un': {'a'}}), {'lic': 'Q6938433', 'by': 'A', 'user': 'a'})
        self.assertEqual(PN.judge({'lic': {'Q14946043'}, 'an': set(), 'un': {'a'}}), {'lic': 'Q14946043', 'by': 'a'})
        self.assertEqual(PN.judge({'lic': {'Q14946043'}, 'an': set(), 'un': set()}), {})   # 저작자 없음
        self.assertEqual(PN.judge({'lic': {'Q50829104'}, 'an': {'A'}, 'un': set()}), {})  # 목록 밖 라이선스
        self.assertIsNone(PN.photo_row('https://example.org/x.jpg', {'lic': 'Q6938433', 'by': 'A'}))


class TestCoverageNames(unittest.TestCase):
    def test_section0(self):
        d = tempfile.mkdtemp()
        rows = [{'id': 'a:1', 'nm': 'x1', 'c': 'museum', 'ko': '박물관', 'zh': '东京塔'}, {'id': 'a:2', 'nm': 'x2', 'wid': 'w:2', 'h': 'raw:daily'},
                {'id': 'a:3', 'nm': 'x3', 'c': 'restaurant', 'h': 'Mo-Fr 09:00-18:00', 'zh': '大阪城'}, {'id': 'a:4', 'nm': 'x4', 'zh': '東京鐵塔'}]
        gz(os.path.join(d, 'col_JP_0.json.gz'), P.to_layer('JP', [dict({'lat': 35.0 + i * 0.001, 'lon': 139.0, 'src': 'x', 'lic': 'CC0', 'c': 'museum'}, **r) for i, r in enumerate(rows)]))
        PD.write_layer(os.path.join(d, 'names-wd-00.json.gz'), PN.names_doc('wikidata', {'w:2': {'ko': None, 'en': 'B', 'ja': 'ビー', 'zh_Hans': None, 'zh_Hant': None, 'q': 'Q2'}}))
        PD.write_layer(os.path.join(d, 'names-osm-x-00.json.gz'), PN.names_doc('osm', {'a:4': {'ko': '사', 'en': None, 'ja': None, 'zh_Hans': None, 'zh_Hant': None, 'osm': 'node/1'}}))
        gz(os.path.join(d, 'names-x.json.gz'), {'schema': 'names-1', 'rows': {'a:3': {'ks': '검색만', 'rd': 'よみ'}}})  # 검색 열쇠·읽는 법은 이름 칸이 아님
        gz(os.path.join(d, 'hours-x.json.gz'), {'schema': 'hours-1', 'rows': {'w:2': {'oh': 'Mo-Fr 9am-5pm'}, 'a:4': {'oh': '24/7'}}})
        gz(os.path.join(d, 'photos-wd-00.json.gz'), {'schema': 'photos-1', 'rows': {'w:2': {'photo_url': 'https://commons.wikimedia.org/wiki/Special:FilePath/x.jpg'}}})
        lay = CV.measure_dir(d, {})
        a = lay['places']
        self.assertEqual((a['n'], a['ko'], a['en'], a['ja'], a['zh_Hans'], a['zh_Hant'], a['h'], a['photo']), (4, 2, 1, 1, 1, 1, 2, 1))
        md = CV.to_md(CV.build({}, lay, date='2026-11-18'))
        self.assertIn('### 0. 이름 5개 언어·영업시간·사진 채움', md)
        self.assertIn('| 이름 중국어 번체 | 1 | 25.0% |', md); self.assertIn('| 영업시간 | 2 | 50.0% |', md)
        self.assertNotIn('추정', md)


if __name__ == '__main__':
    unittest.main()
