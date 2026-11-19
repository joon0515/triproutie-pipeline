"""쇼핑 층(shop_index.py)·행사 층(events_layer.py·tourapi searchFestival2)·학습 통계 브랜드 언급 수 — 네트워크 없음. (256기)"""
import gzip, io, json, os, sys, tempfile, unittest, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
for p in ('scripts', os.path.join('scripts', 'collect'), os.path.join('scripts', 'patterns'), 'tools'):
    sys.path.insert(0, os.path.join(ROOT, p))
import shop_index as S  # noqa: E402
import events_layer as EV  # noqa: E402
import publish_layers as PL  # noqa: E402
import tourapi_daily as TD  # noqa: E402
import parse as PP  # noqa: E402
import stats as ST  # noqa: E402
from places import Gazetteer  # noqa: E402

FACTS = [{'id': 'donki_taxfree', 'match': {'cc': 'JP', 'names': ['Don Quijote', 'ドン・キホーテ']}, 'taxfree': {'min': 5000}, 'url': 'https://x', 'checked': '2026-11-20'},
         {'id': 'sb1', 'match': {'cc': 'US', 'names': ['Starbucks'], 'near': [47.6097, -122.3422, 150]}, 'first_year': 1971, 'url': 'https://y', 'checked': '2026-11-20'}]


def ov(i, nm, cat, cf, bq, bn, la, lo):
    return S.ovt_row(i, nm, cat, cf, bq, bn, ['AllThePlaces'], la, lo)


