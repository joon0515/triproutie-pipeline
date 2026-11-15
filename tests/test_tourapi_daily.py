"""tourapi_daily 모의 시험(키·네트워크 없음): 이어받기·하루 한도·실패 처리·사진 유형·층 파일.  python3 -m unittest tests/test_tourapi_daily.py"""
import gzip, json, os, sys, tempfile, unittest, urllib.error, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts'))
import tourapi_daily as T  # noqa: E402

SIZES = {('1', '12'): 1203, ('39', '39'): 7, ('8', '32'): 3, ('37', '28'): 2}  # 나머지 작업은 0곳
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
            a, ct = q['areaCode'], q['contentTypeId']; N = SIZES.get((a, ct), 0)
            rows, page = int(q['numOfRows']), int(q['pageNo'])
            ids = range((page - 1) * rows, min(page * rows, N))
            items = [{'contentid': f'{a}{ct}{i:05d}', 'contenttypeid': ct, 'title': f'곳{i}', 'areacode': a, 'sigungucode': str(i % 25 + 1),
                      'addr1': '어딘가', 'mapx': '127.0', 'mapy': '37.5', 'cpyrhtDivCd': CPY[i % 5],
                      'firstimage': f'http://tong.visitkorea.or.kr/{i}.jpg', 'firstimage2': f'http://tong.visitkorea.or.kr/{i}s.jpg'} for i in ids]
            return json.dumps({'response': {'header': {'resultCode': '0000'}, 'body': {'totalCount': N, 'items': {'item': items} if items else ''}}}).encode()
        cid = q['contentId']
        if cid in self.bad or (cid in self.flaky and cid not in self.seen_flaky):
            self.seen_flaky.add(cid)
            raise OSError('timeout')
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
        stop = T.run(st, s, T.Client('TESTKEY', st, limit, opener=fake, sleep=lambda x: None))
        return st, stop, T.Store(self.store)


class TestResume(Base):
    def test_limit_and_resume_without_duplicates(self):
        f = Fake(); st, stop, s = self.go(f, 900)
        self.assertEqual(stop, 'LIMIT'); self.assertLessEqual(st['calls'], 900); self.assertGreaterEqual(st['calls'], 898)
        first = len(s.area('1')); self.assertGreater(first, 400)
        # 같은 날 다시: 한 번도 부르지 않는다
        f2 = Fake(); st, stop, _ = self.go(f2, 900, st); self.assertEqual(f2.urls, 0); self.assertEqual(stop, 'LIMIT')
        # 다음 날들: 이어서, 이미 받은 곳 상세는 다시 안 부른다
        total_detail = f.n.get('detailIntro2', 0)
        for d in ('2026-11-17', '2026-11-18', '2026-11-19'):
            day(d); f3 = Fake(); st, stop, s = self.go(f3, 900, st); total_detail += f3.n.get('detailIntro2', 0)
            if st.get('completed_passes'):
                break
        self.assertEqual(len(s.area('1')), 1203)
        self.assertEqual(len(s.area('39')) + len(s.area('8')) + len(s.area('37')), 12)
        p2 = sum(1 for a in T.AREAS for c in s.area(a).values() if c['pass'] == 2)  # 한 바퀴 끝난 날 남은 호출로 2회차(갱신)를 시작한 몫
        self.assertEqual(total_detail - p2, 1203 + 12)  # 1회차: 장소마다 소개 1번씩(중복 0)
        self.assertGreaterEqual(st.get('completed_passes', 0), 1)
        self.assertEqual(set(st['totals']), {f'{a}-{t}' for a, t in T.tasks()})  # 17시도×6종류 모두 목록 확인
        self.assertLess(len(json.dumps(st)), 8000)  # 커밋하는 상태 파일은 작다

    def test_state_file_roundtrip(self):
        st, _, _ = self.go(Fake(), 100)
        st2 = json.loads(json.dumps(st)); day('2026-11-17'); f = Fake(); st2, _, s = self.go(f, 100, st2)
        self.assertEqual(f.n['areaBasedList2'], 1)  # 같은 쪽을 다시 읽고, 받은 49곳은 건너뜀
        self.assertEqual(f.n['detailIntro2'], 49); self.assertEqual(len(s.area('1')), 98)


