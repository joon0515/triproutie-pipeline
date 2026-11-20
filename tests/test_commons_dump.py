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
        self.assertIn('| 1 | jeju | 0 | 0 | 3 | 1 | 33.3% | 1/3 | 0 | 0.0% | 0/0 |', L)

    def test_city_items_skip_qlever(self):
        import photo_pri as PP
        cs = {}
        rows, files = PP.city_rows([{'key': 'paris', 'bbox': [2.1, 48.7, 2.6, 49.0]}], cs, {}, None, 0, items={'paris': {'q': 'Q90', 'p': WD.FILEPATH + 'P.jpg'}}, now_day='2026-11-20')
        self.assertEqual((cs['paris']['q'], files), ('Q90', [WD.FILEPATH + 'P.jpg']))  # 판정 전 → 판정할 파일로
        rows, _ = PP.city_rows([{'key': 'paris', 'bbox': [2.1, 48.7, 2.6, 49.0]}], cs, {WD.FILEPATH + 'P.jpg': ['2026-11-17', {'lic': 'Q18199165', 'by': 'A'}]}, None, 0,
                               items={'paris': {'q': 'Q90', 'p': WD.FILEPATH + 'P.jpg'}}, now_day='2026-11-20')
        self.assertEqual(rows['city:paris']['photo_license'], 'CC BY-SA 4.0')


    def test_geo_match_and_false_positives(self):
        import photo_pri as PP
        box = [135.35, 34.55, 135.65, 34.8]
        o = 100 / 111320.0
        items = [['Q1', 34.6873, 135.5259, 80, WD.FILEPATH + 'Castle.jpg', ['Osaka Castle', '大阪城']],
                 ['Q2', 34.6600, 135.5000, 5, WD.FILEPATH + 'Park.jpg', ['公園']],
                 ['Q3', 34.7000, 135.4000, 9, WD.FILEPATH + 'A.jpg', ['Umeda Sky Building']],
                 ['Q4', 34.7000 + 0.2 * o, 135.4000, 9, WD.FILEPATH + 'B.jpg', ['Umeda Sky Building']]]
        d = {'id': ['fsq:castle', 'fsq:far', 'fsq:cafe', 'fsq:park2', 'fsq:twin', 'Q9', 'fsq:short'],
             '_lat': [34.6875, 34.6873 + 1.5 * o, 34.6873, 34.6601, 34.7001, 34.6873, 34.6873],
             '_lon': [135.5259, 135.5259, 135.5260, 135.5000, 135.4000, 135.5259, 135.5259],
             'nm': ['大阪城', 'Osaka Castle', 'Osaka Castle Cafe & Bar Rooftop', '公園', 'Umeda Sky Building', 'Osaka Castle', '大阪'],
             'en': [None, None, None, None, None, None, None]}
        got = PP.geo_match([d], items, box)
        self.assertEqual(got.get('fsq:castle'), ('Q1', WD.FILEPATH + 'Castle.jpg'))  # 다국어 별칭 · 30m
        self.assertNotIn('fsq:far', got)      # 150m — 거리 넘음
        self.assertNotIn('fsq:cafe', got)     # 이름이 길게 다름(닮음 < 0.8) — 가게를 명소로 잘못 붙이지 않음
        self.assertIn('fsq:park2', got)       # 같은 이름 · 가까움
        self.assertNotIn('fsq:twin', got)     # 같은 점수의 서로 다른 항목 두 개 → 버림
        self.assertNotIn('Q9', got)           # QID 행은 P18 길 몫
        self.assertNotIn('fsq:short', got)    # '大阪' ⊂ '大阪城' 이지만 짧은 쪽 2글자 → 0

    def test_city_fallback_files(self):
        import photo_pri as PP
        a, b = WD.FILEPATH + 'NonFree.jpg', WD.FILEPATH + 'Banner.jpg'
        it = {'paris': {'q': 'Q90', 'p': a, 'files': [a, b]}}
        cc = {a: ['2026-11-17', {}], b: ['2026-11-17', {'lic': 'Q6938433', 'by': 'B'}]}
        rows, files = PP.city_rows([{'key': 'paris', 'bbox': [2.1, 48.7, 2.6, 49.0]}], {}, cc, None, 0, items=it, now_day='2026-11-20')
        self.assertEqual(rows['city:paris']['photo_file'], 'Banner.jpg')
        e = WD.city_entry([400, 'Q90', a, [a, b]], [[5, 'Q7', 'X', ['X']]], {'Q2': ['Q2', 0, 0, 900, 'L', ['l']], 'Q3': ['Q3', 0, 0, 1, 'M', ['m']]})
        self.assertEqual(e['files'], [a, b, 'X', 'L', 'M'])

    def test_city_geo_fallback(self):
        import photo_pri as PP
        it = PP.city_fallback({'okinawa_naha': {'q': 'Q1', 'p': 'a', 'files': ['a', 'b']}},
                              {'okinawa_naha': [['Q5', 0, 0, 3, 'c', ['x']], ['Q6', 0, 0, 90, 'd', ['y']], ['Q7', 0, 0, 1, 'a', ['z']]]})
        self.assertEqual(it['okinawa_naha']['files'], ['a', 'b', 'd', 'c'])  # sitelinks 큰 명소 먼저 · 중복 없음

    def test_sight_only_ids(self):
        import photo_pri as PP
        d = {'id': ['a', 'b', 'c'], '_lat': [34.6, 34.6, 34.6], '_lon': [135.5, 135.5, 135.5], 's': [9, 5, 1], 'cats': ['food', 'sight', 'nature'], 'c': [0, 1, 2]}
        self.assertEqual(PP.city_tile_ids([d], [135.35, 34.55, 135.65, 34.8], sight_only=True), ['b', 'c'])

    def test_pov_match(self):
        o = 1 / 111320.0
        CD.set_targets({'osaka': [['fsq:castle', 34.6873, 135.5259, ['osakacastle', '大阪城'], 'Q1'], ['fsq:park', 34.70, 135.40, ['park'], None],
                                  ['fsq:tower', 34.6525, 135.5063, ['tsutenkaku', '通天閣'], None]]})

        def ent(title, la, lo, lic='Q18199165', an='Kim', dep=None, cap=None):
            st = {'P275': [{'mainsnak': {'datavalue': {'value': {'id': lic}}}}], 'P1259': [{'mainsnak': {'datavalue': {'value': {'latitude': la, 'longitude': lo}}}}]}
            if an:
                st['P170'] = [{'mainsnak': {'snaktype': 'somevalue'}, 'qualifiers': {'P2093': [{'datavalue': {'value': an}}]}}]
            if dep:
                st['P180'] = [{'mainsnak': {'datavalue': {'value': {'id': dep}}}}]
            e = {'type': 'mediainfo', 'id': 'M1', 'title': 'File:' + title, 'statements': st, 'labels': {'en': {'language': 'en', 'value': cap}} if cap else {}}
            return json.dumps(e, ensure_ascii=False).encode() + b','
        sk = CD.Sink(set(), '2026-11-17', False)
        sk.feed(b'\n'.join([
            ent('IMG 0001.jpg', 34.6873 + 30 * o, 135.5259, dep='Q1'),                 # 묘사 대상 일치 · 30m
            ent('Osaka Castle at night.jpg', 34.6873 + 10 * o, 135.5259),              # 이름 일치 · 10m (순위는 묘사 대상이 먼저)
            ent('通天閣 夜景.jpg', 34.6525 + 70 * o, 135.5063),                         # 70m — 거리 넘음
            ent('Tsutenkaku tower.jpg', 34.6525 + 20 * o, 135.5063, lic='Q99'),        # 비자유
            ent('Tsutenkaku 2.jpg', 34.6525 + 25 * o, 135.5063, an=None),             # 저작자 없음
            ent('Some park bench.jpg', 34.70, 135.40),                                # 'park' 4글자 영문 — 포함은 맞지만 아래에서 따로 봄
            ent('Random street.jpg', 34.6525 + 5 * o, 135.5063, cap='a street'),       # 이름 없음 → 안 붙음
        ]) + b'\n')
        pv = sk.out['_pov']
        self.assertEqual((pv['fsq:castle'][0], pv['fsq:castle'][2]), (0, CD.url_of('IMG 0001.jpg')))
        self.assertNotIn('fsq:tower', pv)
        self.assertEqual(pv['fsq:park'][0], 1)
        self.assertTrue(CD.name_hit({'大阪城'}, ['大阪城天守閣'])); self.assertFalse(CD.name_hit({'城'}, ['大阪城'])); self.assertFalse(CD.name_hit({'zoo'}, ['tennojizoo']))
        CD.set_targets({})

    def test_kr_loc(self):
        import photo_pri as PP
        t = ['h:1', 33.4581, 126.9425, sorted(PP.kr_keys('성산일출봉')), None]
        items = [{'contentid': '9', 'contenttypeid': '39', 'title': '성산일출봉 식당', 'firstimage': 'http://tong.visitkorea.or.kr/a.jpg', 'cpyrhtDivCd': 'Type1'},
                 {'contentid': '1', 'contenttypeid': '12', 'title': '성산일출봉 [유네스코 세계자연유산]', 'firstimage': 'http://tong.visitkorea.or.kr/b.jpg', 'cpyrhtDivCd': 'Type3'}]
        r, w = PP.kr_loc_pick(t, items)
        self.assertEqual((w, r['kto_cid'], r['photo_license'], r.get('no_modify')), ('ok', '1', 'KOGL-3', True))
        self.assertTrue(r['photo_url'].startswith('https://'))
        self.assertEqual(PP.kr_loc_pick(t, items[:1])[1], 'no_near:39'); self.assertEqual(PP.kr_loc_pick(t, [])[1], 'no_items')                      # 식당(39) 은 대상 아님
        self.assertEqual(PP.kr_loc_pick(t, [dict(items[1], title='우도')])[1], 'name:ko:<0.2')
        self.assertEqual(PP.kr_loc_pick(t, [dict(items[1], cpyrhtDivCd='Type2')])[1], 'license')
        self.assertEqual(PP.kr_loc_pick(t, [dict(items[1], firstimage='')])[1], 'no_photo')
        calls = []
        def op(req):
            calls.append(1); return {'response': {'header': {'resultCode': '0000'}, 'body': {'items': {'item': items[1]}}}}
        lc = {}
        got, why, n = PP.kr_loc({'jeju': [t]}, {}, lc, 'SECRET', opener=op, now_day='2026-11-20', sleep=lambda x: None)
        got2, _, n2 = PP.kr_loc({'jeju': [t]}, {}, lc, 'SECRET', opener=op, now_day='2026-11-25', sleep=lambda x: None)
        self.assertEqual((list(got), n, n2, len(calls)), (['h:1'], 2, 0, 2))  # 유형 2번 · 30일 캐시
        self.assertNotIn('SECRET', json.dumps([why, lc]))

    def test_kr_loc_stops_on_quota(self):
        import photo_pri as PP, urllib.error
        def op(req):
            raise urllib.error.HTTPError('https://x/?serviceKey=SECRET', 429, 'Too Many', {}, None)
        t = [['h:%d' % i, 33.4, 126.5, ['a'], None] for i in range(5)]
        got, why, n = PP.kr_loc({'jeju': t}, {}, {}, 'SECRET', opener=op, now_day='2026-11-20', sleep=lambda x: None)
        self.assertEqual((n, why.get('stop')), (1, 'err:HTTPError:429'))
        self.assertNotIn('SECRET', json.dumps(why))

    def test_kr_loc_api_error_not_cached(self):
        import photo_pri as PP
        lc = {}
        got, why, n = PP.kr_loc({'jeju': [['h:1', 33.4, 126.5, ['a'], None]]}, {}, lc, 'K', opener=lambda r: {'response': {'header': {'resultCode': '10'}}},
                                now_day='2026-11-20', sleep=lambda x: None)
        self.assertEqual((lc, list(why)), ({}, ['err:KrApiError:10']))

    def test_miss_diag(self):
        import photo_pri as PP
        tg = {'paris': [['a', 48.85, 2.35, ['louvre'], None], ['b', 48.86, 2.30, [], None], ['c', 48.87, 2.31, ['x'], None]]}
        geo = {'paris': [['Q1', 48.85, 2.3501, 5, 'f', ['Musée du Louvre', 'Louvre']]]}
        d = PP.miss_diag({'paris': ['a', 'b', 'c', 'd']}, {'d': {}}, tg, geo)
        self.assertEqual(d['paris']['all'], {'qid_or_geo': 1, 'no_name': 1, 'no_qid_no_geo': 1})

    def test_wiki_boost_ranks_landmarks(self):
        import photo_pri as PP
        d = {'id': ['h:church', 'h:palace', 'h:tower', 'h:cafe'], '_lat': [37.5] * 4, '_lon': [127.0] * 4, 's': [75, 75, 62, 80],
             'cats': ['sight', 'activity', 'food'], 'c': [0, 0, 1, 2]}
        pop = {}
        b = PP.wiki_boost([d], {'h:palace': ('Q1', 'p'), 'h:tower': ('Q2', 'p'), 'h:cafe': ('Q3', 'p')}, {'Q1': 120, 'Q2': 90, 'Q3': 2}, pop)
        self.assertEqual(set(b), {'h:palace', 'h:tower'})  # sitelinks 2 → pop<10 가점 없음
        self.assertGreater(b['h:palace'], 75); self.assertEqual(pop['h:palace']['q'], 'Q1')
        box = [126.7, 37.4, 127.2, 37.7]
        self.assertEqual(PP.city_tile_ids([d], box, sight_only=True, boost=b), ['h:palace', 'h:tower', 'h:church'])  # 활동 분류라도 위키 연결이면 명소
        self.assertEqual(PP.city_tile_ids([d], box, sight_only=True), ['h:church', 'h:palace'])

    def test_geo_item(self):
        WD.city_setup([{'key': 'osaka', 'bbox': [135.35, 34.55, 135.65, 34.8]}])
        ent = {'id': 'Q1', 'labels': {'en': {'value': 'Osaka Castle'}, 'ja': {'value': '大阪城'}}, 'aliases': {'ja': [{'value': '大坂城'}]}, 'sitelinks': {'a': {}},
               'claims': {'P625': [{'mainsnak': {'datavalue': {'value': {'latitude': 34.6873, 'longitude': 135.5259}}}}],
                          'P18': [{'mainsnak': {'datavalue': {'value': 'C.jpg'}}}]}}
        keys, g = WD.geo_item(ent)
        self.assertEqual((keys, g[0], g[4], g[5], g[6]), (['osaka'], 'Q1', WD.FILEPATH + 'C.jpg', ['Osaka Castle', '大坂城', '大阪城'], 0))
        ent['claims']['P1082'] = [{}]
        self.assertEqual(WD.geo_item(ent)[1][6], 1)
        del ent['claims']['P1082']
        ent['claims']['P625'][0]['mainsnak']['datavalue']['value']['latitude'] = 10.0
        self.assertIsNone(WD.geo_item(ent))  # 상자 밖


if __name__ == '__main__':
    unittest.main()