class TestShop(unittest.TestCase):
    def test_is_shop(self):
        for c in ('departmentstore', 'conveniencestore', 'discountstore', 'souvenirshop', 'drugstore', 'shoppingmall'):
            self.assertTrue(S.is_shop(c), c)
        for c in ('coffeeshop', 'restaurant', 'museum', 'workshop', ''):
            self.assertFalse(S.is_shop(c), c)

    def test_ovt_row_keeps_brand_or_traveller_kinds(self):
        self.assertIsNotNone(ov('a', 'Lawson', 'convenience_store', .9, 'Q1', 'Lawson', 35, 139))
        self.assertIsNone(ov('b', 'Corner shop', 'convenience_store', .9, None, None, 35, 139))  # 무브랜드 동네 가게는 뺌
        self.assertIsNotNone(ov('c', 'Nishiki Market', 'market', .9, None, None, 35, 135.7))
        self.assertIsNone(ov('d', 'Cafe', 'coffee_shop', .9, 'Q9', 'X', 35, 139))
        # departmentstore 는 KEEP_SUBS 라 QID 가 틀려도 행은 남되 브랜드는 비어야 한다
        r = ov('f', 'Isetan', 'department_store', .9, 'bad', None, 35, 139)
        self.assertIsNotNone(r); self.assertIsNone(r['bq'])

    def _jp(self):
        rows = [ov(f'dq{i}', f'ドン・キホーテ {i}', 'discount_store', .95 if i == 0 else .7, 'Q100', 'ドン・キホーテ', 35.69 + i * 0.001, 139.70) for i in range(6)]
        rows += [ov('far', 'ドン・キホーテ 大阪', 'discount_store', .9, 'Q100', 'ドン・キホーテ', 34.67, 135.50)]
        rows += [ov('mk', 'Local Market', 'market', .8, None, None, 35.0, 135.7)]
        return rows

    def test_must_one_per_city_cell_and_taxfree_fact(self):
        wd = {'Q100': (40, 1)}
        rows, brands, st = S.build_cc('JP', self._jp(), [], wd, {'JP': .5}, {}, FACTS)
        musts = [k for k, v in rows.items() if v[7] == 1]
        self.assertEqual(sorted(musts), ['overture_places:dq0', 'overture_places:far'])  # 도쿄 칸 1등 + 오사카 칸 1등
        v = rows['overture_places:dq0']
        self.assertIn('taxfree', v[8]); self.assertIn('f:donki_taxfree', v[8]); self.assertIn('chain', v[8]); self.assertIn('known', v[8])
        self.assertEqual(brands['Q100']['c'], 7)
        self.assertEqual(rows['overture_places:mk'][7], 0)
        self.assertTrue(all(0 <= v[6] <= 100 for v in rows.values()))

    def test_iconic_from_own_qid_and_first_store_fact(self):
        ovt = [ov('sb', 'Starbucks', 'coffee_shop', .9, 'Q37158', 'Starbucks', 47.6098, -122.3424)]  # coffeeshop 는 쇼핑이 아니라 Overture 행은 없음
        self.assertEqual(ovt, [None])
        col = [{'id': 'wikidata:Q999', 'name': 'Original Starbucks', 'sub': 'giftshop', 'cf': None, 'bq': None, 'bn': None, 'q': 'Q999',
                'la': 47.6098, 'lo': -122.3424, 'srcs': ['wikidata']}]
        rows, _, st = S.build_cc('US', [], col, {'Q999': (12, 1)}, {}, {}, FACTS)
        v = rows['wikidata:Q999']
        self.assertEqual(v[7], 1); self.assertIn('iconic', v[8]); self.assertIn('first', v[8])
        rows2, _, _ = S.build_cc('US', [], [dict(col[0], la=40.0, lo=-74.0, q='Q998')], {}, {}, {}, FACTS)
        self.assertNotIn('first', rows2['wikidata:Q999' if 'wikidata:Q999' in rows2 else next(iter(rows2))][8])  # 반경 밖은 1호점 사실이 붙지 않음

    def test_merge_attaches_own_qid(self):
        o = [ov('m', 'Mitsukoshi Nihonbashi', 'department_store', .9, 'Q200', 'Mitsukoshi', 35.6863, 139.7732)]
        c = [{'id': 'osm:1', 'name': 'Mitsukoshi Nihonbashi', 'sub': 'departmentstore', 'cf': None, 'bq': None, 'bn': None, 'q': 'Q201',
              'la': 35.68632, 'lo': 139.77322, 'srcs': ['osm']}]
        m = S.merge(o, c)
        self.assertEqual(len(m), 1); self.assertEqual(m[0]['q'], 'Q201'); self.assertIn('osm', m[0]['srcs'])

    def test_brand_trends_need_enough_mentions(self):
        h = {'2026-09-21': {'JP': {'Q1': 10, 'Q2': 1}}, '2026-10-21': {'JP': {'Q1': 30, 'Q2': 3}}, '2026-11-20': {'JP': {'Q1': 60, 'Q2': 9}}}
        t = S.brand_trends(h, 'JP')
        self.assertAlmostEqual(t['Q1'], 0.5)  # 새 언급 30 ÷ 앞 20 − 1
        self.assertNotIn('Q2', t)  # 앞 30일 2건 — 근거 부족
        self.assertEqual(S.brand_trends({'2026-11-20': {'JP': {'Q1': 5}}}, 'JP'), {})

    def test_shop_share_min_sample(self):
        pat = {'groups': {'cc': {'jp': {'all': {'n_days': 40, 'cat_share': {'sight': .5, 'shopping': .25, 'food': .25}}},
                                 'kr': {'all': {'n_days': 5, 'cat_share': {'shopping': 1}}}}}}
        self.assertEqual(S.shop_share(pat), {'JP': 0.5})

    def test_facts_file_has_url_and_date(self):
        fs = S.load_facts()
        self.assertTrue(fs)
        for f in fs:
            self.assertTrue(f['url'].startswith('https://') and f['checked'] and f.get('robots'), f['id'])

    def test_build_end_to_end_and_publish_entry(self):
        with tempfile.TemporaryDirectory() as d:
            store, out = os.path.join(d, 's'), os.path.join(d, 'o')
            os.makedirs(store)
            oj = os.path.join(d, 'ovt.json')
            json.dump({'JP': [dict(i=f'dq{k}', nm=f'Don Quijote {k}', cat='discount_store', cf=.9, bq='Q100', bn='Don Quijote', ds=['AllThePlaces'],
                                   lat=35.69 + k * .001, lon=139.7) for k in range(3)]}, open(oj, 'w'))
            a = type('A', (), dict(store=store, out=out, ccs=None, chunk=None, release=None, patterns=None, brand_hist=None, facts=None, ovt_json=oj))
            sm = S.build(a)
            self.assertEqual(sm['cc']['JP']['rows'], 3)
            fn = os.path.join(out, 'shop-JP-00.json.gz')
            j = json.loads(gzip.decompress(open(fn, 'rb').read()))
            self.assertEqual(j['schema'], 'shop-1'); self.assertIn('donki_taxfree', j['facts']); self.assertNotIn('match', j['facts']['donki_taxfree'])
            e = PL.describe(fn)
            self.assertEqual(e['kind'], 'shop'); self.assertEqual(e['cc'], 'JP'); self.assertEqual(len(e['bbox']), 4)
            g = gzip.open(os.path.join(out, 'brands_gaz_all.jsonl.gz'), 'rt').read()
            self.assertIn('"brand": "Q100"', g)


