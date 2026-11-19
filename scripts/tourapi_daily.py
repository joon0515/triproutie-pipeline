#!/usr/bin/env python3
"""한국관광공사 TourAPI 4.0(KorService2) 전국 수집 — 하루 한도 안에서 이어받기. (2026-11-15, 대화 세션 collect.py 를 옮긴 것)

    TOURAPI_KEY=... python3 scripts/tourapi_daily.py --state tourapi/state.json --store W/store --layers W/layers --agg W/agg_tourapi.json

범위: 16개 시도(법정동 lDongRegnCd — 광주·전남 통합 뒤) 전부 × 관광지 12·문화시설 14·레포츠 28·숙박 32·쇼핑 38·음식점 39. 시도 단위 목록에 그 시도의 모든
  시군구가 들어 있고 장소마다 lDongSignguCd 를 저장한다(전국 269개 시군구, 소도시 포함).
단계(11/15 변경 — 전국 장소를 첫날 앱에 넣기 위해):
  1) list   : 96개 작업(시도×종류)의 areaBasedList2 를 1000곳/쪽으로 전부 받는다(이름·주소·좌표·대표사진·분류). 약 130회 → 첫날 끝.
  2) detail : 남은 한도로 detailIntro2(영업시간·휴무·주차·메뉴·체크인…). 순서 = 관광지12·음식점39 → 숙박32 → 문화14·레포츠28·쇼핑38,
              같은 종류 안에서는 앱 데이터에 이미 있는 곳(tourapi/priority_cids.json) → 서울·부산 외(소도시) → 서울·부산.
  0) festival: (11/20 256기) 하루 한 번 searchFestival2 → store/festivals.json → 층 events_kto.json.gz(schema events-1). 2~3회/일.
  3) info   : 요금이 detailInfo2 에만 있는 12·28 의 요금. 끝나면 회차(pass)+1, 1)부터 다시(내용 갱신).
  (11/17) 소개: 목록을 한 번 다 받은 뒤부터 매일 한도의 --ov-share(기본 25%)를 먼저 detailCommon2 overview 에 쓴다. 관광지 12·문화시설 14 중
              cpyrhtDivCd 가 Type1(공공누리 제1유형)인 곳만, 제주·경주 → 앱에 있는 곳 → 그 밖 순. overview 의 첫 문장만(원문 그대로, 120자 넘으면 버림)
              카드 ov 에 두고 층 행에도 ov 로 싣는다 → scripts/place_desc.py 가 desc-kto(ko 칸)로 옮긴다. 제3유형(변경 금지)·그 밖은 받지 않는다.
이어받기: 상태 파일(--state, 저장소에 커밋하는 작은 JSON)에는 단계·작업 번호·쪽·오늘 호출 수만. 장소별 진행(받은 회차·실패 횟수)은
  누적본(--store, GCS 와 동기화)의 카드에 있어 같은 곳을 두 번 부르지 않는다.
한도: KST 날짜별 호출 수 ≤ --limit(기본 900, 개발계정). 한도 초과 응답(LIMITED_NUMBER…)·429 면 그날 즉시 멈춤.
실패: 호출당 4번까지 지수 대기, 연속 20회 실패면 그날 중단(종료코드 2). 키 오류(401·403·미등록)도 2. 장소 하나가 끝내 실패하면
  카드에 실패 횟수를 적고 다음 곳으로(3번 실패한 곳은 이번 회차에서 건너뜀).
출력(장소 내용은 로그에 쓰지 않음 — 개수만):
  --store  area_<시도>.json   {cid: 장소 카드}  (누적, GCS gs://<bucket>/tourapi/v1/store/ 와 동기화)
  --layers kto_<시도>.json.gz schema 'tourapi-1' — places-layers finalize 가 layers_extra/ 에서 집어 목록에 싣는다
  --agg    agg_tourapi.json   앱 데이터용(대화 세션 판과 같은 모양: 'kto:<cid>' → name·city·tourapi_cid·type·cpy·사실 필드)
사진: firstimage 는 공공누리 제1유형(출처표시)·제3유형(출처표시+변경금지)만 저장. 3유형은 no_modify=true(자르기·보정 금지).
  그 밖(2·4유형·미표기)은 사진을 버리고 사실 필드만 둔다. 출처 '한국관광공사 TourAPI 4.0' 은 파일마다 attribution 으로 싣는다.
키: 환경변수 TOURAPI_KEY 에서만 읽는다(Actions secret DATA_GO_KR_KEY). 주소에 키가 들어가므로 URL·응답 본문은 절대 출력하지 않는다.
"""
import argparse, datetime, gzip, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

