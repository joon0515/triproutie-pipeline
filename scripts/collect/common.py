"""트립루티 수집 공통부 — 예절 있는 HTTP(robots 실시간 확인·간격·하루 한도·차단 시 오래 쉼), 금지 도메인, 저장소(Store), 정규화.

원칙(바꾸지 말 것): 이용약관·robots.txt·라이선스가 허락하는 출처만. 위장(브라우저 흉내)·프록시 돌리기·robots 무시·CAPTCHA 우회 없음.
User-Agent 에는 저장소 주소를 연락처로 싣는다. 개인정보(사람 이름·개인 메일·개인 전화)는 받지 않는다 — 업체·기관의 공식 전화만.
"""
import datetime, gzip, hashlib, io, json, math, os, re, subprocess, time, unicodedata, urllib.error, urllib.parse, urllib.request
import http.client
import urllib.robotparser

UA = 'TripRoutie-collector/1.0 (+https://github.com/joon0515/triproutie-pipeline)'
ROBOTS_NAME = 'TripRoutie-collector'
# 금지 목록(대표 결정): 인스타그램·틱톡·네이버·카카오·구글·타벨로그·트립어드바이저. 씨앗·링크·출처 어디서든 자동 제외.
FORBIDDEN = ('instagram.com', 'cdninstagram.com', 'tiktok.com', 'tiktokcdn.com', 'naver.com', 'naver.me', 'naver.net', 'pstatic.net',
             'kakao.com', 'kakaocdn.net', 'daum.net', 'kko.to', 'tabelog.com', 'goo.gl', 'g.page', 'g.co', 'youtube.com', 'youtu.be',
             'hko.gov.hk', 'weather.gov.hk',  # 홍콩천문대: 상업 목적은 사전 서면 허락 필요(registry.yaml banned)
             'tistory.com', 'blog.me', 'brunch.co.kr', 'velog.io', 'blogspot.com', 'ameblo.jp', 'hatenablog.com', 'note.com')  # 블로그: 허락 근거 없음
FORBIDDEN_LABELS = ('google', 'tripadvisor', 'tabelog', 'instagram', 'tiktok', 'naver', 'kakao')  # google.co.jp · tripadvisor.fr 등 모든 나라 도메인
LANGS = ('ko', 'en', 'ja', 'zh')  # + nm(현지 이름) = 앱 타일과 같은 5개 이름 칸
BLOCK_COOL_DAYS = 7  # 403 을 받은 사이트는 7일 쉼
_SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'sources')
_POLICY, _PRIO = {}, {}


def refresh_days(kind, default):
    """증분 갱신 주기(일) — sources/refresh_policy.json 의 fields.<kind>.days. 파일·칸이 없으면 default."""
    if not _POLICY:
        try:
            _POLICY.update(json.load(open(os.path.join(_SRC_DIR, 'refresh_policy.json'), encoding='utf-8')).get('fields') or {})
        except (OSError, ValueError):
            _POLICY['_'] = {}
    try:
        return int(_POLICY[kind]['days'])
    except (KeyError, TypeError, ValueError):
        return default


def shard_cc(shard):
    """'<지역>:<cc>' 나라 조각 → cc. 언어(en)·번호(0) 조각은 None."""
    s = str(shard or '')
    return s.rsplit(':', 1)[1].upper() if ':' in s else None


def region_rank(cc, demand=None):
    """지역 순환 우선순위 — 작을수록 먼저. cc = 나라 코드(없으면 0 — 나라가 없는 조각은 순서를 바꾸지 않음).
    demand = {cc: 사용자 검색 수}(있으면 그 순서가 단계보다 앞선다). 근거: sources/region_priority.json."""
    cc = str(cc or '').upper()
    if len(cc) != 2 or not cc.isalpha():
        return 0
    if not _PRIO:
        try:
            tiers = json.load(open(os.path.join(_SRC_DIR, 'region_priority.json'), encoding='utf-8')).get('tiers') or []
        except (OSError, ValueError):
            tiers = []
        _PRIO.update({c: i for i, t in enumerate(tiers) for c in t})
        _PRIO['_n'] = len(tiers)
    base = _PRIO.get(cc, _PRIO['_n'])
    if demand:
        ranked = sorted(demand, key=lambda c: -float(demand[c] or 0))
        if cc in demand and float(demand[cc] or 0) > 0:
            return ranked.index(cc) / (len(ranked) + 1) - 1  # 검색 수가 있는 나라는 모두 단계 0 보다 앞(-1 ~ 0)
    return base
