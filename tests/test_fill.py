"""11/15 데이터 보강(영업시간·사진·GTFS 보관) — 네트워크 없는 작은 시험."""
import gzip, json, os, sys, tempfile, unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path[:0] = [os.path.join(ROOT, 'tools'), os.path.join(ROOT, 'scripts', 'collect'), os.path.join(ROOT, 'scripts')]
import hours_merge as HM, hours_osm as HO, photo_layer as PL, collectors as C, layers_plan as LP  # noqa: E402


def wgz(path, doc):
    with gzip.open(path, 'wt', encoding='utf-8') as f:
        json.dump(doc, f)


class TestHours(unittest.TestCase):
    def test_match_and_merge(self):
        d = {'id': ['a', 'b'], '_lat': [48.86, 13.75], '_lon': [2.34, 100.50], 'nm': ['Musée du Louvre', 'Wat Pho'], 'ko': [None, None], 'en': [None, 'Wat Pho'], 'ja': [None, None], 'zh': [None, None]}
        osm = [(48.8601, 2.3401, ['Musée du Louvre', 'Louvre Museum'], 'We-Mo 09:00-18:00', 'Q19675', 'w1', True),
               (13.7502, 100.5002, ['วัดโพธิ์', 'Wat Pho'], None, 'Q1059910', 'n2', True)]
        rows = HO.match(d, osm)
        self.assertEqual(rows['a']['oh'], 'We-Mo 09:00-18:00'); self.assertEqual(rows['b']['q'], 'Q1059910'); self.assertNotIn('oh', rows['b'])
        t = tempfile.mkdtemp(); o, c, out = (os.path.join(t, k) for k in ('o', 'c', 'out'))
        os.makedirs(o); os.makedirs(c)
        wgz(os.path.join(o, 'hours-eu-fr.json.gz'), {'schema': 'hours-1', 'bbox': [-5, 41, 10, 51], 'rows': {'a': rows['a']}})
        wgz(os.path.join(c, 'col_FR_0.json.gz'), {'cc': 'FR', 'id': ['x', 'y'], 'dla': [4886000, 0], 'dlo': [234000, 100], 'wid': ['a', 'z'], 'h': ['Mo-Su 09:00-21:00', 'raw:sometimes'],
                                                  'rk': ['o', None], 'src': ['official_sites', 'wikivoyage'], 'url': ['https://www.louvre.fr', None]})
        rep = HM.merge(o, c, out)
        m = json.load(gzip.open(os.path.join(out, 'hours-eu-fr.json.gz')))['rows']['a']
        self.assertEqual((m['oh'], m['src'], m.get('hd')), ('Mo-Su 09:00-21:00', 'official', 1))  # 공식 > OSM, 다르면 hd
        self.assertEqual(rep['official'], 1); self.assertFalse(os.path.exists(os.path.join(out, 'hours-col-fr.json.gz')))  # raw: 는 버림


class TestPhotos(unittest.TestCase):
    def test_license_filter(self):
        rec = {'thumb': 'https://upload.wikimedia.org/x.jpg', 'credit': '사진: A · CC BY-SA 4.0', 'license': 'CC BY-SA 4.0', 'artist': 'A', 'page': 'p'}
        self.assertEqual(PL.commons_row(rec, 'x.jpg')['photo_license'], 'CC BY-SA 4.0')
        for bad in ('Public domain', 'CC BY-NC 2.0', 'GFDL'):
            self.assertIsNone(PL.commons_row(dict(rec, license=bad), 'x.jpg'))
        self.assertIsNotNone(PL.commons_row(dict(rec, license='CC0'), 'x.jpg'))

    def test_kto_match(self):
        t = tempfile.mkdtemp()
        wgz(os.path.join(t, 'kto_47.json.gz'), {'rows': {'kto:1': {'n': '불국사', 'lat': 35.7900, 'lon': 129.3320, 'photo': {'url': 'http://tong.visitkorea.or.kr/a.jpg', 'license': 'KOGL-3', 'credit': '한국관광공사', 'no_modify': True}},
                                                         'kto:2': {'n': '석굴암', 'lat': 35.79, 'lon': 129.35, 'photo': {'url': 'http://x/b.jpg', 'license': 'KOGL-2'}}}})
        g = PL.kto_photos(t)
        d = {'nm': ['불국사', '석굴암'], 'ko': [None, None], '_lat': [35.7901, 35.79], '_lon': [129.3321, 129.35]}
        v = PL.from_kto(d, g, 0)
        self.assertEqual((v['photo_src'], v['photo_license'], v['no_modify']), ('tourapi', 'KOGL-3', True)); self.assertIn('공공누리 제3유형', v['photo_credit'])
        self.assertIsNone(PL.from_kto(d, g, 1))  # 2유형은 수집 단계에서 버려지고, 여기서도 받지 않음


class TestGtfs(unittest.TestCase):
    CSV = ('id,data_type,status,redirect.id,urls.authentication_type,urls.license,urls.latest,provider,location.country_code,location.municipality,'
           'location.bounding_box.minimum_latitude,location.bounding_box.maximum_latitude,location.bounding_box.minimum_longitude,location.bounding_box.maximum_longitude\n'
           'mdb-3175,gtfs,active,,0,https://creativecommons.org/licenses/by/4.0/,https://files.example.org/a.zip,Toei,JP,Koto,35.6,35.8,139.7,139.9\n'
           'tdg-80921,gtfs,active,,0,https://wiki.lafabriquedesmobilites.fr/wiki/Licence_Mobilit%C3%A9s,https://files.example.org/b.zip,IDFM,FR,Paris,48.1,49.2,1.4,3.5\n').encode()

    def test_targets_and_license(self):
        f = C.gtfs_feeds(self.CSV)
        self.assertEqual([x['id'] for x in f], ['mdb-3175'])  # IDFM 은 허락 전엔 받지 않음
        self.assertEqual(len(C.gtfs_feeds(self.CSV, ('Licence_Mobilit',))), 2)
        tg = [{'city': 'tokyo', 'bbox': [139.0, 35.5, 139.95, 35.9]}]
        self.assertEqual(C.gtfs_target(f[0], tg), 'tokyo'); self.assertEqual(C.lic_class(f[0]['license']), 'cc-by')
        self.assertEqual(C.lic_class('https://opendatacommons.org/licenses/odbl/1.0/'), 'odbl')


class TestPlan(unittest.TestCase):
    def test_resolve(self):
        tag, mode, sel = LP.resolve({'IN_GROUPS': 'all'})
        self.assertTrue(tag.startswith('L-')); self.assertEqual(mode, 'full')
        self.assertTrue({'th', 'la', 'eu-fr', 'kr'} <= set(sel))
        self.assertEqual(LP.resolve({'IN_MODE': 'merge', 'IN_LATEST': 'L-2026-11-02'})[:2], ('L-2026-11-02', 'merge'))
        with self.assertRaises(SystemExit):
            LP.resolve({'IN_MODE': 'merge', 'IN_LATEST': ''})


if __name__ == '__main__':
    unittest.main()