BASE = os.environ.get('TOURAPI_BASE', 'https://apis.data.go.kr/B551011/KorService2/')
KST = datetime.timezone(datetime.timedelta(hours=9))
# 시도 = 법정동 시도 코드(lDongRegnCd). 옛 areaCode 는 TourAPI 자료의 약 2/3(새로 등록·갱신된 곳)에 비어 있어 그것으로 거르면 빠진다
# (11/15 로컬 자료 28,913곳 중 19,187곳 areacode 없음). 광주·전남은 통합(12). 시군구 = lDongSignguCd(전국 269개).
AREAS = ['11', '26', '27', '28', '12', '30', '31', '36110', '41', '51', '43', '44', '52', '47', '48', '50']  # 16개 시도(통합 뒤)
AREA_KO = {'11': '서울', '26': '부산', '27': '대구', '28': '인천', '12': '전남광주', '30': '대전', '31': '울산', '36110': '세종', '41': '경기',
           '51': '강원', '43': '충북', '44': '충남', '52': '전북', '47': '경북', '48': '경남', '50': '제주'}
TYPES = ['12', '39', '32', '14', '28', '38']  # 앞쪽이 먼저(일정 엔진이 많이 쓰는 순)
TYPE_KO = {'12': '관광지', '14': '문화시설', '28': '레포츠', '32': '숙박', '38': '쇼핑', '39': '음식점'}
NEED_INFO = {'12', '28'}  # 요금이 detailInfo2 에만 있는 종류
ROWS = 1000
PRIO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tourapi', 'priority_cids.json')
BIG = {'11', '26'}  # 서울·부산(대화 세션 판에서 이미 많이 받은 곳) — 상세는 뒤로
DETAIL_ORDER = {'12': 0, '39': 0, '32': 1, '14': 2, '28': 2, '38': 2}
PHOTO_OK = {'Type1': 'KOGL-1', 'Type3': 'KOGL-3'}
ATTR = '한국관광공사 TourAPI 4.0 (공공누리 제1·3유형, 출처표시)'
OV_TYPES = ('12', '14')  # 소개를 받는 종류(관광지·문화시설 — '왜 여기?' 설명이 필요한 볼거리)
OV_SHARE = 0.25


def today():
    return datetime.datetime.now(KST).date().isoformat()


def tasks():
    return [(a, t) for t in TYPES for a in AREAS]  # 종류 우선: 전국 관광지 → 전국 음식점 → …


# ---------- 호출 ----------
class Stop(Exception):
    pass