OPEN_LICENSES = ('cc0', 'cc-zero', 'publicdomain', 'pddl', 'cc-by', 'cc_by', 'ccby', 'odc-by', 'odc-odbl', 'odbl', 'ogl', 'open-government',
                 'ca-ogl', 'uk-ogl', 'etalab', 'licence-ouverte', 'lo-2', 'cdla-permissive', 'apache-2', 'mit', 'dl-de-zero', 'dl-de-by',
                 'nlod', 'kogl-1', 'ogdl-taiwan', 'sg-odl')
CLOSED_HINTS = ('nc', 'nd', 'noncommercial', 'no-deriv', 'proprietary', 'all rights', 'other-closed', 'notspecified', 'not specified')


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def today():
    return now().date().isoformat()


class Stop(Exception):
    """이 출처는 오늘 그만(한도·차단·시간)."""


class Blocked(Exception):
    pass


_URL_BAD = re.compile(r'[\x00-\x20\x7f]')


def iri_to_uri(url):
    """한글·전각 등 비ASCII 주소를 요청 가능한 형태로(호스트는 IDNA, 경로·쿼리는 퍼센트 인코딩)."""
    url = url.strip()
    try:
        url.encode('ascii')
        if not _URL_BAD.search(url):  # 11/17: 영문 주소라도 빈칸·제어 문자가 있으면 아래에서 퍼센트 인코딩(http.client InvalidURL 방지)
            return url
    except UnicodeEncodeError:
        pass
    sp = urllib.parse.urlsplit(url)
    try:
        host = (sp.hostname or '').encode('idna').decode('ascii')
    except UnicodeError:
        host = sp.hostname or ''
    net = host + (f':{sp.port}' if sp.port else '')
    q = lambda x, safe: urllib.parse.quote(x, safe=safe)
    return urllib.parse.urlunsplit((sp.scheme, net, q(sp.path, "/%:@!$&'()*+,;="), q(sp.query, "=&%+/:@!$'()*,;?"), q(sp.fragment, "")))


def host_of(url):
    try:
        return (urllib.parse.urlsplit(url).hostname or '').lower().rstrip('.')
    except ValueError:  # 전각 문자 등 깨진 주소 → 빈 값(forbidden 처리되어 버려짐)
        return ''


def forbidden(url):
    h = host_of(url)
    if not h:
        return True
    if any(h == d or h.endswith('.' + d) for d in FORBIDDEN):
        return True
    return any(lab in FORBIDDEN_LABELS for lab in h.split('.'))


def license_open(lic):
    s = (lic or '').lower().replace(' ', '-')
    if not s or any(x in re.split(r'[^a-z]+', s) for x in ('nc', 'nd')) or any(x in s for x in CLOSED_HINTS[2:]):
        return False
    if re.search(r'creativecommons\.org/(licenses/by(-sa)?/|publicdomain/)|opendatacommons\.org/licenses/(odbl|by|pddl)|open-government-licence|etalab', s):
        return True
    return any(x in s for x in OPEN_LICENSES)


