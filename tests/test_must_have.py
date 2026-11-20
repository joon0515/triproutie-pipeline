"""꼭 있어야 할 명소 점검·보충(scripts/must_have.py) — 네트워크 없이. (2026-11-20, 270기)"""
import gzip, json, os, sys, tempfile, unittest

ROOT = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, os.path.join(ROOT, 'scripts')); sys.path.insert(0, os.path.join(ROOT, 'scripts', 'collect')); sys.path.insert(0, os.path.join(ROOT, 'tools'))
import must_have as MH  # noqa: E402
from common import Store  # noqa: E402

FP = 'http://commons.wikimedia.org/wiki/Special:FilePath/'


def gz(p, doc):
    open(p, 'wb').write(gzip.compress(json.dumps(doc, ensure_ascii=False).encode()))


def pack(rows):
    la = lo = 0
    dla, dlo = [], []
    for r in rows:
        A, B = round(r[2] * 1e5), round(r[3] * 1e5)
        dla.append(A - la); dlo.append(B - lo); la, lo = A, B
    cats = ['sight', 'food']
    return {'n': len(rows), 'id': [r[0] for r in rows], 'nm': [r[1] for r in rows], 'dla': dla, 'dlo': dlo, 's': [75] * len(rows),
            'cats': cats, 'c': [cats.index(r[4]) for r in rows], 'cc': 'KR'}


class T(unittest.TestCase):
    def test_must_and_kto(self):
        d = tempfile.mkdtemp()
        for k in ('tiles', 'kto', 'store', 'out'):
            os.makedirs(os.path.join(d, k))
        o = 1 / 111320.0
        gz(os.path.join(d, 'tiles', 'w-1.json.gz'), pack([
            ('h:a', '창덕궁', 37.5794, 126.9910, 'sight'),
            ('h:b', '경복궁역', 37.5757, 126.9735, 'sight'),          # 역 이름 — 궁과 닮음 0.75 미만이면 있음으로 치지 않음
            ('h:c', '성산일출봉 청운식당', 33.4600, 126.9400, 'food')]))
        geo = {'seoul': [['Q1', 37.5796, 126.9770, 200, FP + 'G.jpg', ['Gyeongbokgung', '경복궁', '景福宮'], 0],
                         ['Q2', 37.5794, 126.9911, 150, FP + 'C.jpg', ['Changdeokgung', '창덕궁'], 0],
                         ['Q3', 37.56, 126.98, 900, FP + 'S.jpg', ['Jung District', '중구'], 1]],          # 행정구역 → 대상 아님
               'jeju': [['Q4', 33.4581, 126.9425, 120, FP + 'I.jpg', ['Seongsan Ilchulbong', '성산일출봉'], 0]]}
        st = Store(os.path.join(d, 'store'))
        st.put_json('processed/photos/geo_items.json.gz', {'cities': geo})
        st.put_json('processed/photos/commons.json.gz', {FP + 'G.jpg': ['2026-11-17', {'lic': 'Q18199165', 'by': 'K'}]})
        gz(os.path.join(d, 'kto', 'kto_50.json.gz'), {'rows': {
            'kto:126452': {'n': '성산일출봉 [유네스코 세계자연유산]', 't': '12', 'lat': 33.4581 + 10 * o, 'lon': 126.9425,
                           'photo': {'url': 'http://tong.visitkorea.or.kr/a.jpg', 'license': 'KOGL-1'}},
            'kto:9': {'n': '창덕궁', 't': '12', 'lat': 37.5794, 'lon': 126.9910},                   # 이미 있음
            'kto:10': {'n': '어느식당', 't': '39', 'lat': 33.46, 'lon': 126.94},
            'kto:11': {'n': '안동 하회마을', 't': '12', 'lat': 36.539, 'lon': 128.518}}})                 # 우선 도시 밖 — 한국 전역으로 보충                   # 식당 유형은 싣지 않음
        reps, by_cc, ph = MH.run(st, None, os.path.join(d, 'tiles'), os.path.join(d, 'kto'), os.path.join(d, 'out'))
        s, j = reps['seoul'], reps['jeju']
        self.assertEqual((s['must'], s['present'], s['missing_before'], s['missing_after']), (2, 1, 1, 0))
        self.assertEqual(s['missing_names'], ['Gyeongbokgung'])
        self.assertEqual((j['kto_added'], j['must'], j['present'], j['missing_before'], j['wd_added'], j['missing_after']), (1, 1, 0, 1, 0, 0))  # TourAPI 로 채워져 Wikidata 는 따로 안 더함
        ids = sorted(r['id'] for r in by_cc['KR'])
        self.assertEqual(ids, ['kto:11', 'kto:126452', 'wikidata:Q1'])
        self.assertEqual(ph['kto:126452']['photo_license'], 'KOGL-1'); self.assertEqual(ph['wikidata:Q1']['photo_license'], 'CC BY-SA 4.0')
        f = os.path.join(d, 'out', 'col_mustadd_KR-00.json.gz')
        doc = json.loads(gzip.decompress(open(f, 'rb').read()))
        self.assertEqual((doc['schema'], doc['n'], sorted(doc['lic'])), ('collect-1', 3, ['CC0', 'KOGL', 'KOGL']))
        b1 = open(f, 'rb').read()
        MH.run(st, None, os.path.join(d, 'tiles'), os.path.join(d, 'kto'), os.path.join(d, 'out'))
        self.assertEqual(b1, open(f, 'rb').read())  # 다시 돌려도 같은 파일


if __name__ == '__main__':
    unittest.main()