class Client:
    def __init__(self, key, state, limit, opener=None, sleep=time.sleep, interval=None, clock=time.time):
        self.key, self.st, self.limit, self.sleep = key, state, limit, sleep
        # 출처별 속도 제한(11/18): 요청 사이 최소 간격(초) — 하루 한도(limit)와 따로. 등록부 tourapi.interval_s 와 같은 값(기본 1초 — 수집 원칙 '사이트당 초당 1회 이하').
        self.interval, self.clock, self._last = float(os.environ.get('TOURAPI_INTERVAL_S', 1.0) if interval is None else interval), clock, None
        self.opener = opener or (lambda u: urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'TripRoutie-pipeline/1.0'}), timeout=60).read())
        self.fails = 0
        if state.get('date') != today():
            state['date'], state['calls'] = today(), 0

    def left(self):
        return self.limit - self.st['calls']

    def get(self, op, **q):
        """성공: (body dict, items list). 끝내 실패: None. 그날 멈춰야 하면 Stop."""
        q.update(serviceKey=self.key, MobileOS='ETC', MobileApp='TripRoutie', _type='json')
        u = BASE + op + '?' + urllib.parse.urlencode(q)
        for t in range(4):
            if self.st['calls'] >= self.limit:
                raise Stop('LIMIT')
            self.st['calls'] += 1
            if self._last is not None:
                gap = self.interval - (self.clock() - self._last)
                if gap > 0:
                    self.sleep(gap)
            self._last = self.clock()
            try:
                try:
                    raw = self.opener(u)
                except urllib.error.HTTPError as e:
                    if e.code in (401, 403):
                        raise Stop('KEY')
                    if e.code == 429:
                        raise Stop('QUOTA')
                    raise
                txt = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else raw
                if 'LIMITED_NUMBER_OF_SERVICE_REQUESTS' in txt or 'returnReasonCode>22<' in txt:
                    raise Stop('QUOTA')
                if 'SERVICE_KEY_IS_NOT_REGISTERED' in txt or 'returnReasonCode>30<' in txt:
                    raise Stop('KEY')
                d = json.loads(txt)
                h = d['response'].get('header') or {}
                if h.get('resultCode') not in (None, '0000', '00'):
                    raise ValueError('resultCode')
                body = d['response']['body']
                it = body.get('items') or {}
                it = it.get('item') if isinstance(it, dict) else None
                self.fails = 0
                return body, (it if isinstance(it, list) else ([it] if it else []))
            except Stop:
                raise
            except Exception:
                self.fails += 1
                if self.fails >= 20:
                    raise Stop('FAIL20')
                self.sleep(min(2 ** (t + 1) * 2, 60))
        return None


# ---------- 정규화(norm.py 규칙을 그대로 옮기고 14·28·38 을 더함) ----------
DAY = '월화수목금토일'


def tmin(h, m):
    return int(h) * 60 + int(m)


def ranges(s):
    return [[tmin(a, b), tmin(c, d)] for a, b, c, d in re.findall(r'(\d{1,2}):(\d{2})\s*[~\-–]\s*(\d{1,2}):(\d{2})', s or '')]


def yn(s):
    s = (s or '').strip()
    if not s:
        return None
    if re.match(r'^(가능|있음|유|Y|1|가능함)', s):
        return True
    if re.match(r'^(불가|없음|무|N|0|불가능)', s):
        return False
    return None


def fees_from(pairs):
    fees = []
    for nm, tx in pairs:
        if re.search(r'입장|이용요금|요금|관람료|이용료|입장료|주차', nm):
            for lab, w in re.findall(r'([가-힣A-Za-z]{1,8})\s*[:\s]\s*([\d,]{3,9})\s*원', tx or ''):
                fees.append([nm[:8] + '/' + lab, int(w.replace(',', ''))])
            if re.search(r'무료', tx or '') and not fees:
                fees.append([nm[:8], 0])
    return fees[:12]


# 종류별 소개 필드 이름: (이용시간, 휴무, 주차, 유모차, 반려동물, 카드, 문의, 요금글)
INTRO = {'12': ('usetime', 'restdate', 'parking', 'chkbabycarriage', 'chkpet', 'chkcreditcard', 'infocenter', None),
         '14': ('usetimeculture', 'restdateculture', 'parkingculture', 'chkbabycarriageculture', 'chkpetculture', 'chkcreditcardculture', 'infocenterculture', 'usefee'),
         '28': ('usetimeleports', 'restdateleports', 'parkingleports', 'chkbabycarriageleports', 'chkpetleports', 'chkcreditcardleports', 'infocenterleports', 'usefeeleports'),
         '38': ('opentime', 'restdateshopping', 'parkingshopping', 'chkbabycarriageshopping', 'chkpetshopping', 'chkcreditcardshopping', 'infocentershopping', None)}