class TestEvents(unittest.TestCase):
    NOW = '20261120'

    def test_kinds_and_dates(self):
        self.assertEqual(EV.kind_of('성수 팝업스토어'), 'popup')
        self.assertEqual(EV.kind_of('POP-UP Store Tokyo'), 'popup')
        self.assertEqual(EV.kind_of('진해군항제'), 'festival')
        self.assertEqual(EV.ymd('2026-03-01T15:59:59+08:00'), '20260301')
        self.assertIsNone(EV._row('x', '20261101', '20261110', 1, 1, 'tourapi', 'KR', now=self.NOW))  # 끝난 것
        self.assertIsNone(EV._row('x', '20261201', '20261130', 1, 1, 'tourapi', 'KR', now=self.NOW))  # 끝 < 시작

    def test_kto_rows(self):
        r = EV.kto_rows({'1': {'name': '서울 빛초롱축제', 'start': '20261210', 'end': '20270105', 'lat': 37.56, 'lon': 126.97, 'addr': '서울 중구'},
                         '2': {'name': '지난 축제', 'start': '20261001', 'end': '20261010'}}, now=self.NOW)
        self.assertEqual(list(r), ['kto:1']); self.assertEqual(r['kto:1']['k'], 'festival'); self.assertEqual(r['kto:1']['cc'], 'KR')

    def test_taiwan_rows(self):
        ev = {'Events': [{'EventID': 'E1', 'EventName': '台北燈節', 'StartDateTime': '2027-02-01T00:00:00+08:00', 'EndDateTime': '2027-02-10T00:00:00+08:00',
                          'PositionLat': 25.03, 'PositionLon': 121.56, 'PostalAddress': {'City': '臺北市', 'Town': '信義區'}, 'EventStatus': 'EventScheduled'},
                         {'EventID': 'E2', 'EventName': '取消', 'StartDateTime': '2027-01-01', 'EndDateTime': '2027-01-02', 'EventStatus': 'EventCancelled'}]}
        b = io.BytesIO()
        with zipfile.ZipFile(b, 'w') as z:
            z.writestr('EventList.json', json.dumps(ev, ensure_ascii=False))
        r = EV.taiwan_rows(b.getvalue(), now=self.NOW)
        self.assertEqual(list(r), ['tw:E1']); self.assertEqual(r['tw:E1']['a'], '臺北市信義區'); self.assertEqual(r['tw:E1']['k'], 'festival')

    def test_official_rows_popup_and_publish(self):
        rows = [{'wd': 'Q5', 'lat': 37.54, 'lon': 127.05, 'extra': {'cc': 'KR', 'ev': [{'name': 'Brand X Pop-up', 'start': '2026-11-25', 'end': '2026-12-07', 'page': 'https://brand.example/news'}]}}]
        r = EV.official_rows(rows, now=self.NOW)
        v = r['off:Q5:0']
        self.assertEqual((v['k'], v['u'], v['la']), ('popup', 'https://brand.example/news', 37.54))
        with tempfile.TemporaryDirectory() as d:
            fn = os.path.join(d, 'events_official.json.gz')
            EV.write(fn, EV.doc('', 'official', r))
            e = PL.describe(fn)
            self.assertEqual(e['kind'], 'events'); self.assertEqual(e['bbox'], [127.05, 37.54, 127.05, 37.54])


class FakeCl:
    def __init__(self, items):
        self.items, self.calls = items, []

    def get(self, op, **q):
        self.calls.append((op, q))
        return {'totalCount': len(self.items)}, self.items


class TestFestivalStep(unittest.TestCase):
    def test_once_a_day_and_layer(self):
        far = '20991231'
        items = [{'contentid': 11, 'title': '테스트 축제', 'eventstartdate': '20260101', 'eventenddate': far, 'mapx': '127.0', 'mapy': '37.5', 'cpyrhtDivCd': 'Type2',
                  'firstimage': 'http://img'},
                 {'contentid': 12, 'title': '끝난 축제', 'eventstartdate': '20200101', 'eventenddate': '20200102'}]
        with tempfile.TemporaryDirectory() as d:
            store, st, cl = TD.Store(os.path.join(d, 's')), {}, FakeCl(items)
            self.assertEqual(TD.festivals(st, store, cl), 1)
            self.assertEqual(TD.festivals(st, store, cl), 0)  # 같은 날 두 번 부르지 않음
            self.assertEqual(len(cl.calls), 1); self.assertEqual(cl.calls[0][0], 'searchFestival2')
            fest = json.load(open(os.path.join(d, 's', 'festivals.json')))
            self.assertNotIn('photo', fest['11'])  # 공공누리 2유형 사진은 버림
            self.assertEqual(TD.build_events(store, os.path.join(d, 'L')), 1)
            j = json.loads(gzip.decompress(open(os.path.join(d, 'L', 'events_kto.json.gz'), 'rb').read()))
            self.assertEqual(j['schema'], 'events-1'); self.assertEqual(j['rows']['kto:11']['s'], '20260101')


class TestBrandMentions(unittest.TestCase):
    def test_counts_only_qids(self):
        g = Gazetteer()
        g.add({'nm': 'Don Quijote', 'brand': 'Q100'})
        g.add({'nm': 'ドン・キホーテ', 'brand': 'Q100'})
        txt = 'Day 1\n09:00 Senso-ji\n12:00 lunch ramen\n15:00 Don Quijote Asakusa\n18:00 dinner\nDay 2\n10:00 Meiji Jingu\n13:00 lunch\n16:00 ドン・キホーテ 渋谷店\n19:00 dinner'
        r = PP.parse_text(txt, g)
        self.assertEqual(sum((d.get('brands') or {}).get('Q100', 0) for d in r['days']), 2)
        pat = ST.aggregate([{'city': 'tokyo', 'cc': 'JP', 'type': 'any', 'days': r['days']}], 'clean', [], {})
        self.assertEqual(pat['groups']['cc']['JP']['all']['brand_n'], {'Q100': 2})
        self.assertNotIn('Don Quijote', json.dumps(pat, ensure_ascii=False))  # 이름은 남지 않는다


if __name__ == '__main__':
    unittest.main()
