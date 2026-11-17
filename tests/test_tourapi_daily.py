"""tourapi_daily 모의 시험(키·네트워크 없음): 이어받기·하루 한도·실패 처리·사진 유형·층 파일.  python3 -m unittest tests/test_tourapi_daily.py"""
import gzip, json, os, sys, tempfile, unittest, urllib.error, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts'))
import tourapi_daily as T  # noqa: E402

SIZES = {('11', '12'): 2500, ('50', '12'): 300, ('11', '39'): 200, ('47', '39'): 100, ('36110', '32'): 3, ('52', '28'): 2}  # 나머지 90개 작업은 0곳
PRIO = {'111200007', '111200008', '111200009', '111200010', '111200011'}  # 앱 데이터에 이미 있는 곳(서울 관광지)
CPY = ['Type1', 'Type3', 'Type2', 'Type4', '']


class Fake:
    """KorService2 흉내. bad = 늘 실패하는 cid, flaky = 첫 1번만 실패하는 cid."""
    def __init__(self, bad=(), flaky=(), mode=None):
        self.n = {}; self.bad = set(bad); self.flaky = set(flaky); self.mode = mode; self.seen_flaky = set(); self.urls = 0

    def __call__(self, u):
        self.urls += 1
        op = u.split('KorService2/')[1].split('?')[0]
        q = dict(urllib.parse.parse_qsl(u.split('?', 1)[1]))
        assert q['serviceKey'] == 'TESTKEY'
        self.n[op] = self.n.get(op, 0) + 1
        if self.mode == 'quota':
            return b'<OpenAPI_ServiceResponse><cmmMsgHeader><returnAuthMsg>LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR</returnAuthMsg><returnReasonCode>22</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>'
        if self.mode == '401':
            raise urllib.error.HTTPError(u, 401, 'Unauthorized', {}, None)
        if self.mode == 'down':
            raise OSError('down')
        if op == 'areaBasedList2':
            assert 'areaCode' not in q; a, ct = q['lDongRegnCd'], q['contentTypeId']; N = SIZES.get((a, ct), 0)
            rows, page = int(q['numOfRows']), int(q['pageNo'])
            ids = range((page - 1) * rows, min(page * rows, N))
            items = [{'contentid': f'{a}{ct}{i:05d}', 'contenttypeid': ct, 'title': f'곳{i}', 'lDongRegnCd': a, 'lDongSignguCd': str(110 + i % 25), 'areacode': '' if i % 3 else '1', 'sigungucode': '',
                      'addr1': '어딘가', 'mapx': '127.0', 'mapy': '37.5', 'cpyrhtDivCd': CPY[i % 5],
                      'firstimage': f'http://tong.visitkorea.or.kr/{i}.jpg', 'firstimage2': f'http://tong.visitkorea.or.kr/{i}s.jpg', 'lclsSystm3': 'VE010100'} for i in ids]
            return json.dumps({'response': {'header': {'resultCode': '0000'}, 'body': {'totalCount': N, 'items': {'item': items} if items else ''}}}).encode()
        cid = q['contentId']
        if cid in self.bad or (cid in self.flaky and cid not in self.seen_flaky):
            self.seen_flaky.add(cid)
            raise OSError('timeout')
        if op == 'detailCommon2':  # (11/17) 소개
            assert set(q) == {'serviceKey', 'MobileOS', 'MobileApp', '_type', 'contentId'}
            it = [{'contentid': cid, 'overview': f'곳{cid} 은 바다가 보이는 언덕이다.<br>둘째 문장이다.'}]
            return json.dumps({'response': {'header': {'resultCode': '0000'}, 'body': {'items': {'item': it}}}}).encode()
        if op == 'detailIntro2':
            it = {'usetime': '09:00~18:00', 'restdate': '매주 월요일', 'parking': '가능', 'opentimefood': '11:00-21:00', 'restdatefood': '연중무휴',
                  'firstmenu': '국밥', 'checkintime': '15:00', 'checkouttime': '11:00', 'usefeeleports': '이용요금 어른 10,000원'}
        else:
            it = [{'infoname': '입장료', 'infotext': '어른 3,000원'}]
        return json.dumps({'response': {'header': {'resultCode': '0000'}, 'body': {'items': {'item': it}}}}).encode()