def normalize(ct, intro, info):
    f = {}

    def times(s):
        r = ranges(s)
        if r:
            f['open_ranges'] = r
        if len(r) > 1 and re.search(r'\d\s*월|동절기|하절기|계절|\[', s or ''):
            f['hours_seasonal'] = True

    def rest(s):
        if not s:
            return
        if re.search(r'연중\s*무휴|휴무\s*없|없음', s):
            f['closed_days'] = []
            return
        if '째' not in s:
            wd = sorted({DAY.index(c) for c in re.findall(r'(?:매주\s*)?([월화수목금토일])요일', s)})
            if wd:
                f['closed_days'] = wd
        else:
            nth = re.findall(r'(첫|둘|셋|넷|다섯)째\s*(?:주)?\s*,?\s*(?:및\s*)?([월화수목금토일])?', s)
            f['closed_nth'] = [['첫둘셋넷다'.index(a[0]) + 1, DAY.index(b) if b else None] for a, b in nth]
        if re.search(r'공휴일|명절|설날|추석', s):
            f['closed_holiday'] = True

    d = intro[0] if intro else {}
    if ct in INTRO:
        ut, rd, pk, bc, pet, cc, tel, fee = INTRO[ct]
        times(d.get(ut)); rest(d.get(rd))
        for k, name in ((pk, 'parking'), (bc, 'stroller_rental'), (pet, 'pet_ok'), (cc, 'credit_card')):
            b = yn(d.get(k))
            if b is not None:
                f[name] = b
        if d.get(tel):
            f['inquiry_tel'] = d[tel].strip()[:40]
        if ct == '12':
            m = re.search(r'([\d,]+)', d.get('accomcount') or '')
            if m and m[1].replace(',', ''):
                f['capacity'] = int(m[1].replace(',', ''))
            if d.get('expagerange'):
                f['exp_agerange'] = d['expagerange'].strip()[:30]
            if re.search(r'체험', d.get('expguide') or ''):
                f['experience_available'] = True
        if ct == '28' and d.get('expagerangeleports'):
            f['exp_agerange'] = d['expagerangeleports'].strip()[:30]
        pairs = [(it.get('infoname', ''), it.get('infotext', '')) for it in (info or [])]
        if fee and d.get(fee):
            pairs.append(('이용요금', d.get(fee)))
        fs = fees_from(pairs)
        if fs:
            f['fees_krw'] = fs
    elif ct == '39':
        times(d.get('opentimefood')); rest(d.get('restdatefood'))
        if d.get('firstmenu'):
            f['signature_menu'] = d['firstmenu'].strip()[:60]
        if d.get('treatmenu'):
            f['menu_items'] = [x.strip() for x in re.split(r'[/,·]', d['treatmenu']) if x.strip()][:12]
        for k, name in (('parkingfood', 'parking'), ('packing', 'takeout'), ('chkcreditcardfood', 'credit_card'), ('reservationfood', 'reservation_info')):
            b = yn(d.get(k))
            if b is not None:
                f[name] = b
        if d.get('kidsfacility') in ('0', '1'):
            f['kids_facility'] = d['kidsfacility'] == '1'
        if d.get('infocenterfood'):
            f['inquiry_tel'] = d['infocenterfood'].strip()[:40]
    elif ct == '32':
        for k, name in (('checkintime', 'checkin'), ('checkouttime', 'checkout')):
            r = re.search(r'(\d{1,2}):?(\d{2})', d.get(k) or '')
            if r:
                f[name] = tmin(r[1], r[2])
        m = re.search(r'\d+', d.get('roomcount') or '')
        if m:
            f['room_count'] = int(m[0])
        b = yn(d.get('parkinglodging'))
        if b is not None:
            f['parking'] = b
        if d.get('subfacility'):
            f['facilities'] = [x.strip() for x in re.split(r'[/,·]', d['subfacility']) if x.strip()][:12]
        b = yn(d.get('chkcooking'))
        if b is not None:
            f['cooking_ok'] = b
        for k in ('sauna', 'fitness', 'barbecue', 'publicbath', 'seminar'):
            if d.get(k) == '1':
                f['has_' + k] = True
    return f


LIST_KEYS = ('contentid', 'title', 'lDongRegnCd', 'lDongSignguCd', 'areacode', 'sigungucode', 'addr1', 'mapx', 'mapy', 'cpyrhtDivCd', 'firstimage', 'firstimage2', 'modifiedtime',
             'cat1', 'cat2', 'cat3', 'lclsSystm1', 'lclsSystm2', 'lclsSystm3', 'tel')