# ---------- 저장소(Storage) ----------
class Store:
    """root = 'gs://<bucket>/collect/v1' (gcloud storage) 또는 로컬 폴더(시험·dry-run). 결과 원본은 여기에만 — GitHub 에는 남기지 않는다."""

    def __init__(self, root):
        self.root = root.rstrip('/')
        self.gs = self.root.startswith('gs://')

    def _p(self, key):
        return f'{self.root}/{key}'

    def get(self, key):
        if self.gs:
            r = subprocess.run(['gcloud', 'storage', 'cat', self._p(key)], capture_output=True)
            return r.stdout if r.returncode == 0 else None
        p = self._p(key)
        return open(p, 'rb').read() if os.path.exists(p) else None

    def put(self, key, b):
        if self.gs:
            tmp = os.path.join(os.environ.get('RUNNER_TEMP', '/tmp'), 'up_' + hashlib.sha1(key.encode()).hexdigest()[:12])
            open(tmp, 'wb').write(b)
            subprocess.run(['gcloud', 'storage', 'cp', '-q', tmp, self._p(key)], check=True, capture_output=True)
            os.remove(tmp)
            return
        p = self._p(key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p + '.tmp', 'wb').write(b)
        os.replace(p + '.tmp', p)

    def list(self, prefix):
        if self.gs:
            r = subprocess.run(['gcloud', 'storage', 'ls', self._p(prefix) + '**'], capture_output=True, text=True)
            return sorted(x[len(self.root) + 1:] for x in r.stdout.split() if x.startswith(self.root) and not x.endswith('/'))
        base = self._p(prefix)
        d = base if os.path.isdir(base) else os.path.dirname(base)
        out = []
        for dp, _, fs in os.walk(d):
            for f in fs:
                k = os.path.relpath(os.path.join(dp, f), self.root).replace(os.sep, '/')
                if k.startswith(prefix) and not k.endswith('.tmp'):
                    out.append(k)
        return sorted(out)

    # ---- 여러 파일 한꺼번에(gcloud 를 파일마다 부르지 않게 — 파일 수천 개면 그것만으로 몇 시간) ----
    def pull(self, keys, dest, strict=True):
        """keys 를 로컬 폴더 dest 로 한 번에 받는다(파일 이름만 남김 — 이름이 겹치지 않는 묶음만). 돌려줌 = {key: 로컬 경로}.
        strict 면 하나라도 못 받았을 때 IOError(못 받은 누적본을 '없음'으로 알고 덮어쓰지 않게)."""
        os.makedirs(dest, exist_ok=True)
        keys = list(keys)
        if self.gs and keys:
            subprocess.run(['gcloud', 'storage', 'cp', '-q', '-I', dest.rstrip('/') + '/'], input='\n'.join(self._p(k) for k in keys).encode(), capture_output=True)
        elif keys:
            for k in keys:
                if os.path.exists(self._p(k)):
                    with open(self._p(k), 'rb') as f, open(os.path.join(dest, os.path.basename(k)), 'wb') as g:
                        while True:
                            b = f.read(1 << 20)
                            if not b:
                                break
                            g.write(b)
        out = {k: os.path.join(dest, os.path.basename(k)) for k in keys if os.path.exists(os.path.join(dest, os.path.basename(k)))}
        if strict and len(out) != len(set(keys)):
            raise IOError(f'pull: {len(set(keys)) - len(out)}개 파일을 받지 못함({keys[0].rsplit("/", 1)[0]})')
        return out

    def push(self, paths, prefix):
        """로컬 파일들을 <root>/<prefix>/<파일 이름> 으로 한 번에 올린다."""
        paths = list(paths)
        if not paths:
            return
        if self.gs:
            subprocess.run(['gcloud', 'storage', 'cp', '-q', '-I', self._p(prefix.strip('/')) + '/'], input='\n'.join(paths).encode(), check=True, capture_output=True)
            return
        for p in paths:
            with open(p, 'rb') as f:
                self.put(f"{prefix.strip('/')}/{os.path.basename(p)}", f.read())

    def delete(self, keys):
        keys = list(keys)
        if self.gs and keys:
            subprocess.run(['gcloud', 'storage', 'rm', '-q', '-I'], input='\n'.join(self._p(k) for k in keys).encode(), capture_output=True)
        elif keys:
            for k in keys:
                if os.path.exists(self._p(k)):
                    os.remove(self._p(k))

    def get_json(self, key, default=None):
        b = self.get(key)
        if b is None:
            return default
        return json.loads(gzip.decompress(b) if key.endswith('.gz') else b)

    def put_json(self, key, obj):
        b = json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode()
        self.put(key, gzip.compress(b, 6) if key.endswith('.gz') else b)