def day(d):
    T.today = lambda: d


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.store = os.path.join(self.tmp, 'store'); day('2026-11-16')

    def go(self, fake, limit, state=None):
        st = state if state is not None else {}
        s = T.Store(self.store)
        stop = T.run(st, s, T.Client('TESTKEY', st, limit, opener=fake, sleep=lambda x: None), prio=PRIO)
        return st, stop, T.Store(self.store)


ALL = sum(SIZES.values())


def cards(s):
    return [c for a in T.AREAS for c in s.area(a).values()]


class TestPhases(Base):
    def test_day1_lists_whole_country_then_details_by_priority(self):
        f = Fake(); st, stop, s = self.go(f, 900)
        self.assertEqual(f.n['areaBasedList2'], 96 + 2)  # 96개 작업 + 서울 관광지 2·3쪽(1000곳/쪽)
        self.assertEqual(stop, 'LIMIT'); self.assertEqual(st['calls'], 900); self.assertEqual(st['phase'], 'detail')
        C = cards(s); self.assertEqual(len(C), ALL)  # 첫날 전국 장소 전부
        self.assertTrue(all('lat' in c and c['cat'] and c['area'] and c['sigungu'] for c in C))  # areacode 가 빈 곳도 빠지지 않음
        self.assertEqual(set(st['totals']), {f'{a}-{t}' for a, t in T.tasks()})
        self.assertEqual(f.n['detailIntro2'], 900 - 98); self.assertNotIn('detailInfo2', f.n)
        done = {c['cid'] for c in C if c.get('dpass')}
        self.assertTrue(PRIO <= done)  # 1순위: 앱 데이터에 있는 곳
        small = {c['cid'] for c in C if c['area'] in ('50', '47')}
        self.assertTrue(small <= done)  # 2순위: 서울·부산 외 관광지·음식점
        self.assertEqual(len(done & {c['cid'] for c in C if c['area'] == '11'}), (900 - 98) - 400)  # 나머지가 서울
        self.assertFalse(any(c.get('dpass') for c in C if c['type'] in ('32', '28')))  # 숙박·레포츠는 12·39 뒤
        L = os.path.join(self.tmp, 'layers'); n = T.build_outputs(s, L, os.path.join(self.tmp, 'agg.json')); self.assertEqual(n, ALL)

    def test_resume_no_duplicates_until_pass_done(self):
        f = Fake(); st, _, s = self.go(f, 900)
        f2 = Fake(); st, stop, _ = self.go(f2, 900, st); self.assertEqual(f2.urls, 0); self.assertEqual(stop, 'LIMIT')  # 같은 날 다시: 0회
        intro, info = f.n['detailIntro2'], 0
        for i in range(2, 9):
            day(f'2026-11-{15 + i}'); g = Fake(); st, stop, s = self.go(g, 900, st)
            intro += g.n.get('detailIntro2', 0); info += g.n.get('detailInfo2', 0)
            if st.get('completed_passes'):
                break
        self.assertEqual(st['completed_passes'], 1); self.assertEqual(st['pass'], 2)
        p2 = sum(1 for c in cards(s) if c.get('dpass') == 2)  # 한 바퀴 끝난 날 남은 한도로 2회차(갱신)를 시작한 몫
        self.assertEqual(intro - p2, ALL)  # 1회차: 장소마다 소개 1번(중복 0)
        self.assertEqual(info, 2500 + 300 + 2)  # 요금은 12·28 만
        self.assertTrue(all(c['dpass'] >= 1 and (c['type'] not in ('12', '39') or c['f'].get('open_ranges')) for c in cards(s)))
        self.assertLess(len(json.dumps(st)), 8000)

    def test_state_json_roundtrip(self):
        st, _, _ = self.go(Fake(), 50)
        self.assertEqual((st['phase'], st['task']), ('list', 48))  # 50회 = 서울 관광지 3쪽 + 작업 47개
        st = json.loads(json.dumps(st)); day('2026-11-17'); f = Fake(); st, _, s = self.go(f, 900, st)
        self.assertEqual(f.n['areaBasedList2'], 96 + 2 - 50); self.assertEqual(len(cards(s)), ALL)