def card(x, ct, pas, old=None):
    """목록 항목 → 장소 카드. old(이전 카드)의 사실·상세 진행은 그대로 둔다."""
    c = dict(old or {})
    c.update({'cid': str(x['contentid']), 'type': ct, 'name': (x.get('title') or '')[:80], 'area': str(x.get('lDongRegnCd') or ''),
              'sigungu': str(x.get('lDongSignguCd') or x.get('sigungucode') or ''), 'areacode_old': str(x.get('areacode') or '') or None, 'addr': (x.get('addr1') or '')[:60], 'cpy': x.get('cpyrhtDivCd') or None,
              'modified': x.get('modifiedtime') or None, 'listed': today(), 'lpass': pas,
              'cat': x.get('lclsSystm3') or x.get('cat3') or None})
    c.setdefault('f', {})
    if (x.get('tel') or '').strip():
        c['tel'] = x['tel'].strip()[:40]
    try:
        c['lon'], c['lat'] = round(float(x['mapx']), 6), round(float(x['mapy']), 6)
    except Exception:
        pass
    c.pop('photo', None)
    lic = PHOTO_OK.get(x.get('cpyrhtDivCd') or '')
    if lic and (x.get('firstimage') or '').startswith('http'):
        p = {'url': x['firstimage'], 'license': lic, 'credit': '한국관광공사'}
        if (x.get('firstimage2') or '').startswith('http'):
            p['thumb'] = x['firstimage2']
        if lic == 'KOGL-3':
            p['no_modify'] = True
        c['photo'] = p
    return c


# ---------- 저장소(로컬 폴더, 워크플로가 GCS 와 동기화) ----------
class Store:
    def __init__(self, d):
        self.d, self.cache, self.dirty = d, {}, set()
        os.makedirs(d, exist_ok=True)

    def area(self, a):
        if a not in self.cache:
            p = os.path.join(self.d, f'area_{a}.json')
            self.cache[a] = {}
            if os.path.exists(p):
                with open(p, encoding='utf-8') as fh:
                    self.cache[a] = json.load(fh)
        return self.cache[a]

    def put(self, a, c):
        self.area(a)[c['cid']] = c
        self.dirty.add(a)

    def save(self):
        for a in self.dirty:
            p = os.path.join(self.d, f'area_{a}.json')
            with open(p + '.tmp', 'w', encoding='utf-8') as fh:
                json.dump(self.cache[a], fh, ensure_ascii=False, separators=(',', ':'))
            os.replace(p + '.tmp', p)


# ---------- 행사·축제(searchFestival2, 11/20 256기) ----------
# 하루 한 번: 오늘보다 FEST_BACK_DAYS 앞에 시작한 행사부터 받아(이미 열리고 있는 긴 행사 포함) 끝난 것은 버린다. 1000건/쪽이라 하루 2~3회.
# 사실 값만(이름·기간·주소·좌표·전화). 사진은 장소와 같은 규칙(공공누리 1·3유형만). 결과 = store/festivals.json(전체 교체 — 실패한 날은 앞 것 유지).
FEST_BACK_DAYS = 180


def fest_card(x):
    c = {'cid': str(x.get('contentid') or ''), 'name': (x.get('title') or '')[:80], 'start': str(x.get('eventstartdate') or '')[:8],
         'end': str(x.get('eventenddate') or '')[:8], 'addr': (x.get('addr1') or '')[:60], 'area': str(x.get('lDongRegnCd') or x.get('areacode') or ''),
         'cpy': x.get('cpyrhtDivCd') or None}
    if (x.get('tel') or '').strip():
        c['tel'] = x['tel'].strip()[:40]
    try:
        c['lon'], c['lat'] = round(float(x['mapx']), 6), round(float(x['mapy']), 6)
    except Exception:
        pass
    lic = PHOTO_OK.get(x.get('cpyrhtDivCd') or '')
    if lic and (x.get('firstimage') or '').startswith('http'):
        c['photo'] = {'url': x['firstimage'], 'license': lic, 'credit': '한국관광공사', **({'no_modify': True} if lic == 'KOGL-3' else {})}
    return c


