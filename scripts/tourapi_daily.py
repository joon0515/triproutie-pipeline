#!/usr/bin/env python3
"""한국관광공사 TourAPI 4.0(KorService2) 전국 수집 — 하루 한도 안에서 이어받기. (2026-11-15, 대화 세션 collect.py 를 옮긴 것)

    TOURAPI_KEY=... python3 scripts/tourapi_daily.py --state tourapi/state.json --store W/store --layers W/layers --agg W/agg_tourapi.json

범위: 17개 시도(areaCode) 전부 × 관광지 12·문화시설 14·레포츠 28·숙박 32·쇼핑 38·음식점 39. 시도 단위 목록(areaBasedList2)에
  그 시도의 모든 시군구가 들어 있고, 장소마다 sigungucode 를 함께 저장한다(소도시 포함).
호출: 목록 1회/500곳 + 장소마다 detailIntro2 1회 (+ 요금이 detailInfo2 에만 있는 12·28 은 1회 더).
이어받기: 상태 파일(--state, 저장소에 커밋하는 작은 JSON)에 '몇 번째 작업·몇 쪽'과 오늘 호출 수만 둔다. 이미 이번 회차(pass)에
  받은 장소는 저장소(--store, GCS 와 동기화)를 보고 건너뛰므로 같은 쪽을 다시 읽어도 상세 호출은 다시 하지 않는다.
한도: KST 날짜별 호출 수 ≤ --limit(기본 900, 개발계정). 한도 초과 응답(LIMITED_NUMBER…)이면 그날 즉시 멈춤.
실패: 호출당 4번까지 지수 대기, 연속 20회 실패면 그날 중단(종료코드 2). 키 오류도 2. 장소 하나가 끝내 실패하면 건너뛰고
  retry 목록(최대 300)에 넣어 다음 날 먼저 다시 받는다.
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
AREAS = ['1', '2', '3', '4', '5', '6', '7', '8', '31', '32', '33', '34', '35', '36', '37', '38', '39']  # 17개 시도
AREA_KO = {'1': '서울', '2': '인천', '3': '대전', '4': '대구', '5': '광주', '6': '부산', '7': '울산', '8': '세종', '31': '경기', '32': '강원',
           '33': '충북', '34': '충남', '35': '경북', '36': '경남', '37': '전북', '38': '전남', '39': '제주'}
TYPES = ['12', '39', '32', '14', '28', '38']  # 앞쪽이 먼저(일정 엔진이 많이 쓰는 순)
TYPE_KO = {'12': '관광지', '14': '문화시설', '28': '레포츠', '32': '숙박', '38': '쇼핑', '39': '음식점'}
NEED_INFO = {'12', '28'}  # 요금이 detailInfo2 에만 있는 종류
ROWS = 500
PHOTO_OK = {'Type1': 'KOGL-1', 'Type3': 'KOGL-3'}
ATTR = '한국관광공사 TourAPI 4.0 (공공누리 제1·3유형, 출처표시)'


def today():
    return datetime.datetime.now(KST).date().isoformat()


def tasks():
    return [(a, t) for t in TYPES for a in AREAS]  # 종류 우선: 전국 관광지 → 전국 음식점 → …


# ---------- 호출 ----------
class Stop(Exception):
    pass


class Client:
    def __init__(self, key, state, limit, opener=None, sleep=time.sleep):
        self.key, self.st, self.limit, self.sleep = key, state, limit, sleep
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


def card(x, ct, facts, pas):
    c = {'cid': str(x['contentid']), 'type': ct, 'name': (x.get('title') or '')[:80], 'area': str(x.get('areacode') or ''),
         'sigungu': str(x.get('sigungucode') or ''), 'addr': (x.get('addr1') or '')[:60], 'cpy': x.get('cpyrhtDivCd') or None,
         'modified': x.get('modifiedtime') or None, 'collected': today(), 'pass': pas, 'f': facts}
    try:
        c['lon'], c['lat'] = round(float(x['mapx']), 6), round(float(x['mapy']), 6)
    except Exception:
        pass
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


# ---------- 하루 실행 ----------
def fetch_place(cl, x, ct, pas):
    cid = str(x['contentid'])
    r1 = cl.get('detailIntro2', contentId=cid, contentTypeId=ct)
    if r1 is None:
        return None
    info = None
    if ct in NEED_INFO:
        r2 = cl.get('detailInfo2', contentId=cid, contentTypeId=ct)
        if r2 is None:
            return None
        info = r2[1]
    return card(x, ct, normalize(ct, r1[1], info), pas)


def run(state, store, cl):
    st = state
    st.setdefault('pass', 1); st.setdefault('task', 0); st.setdefault('page', 1); st.setdefault('retry', []); st.setdefault('totals', {})
    T = tasks(); new = 0; stop = None
    try:
        # 1) 어제 실패한 장소 먼저(목록 항목 일부를 retry 에 들고 있다)
        keep = []
        for r in list(st['retry']):
            need = 2 if r['type'] in NEED_INFO else 1
            if cl.left() < need:
                keep.append(r); continue
            c = fetch_place(cl, r['x'], r['type'], st['pass'])
            if c:
                store.put(r['area'], c); new += 1
            elif r.get('n', 0) < 3:
                keep.append(dict(r, n=r.get('n', 0) + 1))
        st['retry'] = keep
        # 2) 작업(시도×종류) 순서대로, 쪽 단위
        while True:
            if st['task'] >= len(T):  # 전국 한 바퀴 끝 → 다음 회차(내용 갱신)
                st['completed_passes'] = st.get('completed_passes', 0) + 1
                st['pass'] += 1; st['task'] = 0; st['page'] = 1
            a, ct = T[st['task']]
            if cl.left() < 2:
                raise Stop('LIMIT')
            r = cl.get('areaBasedList2', areaCode=a, contentTypeId=ct, numOfRows=ROWS, pageNo=st['page'], arrange='A')
            if r is None:
                raise Stop('LISTFAIL')
            body, items = r
            st['totals'][f'{a}-{ct}'] = int(body.get('totalCount') or 0)
            have = store.area(a)
            for x in items:
                cid = str(x.get('contentid') or '')
                if not cid or (have.get(cid) or {}).get('pass') == st['pass']:
                    continue
                need = 2 if ct in NEED_INFO else 1
                if cl.left() < need:
                    raise Stop('LIMIT')  # 같은 쪽을 내일 다시 읽고, 받은 곳은 건너뛴다
                c = fetch_place(cl, x, ct, st['pass'])
                if c:
                    store.put(a, c); new += 1
                elif len(st['retry']) < 300:
                    st['retry'].append({'area': a, 'type': ct, 'x': {k: x.get(k) for k in ('contentid', 'title', 'areacode', 'sigungucode', 'addr1', 'mapx', 'mapy', 'cpyrhtDivCd', 'firstimage', 'firstimage2', 'modifiedtime')}})
            if not items or st['page'] * ROWS >= int(body.get('totalCount') or 0):
                st['task'] += 1; st['page'] = 1
            else:
                st['page'] += 1
    except Stop as e:
        stop = str(e)
    store.save()
    st['last'] = {'date': today(), 'new_places': new, 'stop': stop}
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
            e = {'n': c['name'], 't': c['type'], 'sg': c['sigungu'], 'f': c['f']}
            if 'lat' in c:
                e['lat'], e['lon'] = c['lat'], c['lon']
            if c.get('photo'):
                e['photo'] = c['photo']
            lr['kto:' + cid] = e
            agg['kto:' + cid] = dict(name=c['name'], city=c['addr'][:20], tourapi_cid=cid, type=TYPE_KO[c['type']], cpy=c.get('cpy'),
                                     area=AREA_KO[a], sigungu=c['sigungu'], collected=c['collected'],
                                     **({'photo': c['photo']} if c.get('photo') else {}), **c['f'])
        doc = {'schema': 'tourapi-1', 'license': 'KOGL', 'attribution': ATTR, 'source': 'KorService2', 'area': a, 'area_ko': AREA_KO[a],
               'built': today(), 'rows': lr}
        b = gzip.compress(json.dumps(doc, ensure_ascii=False, separators=(',', ':')).encode('utf-8'), 9)
        open(os.path.join(layers_dir, f'kto_{a}.json.gz'), 'wb').write(b)
        n_all += len(lr)
    json.dump({'_source': ATTR, **agg}, open(agg_path, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    return n_all


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--state', required=True)
    ap.add_argument('--store', required=True)
    ap.add_argument('--layers', required=True)
    ap.add_argument('--agg', required=True)
    ap.add_argument('--limit', type=int, default=int(os.environ.get('TOURAPI_DAILY_LIMIT', '900')))
    a = ap.parse_args()
    key = os.environ.get('TOURAPI_KEY', '').strip()
    if not key:
        sys.exit('TOURAPI_KEY 없음(Actions secret DATA_GO_KR_KEY)')
    state = json.load(open(a.state)) if os.path.exists(a.state) else {}
    store = Store(a.store)
    stop = run(state, store, Client(key, state, a.limit))
    os.makedirs(os.path.dirname(a.state) or '.', exist_ok=True)
    json.dump(state, open(a.state, 'w'), ensure_ascii=False, indent=1, sort_keys=True)
    n = build_outputs(store, a.layers, a.agg)
    T = tasks(); tot = sum(state['totals'].values())
    print(f"tourapi: calls={state['calls']} new={state['last']['new_places']} stored={n} task={state['task']}/{len(T)} pass={state['pass']} known_total={tot} stop={stop}")
    return 2 if stop in ('KEY', 'FAIL20', 'LISTFAIL') else 0


if __name__ == '__main__':
    sys.exit(main())