class TestFailures(Base):
    def test_bad_place_goes_to_retry_flaky_recovers(self):
        f = Fake(bad={'11200003'}, flaky={'11200005'}); st, stop, s = self.go(f, 300)
        self.assertNotIn('11200003', s.area('1')); self.assertIn('11200005', s.area('1'))
        self.assertEqual([r['x']['contentid'] for r in st['retry']], ['11200003'])
        day('2026-11-17'); st, _, s = self.go(Fake(), 300, st)  # 다음 날 먼저 다시 받는다
        self.assertIn('11200003', s.area('1')); self.assertEqual(st['retry'], [])

    def test_quota_stops_at_once(self):
        f = Fake(mode='quota'); st, stop, _ = self.go(f, 900); self.assertEqual(stop, 'QUOTA'); self.assertEqual(f.urls, 1)

    def test_bad_key_stops(self):
        f = Fake(mode='401'); st, stop, _ = self.go(f, 900); self.assertEqual(stop, 'KEY'); self.assertEqual(f.urls, 1)

    def test_server_down_list_fail(self):
        f = Fake(mode='down'); st, stop, _ = self.go(f, 900)
        self.assertEqual(stop, 'LISTFAIL'); self.assertEqual(f.urls, 4); self.assertEqual(st['task'], 0)

    def test_twenty_consecutive_fails(self):
        f = Fake(bad={f'112{i:05d}' for i in range(30)}); st, stop, _ = self.go(f, 900)
        self.assertEqual(stop, 'FAIL20'); self.assertLess(st['calls'], 30)


class TestOutputs(Base):
    def test_photos_and_layers(self):
        st, _, s = self.go(Fake(), 60)
        rows = s.area('1')
        for c in rows.values():
            if c['cpy'] in ('Type1', 'Type3'):
                self.assertEqual(c['photo']['license'], 'KOGL-1' if c['cpy'] == 'Type1' else 'KOGL-3')
                self.assertEqual(c['photo'].get('no_modify', False), c['cpy'] == 'Type3')
            else:
                self.assertNotIn('photo', c)
        c = next(iter(rows.values()))
        self.assertEqual(c['f']['open_ranges'], [[540, 1080]]); self.assertEqual(c['f']['closed_days'], [0]); self.assertEqual(c['f']['fees_krw'], [['입장료/어른', 3000]])
        L = os.path.join(self.tmp, 'layers'); A = os.path.join(self.tmp, 'agg.json')
        n = T.build_outputs(s, L, A); self.assertEqual(n, len(rows))
        doc = json.loads(gzip.decompress(open(os.path.join(L, 'kto_1.json.gz'), 'rb').read()))
        self.assertEqual(doc['schema'], 'tourapi-1'); self.assertIn('한국관광공사', doc['attribution'])
        agg = json.load(open(A)); k = next(x for x in agg if x.startswith('kto:'))
        self.assertEqual(agg[k]['type'], '관광지'); self.assertIn('한국관광공사', agg['_source'])
        sys.path.insert(0, os.path.join(HERE, '..', 'scripts')); import publish_layers as P
        e = P.describe(os.path.join(L, 'kto_1.json.gz'))
        self.assertEqual(e['kind'], 'tourapi'); self.assertEqual(e['n'], len(rows)); self.assertIn('bbox', e); self.assertEqual(e['license'], 'KOGL')

    def test_types_normalize(self):
        f = T.normalize('28', [{'usetimeleports': '10:00~17:00', 'usefeeleports': '어른 10,000원', 'restdateleports': '연중무휴'}], [])
        self.assertEqual(f['open_ranges'], [[600, 1020]]); self.assertEqual(f['closed_days'], []); self.assertEqual(f['fees_krw'], [['이용요금/어른', 10000]])
        f = T.normalize('39', [{'opentimefood': '11:00-21:00', 'kidsfacility': '1'}], None); self.assertTrue(f['kids_facility'])
        f = T.normalize('32', [{'checkintime': '15:00', 'checkouttime': '11:00'}], None); self.assertEqual((f['checkin'], f['checkout']), (900, 660))


if __name__ == '__main__':
    unittest.main()