def festivals(st, store, cl, back=FEST_BACK_DAYS):
    """오늘(KST) 처음 부를 때만. 돌려줌 = 받은 행사 수(0 = 오늘 이미 받음·실패)."""
    if st.get('fest_day') == today():
        return 0
    d0 = (datetime.datetime.now(KST) - datetime.timedelta(days=back)).strftime('%Y%m%d')
    now = today().replace('-', '')
    got, page = {}, 1
    while True:
        r = cl.get('searchFestival2', eventStartDate=d0, numOfRows=ROWS, pageNo=page, arrange='A')
        if r is None:
            return 0  # 실패한 날은 앞 파일 유지
        body, items = r
        for x in items:
            c = fest_card(x)
            if c['cid'] and c['name'] and len(c['start']) == 8 and (c['end'] or c['start']) >= now:
                got[c['cid']] = c
        if not items or page * ROWS >= int(body.get('totalCount') or 0):
            break
        page += 1
    p = os.path.join(store.d, 'festivals.json')
    with open(p + '.tmp', 'w', encoding='utf-8') as fh:
        json.dump(got, fh, ensure_ascii=False, separators=(',', ':'))
    os.replace(p + '.tmp', p)
    st['fest_day'] = today()
    return len(got)


def build_events(store, layers_dir):
    """store/festivals.json → layers events_kto.json.gz (schema events-1, 공통 모양 = scripts/events_layer.py)."""
    p = os.path.join(store.d, 'festivals.json')
    if not os.path.exists(p):
        return 0
    import events_layer as EV
    rows = EV.kto_rows(json.load(open(p, encoding='utf-8')))
    os.makedirs(layers_dir, exist_ok=True)
    EV.write(os.path.join(layers_dir, 'events_kto.json.gz'), EV.doc('KR', 'tourapi', rows))
    return len(rows)


# ---------- 하루 실행 ----------
def load_prio():
    try:
        with open(PRIO) as fh:
            return set(map(str, json.load(fh)))
    except Exception:
        return set()


def is_front(a, c):
    """(11/15) 앱 감사(X_audit): 경주·제주 검색 풀의 영업시간 보유율이 3~19% → 같은 종류 안에서 맨 앞에.
    제주 = 시도 50. 경주 = 경북(47) 의 법정동 시군구 130(옛 areacode 35·sigungu 2), 주소에 '경주시'가 있어도 경주로 본다."""
    if a == '50':
        return True
    sg = str(c.get('sigungu') or '')
    return (a == '47' and sg in ('130', '47130')) or (c.get('areacode_old') == '35' and sg == '2') or '경주시' in (c.get('addr') or '')


def front_tier(a, c, in_prio):
    """같은 종류 안의 순서: 0 경주·제주 → 1 앱에 있는 소도시 → 2 앱에 있는 서울·부산 → 3 앱에 없는 소도시 → 4 그 밖의 서울·부산.
    (11/15 전: 앱에 있는 곳 → 소도시 → 서울·부산. 경주·제주를 맨 앞에, 앱에 있는 곳 안에서는 소도시를 먼저 — 대도시는 이미 일정 풀 영업시간 93~98%)"""
    if is_front(a, c):
        return 0
    if in_prio:
        return 1 if a not in BIG else 2
    return 3 if a not in BIG else 4


def queue(store, st, prio, step):
    """이번 회차에 아직 안 받은 카드들을 우선순위대로. step = 'detail'(detailIntro2) | 'info'(detailInfo2, 12·28만)."""
    P = st['pass']; mark, fk = ('dpass', 'dfail') if step == 'detail' else ('ipass', 'ifail')
    L = []
    for a in AREAS:
        for cid, c in store.area(a).items():
            if c.get(mark) == P or (c.get(fk) or [0, 0])[0] == P and c[fk][1] >= 3:
                continue
            if step == 'info' and c['type'] not in NEED_INFO:
                continue
            L.append((DETAIL_ORDER.get(c['type'], 3), front_tier(a, c, cid in prio), int(cid) if cid.isdigit() else 0, a, cid))
    L.sort()
    return [(a, cid) for *_, a, cid in L]


def ov_queue(store, st, prio):
    """소개(detailCommon2 overview)를 아직 안 받은 제1유형 관광지·문화시설 — 제주·경주 → 앱에 있는 곳 → 그 밖."""
    P, L = st['pass'], []
    for a in AREAS:
        for cid, c in store.area(a).items():
            if c.get('type') not in OV_TYPES or c.get('cpy') != 'Type1':
                continue
            if c.get('opass') == P or (c.get('ofail') or [0, 0])[0] == P and c['ofail'][1] >= 3:
                continue
            L.append((front_tier(a, c, cid in prio), OV_TYPES.index(c['type']), int(cid) if cid.isdigit() else 0, a, cid))
    L.sort()
    return [(a, cid) for *_, a, cid in L]