class TestFailures(Base):
    def test_bad_place_three_strikes_flaky_recovers(self):
        f = Fake(bad={'111200007'}, flaky={'111200008'}); st, stop, s = self.go(f, 900)
        c = s.area('11')['111200007']; self.assertNotIn('dpass', c); self.assertEqual(c['dfail'], [1, 1])  # 한 바퀴에 1번만 시도
        self.assertEqual(s.area('11')['111200008']['dpass'], 1)
        ok = len([x for x in cards(s) if x.get('dpass')])
        self.assertEqual(f.n['detailIntro2'], ok + 1 + 4)  # 일시 실패 1 + 끝내 실패 4시도
        for i in range(2, 9):  # 다음 날들에 다시 시도, 3번 실패하면 건너뛰고 회차를 끝낸다
            day(f'2026-11-{15 + i}'); st, stop, s = self.go(Fake(bad={'111200007'}), 900, st)
            if st.get('completed_passes'):
                break
        self.assertEqual(st['completed_passes'], 1)  # 끝내 실패하는 곳이 있어도 회차는 끝난다
        c = s.area('11')['111200007']; self.assertNotIn('dpass', c)
        self.assertTrue(c['dfail'] == [1, 3] or c['dfail'][0] == 2)  # 1회차 3번 실패 뒤 건너뜀(같은 날 2회차가 시작됐으면 [2, n])

    def test_quota_stops_at_once(self):
        f = Fake(mode='quota'); st, stop, _ = self.go(f, 900); self.assertEqual(stop, 'QUOTA'); self.assertEqual(f.urls, 1)

    def test_bad_key_stops(self):
        f = Fake(mode='401'); st, stop, _ = self.go(f, 900); self.assertEqual(stop, 'KEY'); self.assertEqual(f.urls, 1)

    def test_server_down_list_fail(self):
        f = Fake(mode='down'); st, stop, _ = self.go(f, 900)
        self.assertEqual(stop, 'LISTFAIL'); self.assertEqual(f.urls, 4); self.assertEqual(st['task'], 0)

    def test_twenty_consecutive_fails(self):
        f = Fake(bad={f'5012{i:05d}' for i in range(5)}); st, stop, _ = self.go(f, 900)  # 11/15: 제주·경주가 큐 맨 앞(front_tier 0)
        self.assertEqual(stop, 'FAIL20'); self.assertEqual(st['calls'], 98 + 4 + 20)  # 경주(47·시군구 130) 음식점 4곳이 먼저 성공 → 제주 5곳에서 20회 연속 실패


class TestFront(unittest.TestCase):
    def test_front_tier(self):  # 11/15: 경주·제주 → 앱에 있는 소도시 → 앱에 있는 서울·부산 → 그 밖
        self.assertEqual(T.front_tier('50', {}, False), 0)
        self.assertEqual(T.front_tier('47', {'sigungu': '130'}, False), 0)
        self.assertEqual(T.front_tier('47', {'sigungu': '2', 'areacode_old': '35'}, False), 0)
        self.assertEqual(T.front_tier('47', {'sigungu': '170', 'addr': '경상북도 안동시'}, True), 1)
        self.assertEqual(T.front_tier('11', {}, True), 2)
        self.assertEqual(T.front_tier('47', {'sigungu': '170'}, False), 3)
        self.assertEqual(T.front_tier('26', {}, False), 4)