# ---------- 예절 있는 HTTP ----------
class Http:
    """출처 하나·조각(shard) 하나 전용. st = 그 조각의 상태 dict(하루 요청 수), hosts = 사이트별 쉼 기록(공유)."""

    def __init__(self, src, st, hosts, opener=None, sleep=time.sleep, clock=time.time, deadline=None):
        self.src, self.st, self.hosts, self.sleep, self.clock = src, st, hosts, sleep, clock
        self.limit = int(src.get('daily_limit', 500))
        self.interval = max(1.0, float(src.get('interval_s', 1.0)))  # 사이트당 초당 1회 이하
        self.deadline = deadline
        self.timeout, self.retries = float(src.get('timeout_s', 120)), int(src.get('retries', 3))
        self.opener = opener or (lambda u, h: self._open(u, h, self.timeout))
        self.robots, self.last = {}, {}
        if st.get('date') != today():
            st['date'], st['req'] = today(), 0

    @staticmethod
    def _open(url, headers, timeout=120):
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()

    def _robots(self, url):
        sp = urllib.parse.urlsplit(url)
        base = f'{sp.scheme}://{sp.netloc}'
        if base not in self.robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                code, _, body = self.opener(base + '/robots.txt', {'User-Agent': UA})
                rp.parse(body.decode('utf-8', 'replace').splitlines())
            except urllib.error.HTTPError as e:
                if 400 <= e.code < 500:
                    rp.allow_all = True  # robots.txt 없음(4xx) = 표준상 허용
                else:
                    rp.disallow_all = True  # 5xx·접속 실패 = 확인 못 함 → 받지 않음
            except Exception:
                rp.disallow_all = True
            self.robots[base] = rp
        return self.robots[base]

    def allowed(self, url):
        return not forbidden(url) and self._robots(url).can_fetch(ROBOTS_NAME, url)

    def get(self, url, accept=None):
        url = iri_to_uri(url)
        h = host_of(url)
        if forbidden(url):
            raise Blocked('forbidden domain')
        cool = self.hosts.get(h, {}).get('cool_until')
        if cool and cool > now().isoformat():
            raise Blocked('cooling')
        if self.deadline and self.clock() > self.deadline:
            raise Stop('TIME')
        rp = self._robots(url)
        if not rp.can_fetch(ROBOTS_NAME, url):
            raise Blocked('robots')
        gap = max(self.interval, float(rp.crawl_delay(ROBOTS_NAME) or 0))
        wait = self.last.get(h, 0) + gap - self.clock()
        if wait > 0:
            self.sleep(wait)
        for t in range(self.retries):
            if self.st['req'] >= self.limit:
                raise Stop('LIMIT')
            self.st['req'] += 1
            self.last[h] = self.clock()
            try:
                code, hdr, body = self.opener(url, {'User-Agent': UA, **({'Accept': accept} if accept else {})})
                return body
            except urllib.error.HTTPError as e:
                if e.code in (403, 429):
                    ra = (e.headers or {}).get('Retry-After') if e.headers else None
                    days = BLOCK_COOL_DAYS if e.code == 403 else max(1, int(ra) // 86400 if ra and str(ra).isdigit() else 1)
                    self.hosts[h] = {'cool_until': (now() + datetime.timedelta(days=days)).isoformat(), 'code': e.code}
                    raise Blocked(str(e.code))
                if e.code in (404, 410):
                    raise
            except (urllib.error.URLError, OSError, TimeoutError):
                pass
            except (http.client.InvalidURL, ValueError):  # 요청할 수 없는 주소 — 다시 해도 같으니 이 주소만 건너뜀(조각 전체를 죽이지 않음)
                raise IOError('bad url')
            if t + 1 < self.retries:
                self.sleep(min(60, 5 * 2 ** t))
        raise IOError('fetch failed')


# ---------- 정규화 ----------
def norm_name(s):
    s = unicodedata.normalize('NFKC', s or '').lower()
    return re.sub(r'[\s\W_]+', '', s)


def bad_text(s):
    return isinstance(s, str) and ('\ufffd' in s or re.search(r'(Ã.|Â.|â€)', s) is not None)


def valid_coord(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    return -90 <= lat <= 90 and -180 <= lon <= 180 and not (abs(lat) < 1e-6 and abs(lon) < 1e-6) and not math.isnan(lat)


CAT_MAP = {  # 출처 분류 → 앱 분류(타일 cats 와 같은 낱말)
    'see': 'sight', 'do': 'activity', 'eat': 'food', 'drink': 'bar', 'sleep': 'lodging', 'buy': 'shopping', 'go': 'transport',
    'museum': 'museum', 'touristattraction': 'sight', 'landmarksorhistoricalbuildings': 'sight', 'restaurant': 'food', 'cafeorcoffeeshop': 'cafe',
    'foodestablishment': 'food', 'lodgingbusiness': 'lodging', 'hotel': 'lodging', 'airport': 'transport', 'park': 'park', 'event': 'event',
    'stop': 'transport', 'station': 'transport', 'attraction': 'sight', 'shoppingcenter': 'shopping', 'store': 'shopping',
}


def map_cat(c):
    k = re.sub(r'[^a-z]', '', (c or '').lower())
    return CAT_MAP.get(k, k[:24] or 'other')


DAYS = {'mo': 'Mo', 'tu': 'Tu', 'we': 'We', 'th': 'Th', 'fr': 'Fr', 'sa': 'Sa', 'su': 'Su', 'monday': 'Mo', 'tuesday': 'Tu', 'wednesday': 'We',
        'thursday': 'Th', 'friday': 'Fr', 'saturday': 'Sa', 'sunday': 'Su'}


def norm_hours(h):
    """영업시간 → OSM opening_hours 비슷한 짧은 표준형('Mo-Fr 09:00-18:00; Sa 10:00-16:00'). 못 알아들으면 원문(120자)을 raw 로."""
    if not h:
        return None
    if isinstance(h, list):
        parts = []
        for x in h:
            if isinstance(x, dict):  # schema.org OpeningHoursSpecification
                ds = x.get('dayOfWeek') or []
                ds = ds if isinstance(ds, list) else [ds]
                dd = [DAYS.get(str(d).rsplit('/', 1)[-1].lower()) for d in ds]
                o, c = str(x.get('opens', ''))[:5], str(x.get('closes', ''))[:5]
                if all(dd) and re.match(r'\d\d:\d\d', o) and re.match(r'\d\d:\d\d', c):
                    parts.append(f"{','.join(dd)} {o}-{c}")
            elif isinstance(x, str):
                parts.append(norm_hours(x))
        return '; '.join(p for p in parts if p) or None
    s = re.sub(r'\s+', ' ', str(h)).strip()
    m = re.fullmatch(r'((?:Mo|Tu|We|Th|Fr|Sa|Su)(?:[-,](?:Mo|Tu|We|Th|Fr|Sa|Su))*)? ?(\d{1,2}):(\d\d) ?- ?(\d{1,2}):(\d\d)', s)
    if m:
        d = (m.group(1) + ' ') if m.group(1) else ''
        return f'{d}{int(m.group(2)):02d}:{m.group(3)}-{int(m.group(4)):02d}:{m.group(5)}'
    return 'raw:' + s[:120]


def place(src, pid, nm, lat, lon, cat=None, names=None, hours=None, url=None, addr=None, tel=None, price=None, wd=None, extra=None):
    """정규화 장소 1곳. 좌표·이름이 틀리면 None. 사진·본문 글은 담지 않는다(링크만). 예외: 공식 관광 자료의 소개 첫 문장 dsc(extra — collectors._tw_desc)."""
    if not nm or not valid_coord(lat, lon) or bad_text(nm):
        return None
    r = {'id': f"{src['id']}:{pid}", 'nm': str(nm)[:120], 'lat': round(float(lat), 6), 'lon': round(float(lon), 6), 'c': map_cat(cat)}
    for k, v in (names or {}).items():
        if k in LANGS and v and not bad_text(v):
            r[k] = str(v)[:120]
    hh = norm_hours(hours)
    for k, v in (('h', hh), ('url', url if url and not forbidden(url) else None), ('a', addr), ('tel', tel), ('p', price), ('wd', wd)):
        if v:
            r[k] = str(v)[:200]
    if extra:
        r.update(extra)
    r['lic'], r['src'] = src.get('license'), src['id']
    return r


def gz_json(obj):
    return gzip.compress(json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode(), 6)