def overview(st, store, cl, prio, share):
    """하루 한도의 share 몫까지 소개를 받는다. 돌려줌 = 받은 곳 수. Stop 은 그대로 올린다."""
    from place_desc import first_sentence
    o = st.setdefault('ov', {})
    if o.get('date') != today():
        o.update(date=today(), calls=0)
    cap, n = int(cl.limit * share), 0
    for a, cid in ov_queue(store, st, prio):
        if o['calls'] >= cap or cl.left() <= 0:
            break
        c = store.area(a)[cid]
        before = st['calls']
        try:
            r = cl.get('detailCommon2', contentId=cid)
        finally:
            o['calls'] += st['calls'] - before
        if r is None:
            k = c['ofail'][1] + 1 if (c.get('ofail') or [0])[0] == st['pass'] else 1
            c['ofail'] = [st['pass'], k]; store.dirty.add(a); continue
        it = (r[1] or [{}])[0] if isinstance(r[1], list) else {}
        t = first_sentence(it.get('overview') if isinstance(it, dict) else None)
        if t:
            c['ov'] = t
        else:
            c.pop('ov', None)
        c['opass'] = st['pass']; c.pop('ofail', None); store.dirty.add(a); n += 1
    return n


def run(state, store, cl, prio=None, ov_share=0.0, fest=False):
    st = state
    prio = load_prio() if prio is None else prio
    for k, v in (('pass', 1), ('phase', 'list'), ('task', 0), ('page', 1), ('totals', {})):
        st.setdefault(k, v)
    st.pop('retry', None)
    T = tasks(); new = {'list': 0, 'detail': 0, 'info': 0, 'overview': 0}; stop = None
    try:
        if fest:
            new['festival'] = festivals(st, store, cl)  # (11/20 256기) 행사·축제 — 하루 한 번, 몇 번만(1000건/쪽)
        if ov_share > 0 and st.get('list_done'):
            new['overview'] = overview(st, store, cl, prio, ov_share)
        while True:
            if st['phase'] == 'list':
                if st['task'] >= len(T):
                    st['phase'] = 'detail'; st['list_done'] = today(); continue
                a, ct = T[st['task']]
                r = cl.get('areaBasedList2', lDongRegnCd=a, contentTypeId=ct, numOfRows=ROWS, pageNo=st['page'], arrange='A')
                if r is None:
                    raise Stop('LISTFAIL')
                body, items = r
                tot = int(body.get('totalCount') or 0); st['totals'][f'{a}-{ct}'] = tot
                have = store.area(a)
                for x in items:
                    cid = str(x.get('contentid') or '')
                    if cid:
                        store.put(a, card(x, ct, st['pass'], have.get(cid))); new['list'] += 1
                if not items or st['page'] * ROWS >= tot:
                    st['task'] += 1; st['page'] = 1
                else:
                    st['page'] += 1
                continue
            step = st['phase']
            Q = queue(store, st, prio, step)
            if not Q:
                if step == 'detail':
                    st['phase'] = 'info'; st['detail_done'] = today(); continue
                st['completed_passes'] = st.get('completed_passes', 0) + 1  # 한 바퀴 끝 → 목록부터 다시(갱신)
                st['pass'] += 1; st['phase'] = 'list'; st['task'] = 0; st['page'] = 1
                continue
            op, mark, fk = ('detailIntro2', 'dpass', 'dfail') if step == 'detail' else ('detailInfo2', 'ipass', 'ifail')
            for a, cid in Q:
                c = store.area(a)[cid]
                r = cl.get(op, contentId=cid, contentTypeId=c['type'])
                if r is None:
                    n = c[fk][1] + 1 if (c.get(fk) or [0])[0] == st['pass'] else 1
                    c[fk] = [st['pass'], n]; store.dirty.add(a); continue
                if step == 'detail':
                    keep = {k: v for k, v in c['f'].items() if k == 'fees_krw'}
                    c['f'] = dict(normalize(c['type'], r[1], None), **keep)
                else:
                    fs = normalize(c['type'], None, r[1]).get('fees_krw')
                    if fs:
                        c['f']['fees_krw'] = fs
                c[mark] = st['pass']; c['collected'] = today(); c.pop(fk, None)
                store.dirty.add(a); new[step] += 1
    except Stop as e:
        stop = str(e)
    store.save()
    st['last'] = {'date': today(), 'new': new, 'stop': stop}
    return stop