class TestOutputs(Base):
    def test_photos_and_layers(self):
        st, _, s = self.go(Fake(), 420)  # 11/15: 제주 관광지 300곳이 먼저 → 서울 PRIO 까지 닿게 한도를 늘림
        rows = s.area('11')
        for c in rows.values():
            if c['cpy'] in ('Type1', 'Type3'):
                self.assertEqual(c['photo']['license'], 'KOGL-1' if c['cpy'] == 'Type1' else 'KOGL-3')
                self.assertEqual(c['photo'].get('no_modify', False), c['cpy'] == 'Type3')
            else:
                self.assertNotIn('photo', c)
        c = rows['111200007']
        self.assertEqual(c['f']['open_ranges'], [[540, 1080]]); self.assertEqual(c['f']['closed_days'], [0])
        self.assertEqual(T.normalize('12', None, [{'infoname': '입장료', 'infotext': '어른 3,000원'}])['fees_krw'], [['입장료/어른', 3000]])
        L = os.path.join(self.tmp, 'layers'); A = os.path.join(self.tmp, 'agg.json')
        n = T.build_outputs(s, L, A); self.assertEqual(n, ALL)
        doc = json.loads(gzip.decompress(open(os.path.join(L, 'kto_11.json.gz'), 'rb').read()))
        self.assertEqual(doc['schema'], 'tourapi-1'); self.assertIn('한국관광공사', doc['attribution'])
        agg = json.load(open(A)); k = next(x for x in agg if x.startswith('kto:'))
        self.assertEqual(agg[k]['type'], '관광지'); self.assertIn('한국관광공사', agg['_source'])
        sys.path.insert(0, os.path.join(HERE, '..', 'scripts')); import publish_layers as P
        e = P.describe(os.path.join(L, 'kto_11.json.gz'))
        self.assertEqual(e['kind'], 'tourapi'); self.assertEqual(e['n'], len(rows)); self.assertIn('bbox', e); self.assertEqual(e['license'], 'KOGL')

    def test_overview_type1_share(self):  # 11/17: 소개는 제1유형 관광지·문화시설만, 하루 한도의 몫까지, 제주·경주 먼저
        f = Fake(); st, _, s = self.go(f, 900)
        self.assertNotIn('detailCommon2', f.n)  # 첫날(목록 받는 날)·ov_share 0 은 소개를 받지 않음
        day('2026-11-17'); f2 = Fake()
        st2 = dict(st); stt = T.Store(self.store)
        stop = T.run(st2, stt, T.Client('TESTKEY', st2, 400, opener=f2, sleep=lambda x: None), prio=PRIO, ov_share=0.25)
        self.assertEqual(stop, 'LIMIT'); self.assertEqual(f2.n['detailCommon2'], 100); self.assertEqual(st2['ov']['calls'], 100)
        C = cards(T.Store(self.store))
        got = [c for c in C if c.get('opass')]
        self.assertEqual(len(got), 100)
        self.assertTrue(all(c['cpy'] == 'Type1' and c['type'] in T.OV_TYPES for c in got))
        self.assertTrue({c['cid'] for c in C if c['area'] == '50' and c['cpy'] == 'Type1'} <= {c['cid'] for c in got})  # 제주 먼저
        self.assertTrue(all(c['ov'].endswith('언덕이다.') and '<br>' not in c['ov'] for c in got))
        L = os.path.join(self.tmp, 'layers'); T.build_outputs(T.Store(self.store), L, os.path.join(self.tmp, 'agg.json'))
        rows = {}
        for fn in os.listdir(L):
            rows.update(json.loads(gzip.decompress(open(os.path.join(L, fn), 'rb').read()))['rows'])
        self.assertEqual(sum(1 for r in rows.values() if r.get('ov')), 100)

    def test_types_normalize(self):
        f = T.normalize('28', [{'usetimeleports': '10:00~17:00', 'usefeeleports': '어른 10,000원', 'restdateleports': '연중무휴'}], [])
        self.assertEqual(f['open_ranges'], [[600, 1020]]); self.assertEqual(f['closed_days'], []); self.assertEqual(f['fees_krw'], [['이용요금/어른', 10000]])
        f = T.normalize('39', [{'opentimefood': '11:00-21:00', 'kidsfacility': '1'}], None); self.assertTrue(f['kids_facility'])
        f = T.normalize('32', [{'checkintime': '15:00', 'checkouttime': '11:00'}], None); self.assertEqual((f['checkin'], f['checkout']), (900, 660))


if __name__ == '__main__':
    unittest.main()