# ---------- 산출물 ----------
def build_outputs(store, layers_dir, agg_path):
    os.makedirs(layers_dir, exist_ok=True)
    agg = {}; n_all = 0
    for a in AREAS:
        rows = store.area(a)
        if not rows:
            continue
        lr = {}
        for cid, c in rows.items():
            e = {'n': c['name'], 't': c['type'], 'sg': c['sigungu'], 'a': c.get('addr'), 'c': c.get('cat'), 'f': c['f']}
            if 'lat' in c:
                e['lat'], e['lon'] = c['lat'], c['lon']
            if c.get('photo'):
                e['photo'] = c['photo']
            if c.get('ov') and c.get('cpy') == 'Type1':  # 소개 첫 문장 — 공공누리 제1유형만
                e['ov'] = c['ov']
            lr['kto:' + cid] = e
            agg['kto:' + cid] = dict(name=c['name'], city=c['addr'][:20], tourapi_cid=cid, type=TYPE_KO[c['type']], cpy=c.get('cpy'),
                                     area=AREA_KO[a], sigungu=c['sigungu'], cat=c.get('cat'), lat=c.get('lat'), lon=c.get('lon'),
                                     listed=c.get('listed'), collected=c.get('collected'), detail=bool(c.get('dpass')),
                                     **({'photo': c['photo']} if c.get('photo') else {}), **c['f'])
        doc = {'schema': 'tourapi-1', 'license': 'KOGL', 'attribution': ATTR, 'source': 'KorService2', 'area': a, 'area_ko': AREA_KO[a],
               'built': today(), 'rows': lr}
        b = gzip.compress(json.dumps(doc, ensure_ascii=False, separators=(',', ':')).encode('utf-8'), 9)
        with open(os.path.join(layers_dir, f'kto_{a}.json.gz'), 'wb') as fh:
            fh.write(b)
        n_all += len(lr)
    with open(agg_path, 'w', encoding='utf-8') as fh:
        json.dump({'_source': ATTR, **agg}, fh, ensure_ascii=False, separators=(',', ':'))
    return n_all


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--state', required=True)
    ap.add_argument('--store', required=True)
    ap.add_argument('--layers', required=True)
    ap.add_argument('--agg', required=True)
    ap.add_argument('--limit', type=int, default=int(os.environ.get('TOURAPI_DAILY_LIMIT', '900')))
    ap.add_argument('--ov-share', type=float, default=float(os.environ.get('TOURAPI_OV_SHARE', OV_SHARE)), help='하루 한도 가운데 소개(overview)에 먼저 쓰는 몫')
    a = ap.parse_args()
    key = os.environ.get('TOURAPI_KEY', '').strip()
    if not key:
        sys.exit('TOURAPI_KEY 없음(Actions secret DATA_GO_KR_KEY)')
    state = json.load(open(a.state)) if os.path.exists(a.state) else {}
    store = Store(a.store)
    stop = run(state, store, Client(key, state, a.limit), ov_share=a.ov_share, fest=os.environ.get('TOURAPI_FEST_OFF') != '1')
    os.makedirs(os.path.dirname(a.state) or '.', exist_ok=True)
    json.dump(state, open(a.state, 'w'), ensure_ascii=False, indent=1, sort_keys=True)
    n = build_outputs(store, a.layers, a.agg)
    ne = build_events(store, a.layers)
    T = tasks(); tot = sum(state['totals'].values())
    print(f"tourapi: calls={state['calls']} new={state['last']['new']} stored={n} events={ne} phase={state['phase']} task={state['task']}/{len(T)} pass={state['pass']} known_total={tot} stop={stop}")
    return 2 if stop in ('KEY', 'FAIL20', 'LISTFAIL') else 0


if __name__ == '__main__':
    sys.exit(main())
