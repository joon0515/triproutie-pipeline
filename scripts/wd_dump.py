#!/usr/bin/env python3
"""Wikidata 주간 JSON 덤프(CC0) 대량 조인 — 5개 언어 라벨·설명·대표 사진(P18)을 한 번에 채운다. (2026-11-18, 233기)

  python3 scripts/wd_dump.py plan  --store gs://<b>/collect/v1 [--jobs 24 --procs 2]          # GITHUB_OUTPUT: matrix·any·meta
  python3 scripts/wd_dump.py scan  --store … --job K --jobs 24 --procs 2 --meta meta.json      # 조각 K(프로세스 procs 개, 각자 HTTP 1개)
  python3 scripts/wd_dump.py merge --store … --meta meta.json                                  # 조각 합치기 → processed/desc/wd_dump.json.gz

왜: QLever 로 한 번에 250개씩 물으면 하루 3,000회 한도라 수백만 QID 를 채우는 데 몇 주가 걸린다(11/17 소개 2,831곳). 덤프는 한 주에 한 번 전부 훑는다.
받는 곳: https://dumps.wikimedia.org/wikidatawiki/entities/latest-all.json.bz2 (공식 덤프, CC0 — robots.txt 없음 = 허용).
  bz2 한 스트림(약 104GB)이라 앞에서부터만 풀 수 있는데, bz2 '블록'(48비트 표시 0x314159265359, 비트 단위 위치)은 서로 따로 풀린다.
  그래서 파일을 바이트 범위(Range)로 jobs×procs 조각으로 나누고, 조각마다 범위 안에서 시작하는 첫 블록부터 다음 조각의 첫 블록 직전까지를
  비트 단위로 맞춰(앞에 'BZh9' 를 붙여) 풉니다. 조각 경계에 걸친 줄은 앞 조각이 다음 블록을 조금 더 풀어 마무리한다(각 줄은 한 조각만 처리).
  디스크에 내려받지 않고 흘려 읽는다(러너 디스크 한도와 무관). 연결이 끊기면 받은 곳부터 Range 로 이어 받는다.
예절: 러너(IP)마다 연결 procs 개(기본 2 — 덤프 서버의 IP 당 동시 연결 제한 안쪽), 동시 러너는 워크플로 max-parallel 로 묶는다. UA 에 저장소 주소.
조인 열쇠: place_desc.py 가 실행마다 남기는 processed/desc/qids.json.gz(수집 층 wd · 세계 타일 Q-id · OSM wikidata 태그(hours_src q))
  — 223기 이름 5개 언어(names-wd)·218기 소개(desc-wd)가 쓰는 것과 같은 씨앗. 결과 꼴도 place_desc.fetch_wd 의 캐시와 같다:
  {QID: [덤프 날짜, {칸: 설명}, {'l': {칸: 라벨}, 'p': P18 주소}]} → place_desc.run 이 캐시에 덮어 쓰고(더 새것만) 층을 만든다.
장소 내용은 로그에 쓰지 않는다(개수만).
"""
import argparse, bz2, datetime, email.utils, gzip, json, os, re, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, 'collect'))

DUMP = 'https://dumps.wikimedia.org/wikidatawiki/entities/latest-all.json.bz2'
UA = 'TripRoutie-pipeline/1.0 (+https://github.com/joon0515/triproutie-pipeline; weekly wikidata dump join)'
MAGIC, EOS = 0x314159265359, 0x177245385090
FILEPATH = 'http://commons.wikimedia.org/wiki/Special:FilePath/'
DISAMBIG = 'Q4167410'
CHUNK = 8 << 20      # 내려받기 읽기 단위
DEC = 2 << 20        # 한 번에 푸는 압축 바이트(풀린 글 약 30MB)
WANT = set()         # 씨앗 QID(정수) — fork 로 프로세스에 그대로 나눠 쓴다
ID_RE = re.compile(rb'"id": ?"Q(\d+)"')
# (11/20 270기) 도시 대표 사진: 우선 도시(sources/priority_cities.json)의 Wikidata 항목을 QLever 대신 덤프에서 고른다.
#  영어 라벨·별칭이 아래 이름이고 P625 좌표가 그 도시 상자 안이며 P18 이 있는 항목 중 sitelinks 가 가장 많은 것. 결과 = 조각 파일의 '_city' → merge 가 processed/photos/city_items.json
CITY_NAMES = {'jeju': ['Jeju City', 'Jeju Island', 'Jeju'], 'osaka': ['Osaka'], 'tokyo': ['Tokyo'], 'busan': ['Busan'], 'fukuoka': ['Fukuoka'], 'seoul': ['Seoul'],
              'danang': ['Da Nang'], 'bangkok': ['Bangkok'], 'taipei': ['Taipei'], 'kyoto': ['Kyoto'], 'sapporo': ['Sapporo'], 'okinawa_naha': ['Naha'],
              'gangneung': ['Gangneung'], 'gyeongju': ['Gyeongju'], 'hongkong': ['Hong Kong'], 'singapore': ['Singapore'], 'nhatrang': ['Nha Trang'],
              'cebu': ['Cebu City'], 'hochiminh': ['Ho Chi Minh City'], 'hanoi': ['Hanoi'], 'guam': ['Guam'], 'bali_south': ['Denpasar', 'Kuta', 'Badung Regency'],
              'shanghai': ['Shanghai'], 'yeosu': ['Yeosu'], 'jeonju': ['Jeonju'], 'sokcho': ['Sokcho'], 'paris': ['Paris'], 'rome': ['Rome'], 'barcelona': ['Barcelona'],
              'london': ['London'], 'prague': ['Prague'], 'newyork': ['New York City'], 'losangeles': ['Los Angeles'], 'honolulu': ['Honolulu'], 'sydney': ['Sydney'],
              'istanbul': ['Istanbul'], 'dubai': ['Dubai'], 'manila': ['Manila']}
CITY_BOX = {}        # key → bbox (main 이 priority_cities.json 에서 채움)
CITY_OF = {}         # 영어 이름 → [key…]
CITY_RE = None


def city_setup(cities):
    """cities = [{key, bbox}] → 이름 정규식. 이름 표가 없는 도시는 key 를 단어로 푼 이름."""
    global CITY_RE
    CITY_BOX.clear(); CITY_OF.clear()
    for c in cities:
        CITY_BOX[c['key']] = c['bbox']
        for n in CITY_NAMES.get(c['key']) or [' '.join(w.capitalize() for w in c['key'].split('_'))]:
            CITY_OF.setdefault(n, []).append(c['key'])
    alt = b'|'.join(re.escape(n.encode()) for n in sorted(CITY_OF, key=len, reverse=True))
    CITY_RE = re.compile(rb'"language": ?"en", ?"value": ?"(' + alt + rb')"') if CITY_OF else None


def city_hit(ent, names):
    """덤프 항목 + 맞은 영어 이름들 → [(key, [sitelinks, QID, P18 주소])] (좌표가 상자 안 · P18 있음 · 동음이의 아님)."""
    cl = ent.get('claims') or {}
    if any(((c.get('mainsnak') or {}).get('datavalue') or {}).get('value', {}).get('id') == DISAMBIG for c in cl.get('P31') or []):
        return []
    pts = []
    for c in cl.get('P625') or []:
        v = ((c.get('mainsnak') or {}).get('datavalue') or {}).get('value')
        if isinstance(v, dict) and isinstance(v.get('latitude'), (int, float)) and c.get('rank') != 'deprecated':
            pts.append((v['latitude'], v['longitude']))
    p18 = []
    for c in cl.get('P18') or []:
        v = ((c.get('mainsnak') or {}).get('datavalue') or {}).get('value')
        if isinstance(v, str) and v and c.get('rank') != 'deprecated':
            p18.append((0 if c.get('rank') == 'preferred' else 1, FILEPATH + urllib.parse.quote(v, safe='')))
    if not pts or not p18:
        return []
    sl = len(ent.get('sitelinks') or {})
    out = []
    for n in names:
        for k in CITY_OF.get(n, ()):
            b = CITY_BOX[k]
            if any(b[0] <= lo <= b[2] and b[1] <= la <= b[3] for la, lo in pts):
                out.append((k, [sl, ent.get('id'), sorted(p18)[0][1]]))
    return out


def log(m):
    print(f'[wd_dump] {m}', file=sys.stderr, flush=True)


# ---------- bz2 블록 찾기·비트 맞추기 ----------
def _patterns(m):
    """48비트 표시가 바이트 안 r 비트째(0=최상위)에서 시작할 때: (r, 찾을 바이트, 그 바이트의 창 안 위치)."""
    out = []
    for r in range(8):
        v = (m << (16 - r)).to_bytes(8, 'big')  # 64비트 창의 [r, r+48) 에 표시
        j0, j1 = -(-r // 8), (r + 48) // 8 - 1
        out.append((r, v[j0:j1 + 1], j0))
    return out


_PAT = {MAGIC: _patterns(MAGIC), EOS: _patterns(EOS)}


def find_marks(buf, base_byte, m=MAGIC, from_bit=0):
    """buf(파일의 base_byte 부터) 안의 표시 m 위치(파일 전체 기준 비트) 목록, from_bit 이상만, 오름차순."""
    hits = set()
    for r, core, j0 in _PAT[m]:
        i = buf.find(core)
        while i != -1:
            st = i - j0
            if st >= 0 and st + 7 <= len(buf):
                if (int.from_bytes(buf[st:st + 7], 'big') >> (8 - r)) & 0xFFFFFFFFFFFF == m:
                    b = (base_byte + st) * 8 + r
                    if b >= from_bit:
                        hits.add(b)
            i = buf.find(core, i + 1)
    return sorted(hits)


def shift_bits(buf, sh, nbits):
    """buf 의 sh 비트째부터 nbits 비트 → 바이트(마지막 남는 비트는 0). buf 는 넉넉히(+1 바이트) 준다."""
    nbytes = -(-nbits // 8)
    if sh == 0:
        out = bytearray(buf[:nbytes])
    else:
        import numpy as np
        a = np.frombuffer(bytes(buf[:nbytes + 1]).ljust(nbytes + 1, b'\0'), dtype=np.uint8).astype(np.uint16)
        out = bytearray(((a[:-1] << sh) | (a[1:] >> (8 - sh))).astype(np.uint8).tobytes())
    rem = nbits % 8
    if rem and out:
        out[-1] &= (0xFF << (8 - rem)) & 0xFF
    return bytes(out)


class Remote:
    """Range 로 흘려 읽기 + 앞부분 버리기. opener(start) → 읽기 가능한 파일 객체(시험은 가짜)."""

    def __init__(self, start, opener, retries=8):
        self.base, self.buf, self.opener, self.retries, self.eof = start, bytearray(), opener, retries, False
        self.f = None

    def _read(self):
        for t in range(self.retries):
            try:
                if self.f is None:
                    self.f = self.opener(self.base + len(self.buf))
                b = self.f.read(CHUNK)
                if not b:
                    self.eof = True
                return b
            except (OSError, ValueError) as e:
                log(f'끊김 {type(e).__name__} — {t + 1}번째 이어 받기')
                self.f = None
                time.sleep(min(60, 5 * 2 ** t))
        raise IOError('덤프 이어 받기 실패')

    def ensure(self, byte_end):
        while self.base + len(self.buf) < byte_end and not self.eof:
            self.buf += self._read()
        return self.base + len(self.buf) >= byte_end

    def end(self):
        return self.base + len(self.buf)

    def view(self, b0, b1):
        return bytes(self.buf[b0 - self.base:b1 - self.base])

    def trim(self, keep_from):
        k = keep_from - self.base
        if k > 0:
            del self.buf[:k]
            self.base = keep_from


def http_opener(url):
    def op(start):
        return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA, 'Range': f'bytes={start}-'}), timeout=120)
    return op


def next_mark(rd, from_bit, limit_bit=None):
    """from_bit 이후 첫 블록 표시(비트). limit_bit 까지 못 찾으면 None. 내려받으며 찾는다."""
    pos = from_bit // 8
    while True:
        hi = pos + CHUNK
        rd.ensure(hi + 8)
        hits = find_marks(rd.view(max(rd.base, pos - 8), min(rd.end(), hi + 8)), max(rd.base, pos - 8), MAGIC, from_bit)
        if hits:
            return hits[0]
        if rd.end() <= hi or (limit_bit is not None and hi * 8 > limit_bit):
            return None
        pos = hi


def _bits_at(rd, bit, n):
    rd.ensure((bit + n) // 8 + 2)
    b0 = bit // 8
    v = int.from_bytes(rd.view(b0, b0 + (bit % 8 + n + 7) // 8), 'big')
    return (v >> ((-(bit + n)) % 8)) & ((1 << n) - 1)


def decode_range(rd, s_bit, e_bit, sink):
    """[s_bit, e_bit) 의 블록들을 풀어 sink(바이트) 로.
    e_bit 가 있으면: 범위 안 블록 머리의 CRC 로 스트림 끝 표시(EOS + 합친 CRC)를 붙여 마지막 블록까지 다 내보낸다
      (libbz2 는 다음 머리를 읽기 전까지 마지막 블록 끝 약 32KB 를 붙들고 있다 — 11/18 실제 덤프 조각으로 확인).
    e_bit=None 이면 sink 가 True 를 돌려줄 때까지(다음 조각 첫 줄 마무리)."""
    d = bz2.BZ2Decompressor()
    d.decompress(b'BZh9')
    pos, crc, seen = s_bit, 0, set()
    while e_bit is None or pos < e_bit:
        stop = pos + DEC * 8 if e_bit is None else min(pos + DEC * 8, e_bit)
        rd.ensure(stop // 8 + 2)
        if rd.end() * 8 < stop:
            stop = rd.end() * 8
        if stop <= pos:
            break
        if e_bit is not None:  # 이 덩이 안에서 시작하는 블록들의 CRC(머리 48비트 뒤 32비트)
            lo = max(rd.base, pos // 8 - 7)
            for h in find_marks(rd.view(lo, min(rd.end(), stop // 8 + 7)), lo, MAGIC, pos):
                if h < stop and h not in seen:
                    seen.add(h)
                    c = _bits_at(rd, h + 48, 32)
                    crc = (((crc << 1) | (crc >> 31)) & 0xFFFFFFFF) ^ c
        b0 = pos // 8
        nb = stop - pos
        data = shift_bits(rd.view(b0, min(rd.end(), stop // 8 + 2)), pos % 8, nb)
        last = e_bit is not None and stop >= e_bit
        if last:  # 마지막 덩이: 남는 비트 + EOS(48) + CRC(32) 를 이어 붙인다(바이트 경계가 아니라 정수로)
            full, rem = divmod(nb, 8)
            head, tailb = data[:full], (data[full] >> (8 - rem) if rem else 0)
            v = (((tailb << 48) | EOS) << 32) | crc
            tot = rem + 80
            v <<= (-tot) % 8
            data = head
            extra = v.to_bytes((tot + 7) // 8, 'big')
        try:
            out = d.decompress(data)
            if out and sink(out):
                return
            if last:
                out = d.decompress(extra)
                if out:
                    sink(out)
                return
        except (OSError, EOFError, ValueError) as e:
            log(f'풀기 오류 {type(e).__name__} @bit {pos}')
            return
        pos = stop
        rd.trim(pos // 8 - 8)


# ---------- 줄 고르기 ----------
def pick(ent, tags):
    """덤프 항목 하나 → (설명 {꼬리표: 글}, 라벨 {꼬리표: 글}, P18 주소들) | None(동음이의 문서)."""
    cl = ent.get('claims') or {}
    for c in cl.get('P31') or []:
        if ((c.get('mainsnak') or {}).get('datavalue') or {}).get('value', {}).get('id') == DISAMBIG:
            return None
    g = lambda k: {t: v.get('value') for t, v in (ent.get(k) or {}).items() if t in tags and isinstance(v, dict) and v.get('value')}
    p18 = []
    for c in cl.get('P18') or []:
        v = ((c.get('mainsnak') or {}).get('datavalue') or {}).get('value')
        if isinstance(v, str) and v and c.get('rank') != 'deprecated':
            p18.append(FILEPATH + urllib.parse.quote(v, safe=''))  # QLever·WDQS 와 같은 꼴(공백 %20, 괄호 %28 %29)
    return g('descriptions'), g('labels'), sorted(p18)


NOT_WP = ('commonswiki', 'specieswiki', 'wikidatawiki', 'metawiki', 'mediawikiwiki', 'sourceswiki', 'incubatorwiki', 'outreachwiki', 'wikimaniawiki', 'foundationwiki')


def site_stats(ent):
    """덤프 항목 → (sitelinks 수, 위키백과 문서 있음). 위키백과 = '<언어>wiki' 꼴(공용·종·메타 등 제외). 글은 읽지 않고 개수만."""
    sl = ent.get('sitelinks') or {}
    return len(sl), any(k.endswith('wiki') and k not in NOT_WP for k in sl)


def to_cache(desc, labels, p18, day):
    import place_desc as PD
    ex = {'l': {k: v for k, v in PD._pn().wd_label_slots(labels).items() if v}}
    if p18:
        ex['p'] = p18[0]
    return [day, {k: v for k, v in PD.pick_slots(desc, labels).items() if v}, ex]


class Lines:
    """풀린 바이트 → 줄. first=True 면 첫 '\\n' 까지 버린다(앞 조각 몫). tail 모드는 첫 '\\n' 까지만 받고 끝."""

    def __init__(self, want, day, drop_first, tags):
        self.want, self.day, self.drop, self.tags = want, day, drop_first, tags
        self.rest, self.out, self.n, self.hit, self.bad = b'', {}, 0, 0, 0

    def line(self, ln):
        self.n += 1
        if CITY_RE is not None:
            cm = CITY_RE.findall(ln)
            if cm:
                try:
                    for k, v in city_hit(json.loads(ln.rstrip(b',\r\n ')), {x.decode() for x in cm}):
                        cur = self.out.setdefault('_city', {}).get(k)
                        if cur is None or v[0] > cur[0]:
                            self.out['_city'][k] = v
                except ValueError:
                    self.bad += 1
        m = ID_RE.search(ln, 0, 120)
        if not m or int(m.group(1)) not in self.want:
            return
        try:
            ent = json.loads(ln.rstrip(b',\r\n '))
        except ValueError:
            self.bad += 1
            return
        r = pick(ent, self.tags)
        q = 'Q' + m.group(1).decode()
        self.out[q] = to_cache(*r, self.day) if r else [self.day, {}, {'l': {}}]
        if r:  # (11/18 245기) 추천 지수 신호: sitelinks 수(s)·위키백과 문서 있음(w) — scripts/rec_index.py
            s, w = site_stats(ent)
            if s:
                self.out[q][2]['s'] = s
            if w:
                self.out[q][2]['w'] = 1
        self.hit += 1

    def feed(self, b):
        b = self.rest + b
        if self.drop:
            i = b.find(b'\n')
            if i < 0:
                self.rest = b''
                return False
            b, self.drop = b[i + 1:], False
        parts = b.split(b'\n')
        self.rest = parts.pop()
        for ln in parts:
            self.line(ln)
        return False

    def tail(self, b):
        """다음 조각 앞부분: 첫 '\\n' 까지 붙여 마지막 줄을 마무리하면 True."""
        i = b.find(b'\n')
        if i < 0:
            self.rest += b
            return False
        self.line(self.rest + b[:i])
        self.rest = b''
        return True


def scan_part(args):
    """조각 하나(파일의 [a, b) 바이트에서 시작하는 블록들). 돌려줌 = (캐시 조각, 통계)."""
    p, parts, size, eos_bit, day, opener = args
    import place_desc as PD
    opener = http_opener(opener) if isinstance(opener, str) else opener
    a, b = size * p // parts, size * (p + 1) // parts
    t0 = time.time()
    e = eos_bit
    if p < parts - 1:  # 끝 경계 = 다음 조각의 첫 블록. 짧게 따로 읽고 닫는다(본 흐름이 조각 전체를 메모리에 쌓지 않게)
        re_ = Remote(max(0, b - 8), opener)
        e = next_mark(re_, b * 8) or eos_bit
        if re_.f is not None:
            re_.f.close()
        e = min(e, eos_bit)
    rd = Remote(max(0, a - 8), opener)
    s = next_mark(rd, a * 8)
    if s is None or s >= e:
        return {}, {'part': p, 'lines': 0, 'hit': 0, 'sec': 0}
    L = Lines(WANT, day, p > 0, set(PD.ALL_TAGS))
    decode_range(rd, s, e, L.feed)
    cur, done = e, False
    while cur < eos_bit and not done:  # 마지막 줄 마무리: 다음 블록을 한 개씩(끝 표시를 붙여 다 내보내며) 첫 '\n' 까지
        nxt = min(next_mark(rd, cur + 1) or eos_bit, eos_bit)
        got = []
        decode_range(rd, cur, nxt, lambda b: got.append(b) or False)
        done = L.tail(b''.join(got)) if got else False
        cur = nxt
    if e >= eos_bit and L.rest.strip():
        L.line(L.rest)
    return L.out, {'part': p, 'lines': L.n, 'hit': L.hit, 'bad': L.bad, 'sec': round(time.time() - t0), 'mb': round((e - s) / 8 / 2 ** 20)}


# ---------- 저장소 ----------
def meta_of(url=DUMP, opener=None):
    req = urllib.request.Request(url, method='HEAD', headers={'User-Agent': UA})
    h = (opener or urllib.request.urlopen)(req, timeout=60).headers
    size = int(h['Content-Length'])
    day = email.utils.parsedate_to_datetime(h['Last-Modified']).date().isoformat()
    tail = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA, 'Range': f'bytes={size - 64}-'}), timeout=60).read()
    eos = find_marks(tail, size - 64, EOS)
    if not eos:
        raise SystemExit('덤프 끝 표시를 못 찾음 — 한 스트림 bz2 가 아님')
    return {'url': url, 'size': size, 'date': day, 'eos_bit': eos[-1]}


def load_cities():
    j = json.load(open(os.path.join(HERE, '..', 'sources', 'priority_cities.json'), encoding='utf-8'))
    return [c for c in j['cities'] if isinstance(c.get('bbox'), list) and len(c['bbox']) == 4]


def load_qids(store):
    j = store.get_json('processed/desc/qids.json.gz', None) or {}
    return set(int(q) for q in j.get('q') or [])


def main():
    from common import Store
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['plan', 'scan', 'merge'])
    ap.add_argument('--store', required=True)
    ap.add_argument('--jobs', type=int, default=24); ap.add_argument('--procs', type=int, default=2)
    ap.add_argument('--job', type=int, default=0); ap.add_argument('--meta', default='')
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()
    store = Store(a.store)
    if a.cmd == 'plan':
        m = meta_of()
        st = store.get_json('processed/desc/dump/state.json', {}) or {}
        qn = len(load_qids(store))
        m['qids'] = qn
        skip = (not a.force) and st.get('date') == m['date'] and st.get('qids', -1) >= qn and st.get('jobs') == a.jobs  # 조각이 빠졌으면 다시
        if skip and (store.get_json('processed/photos/city_items.json', None) or {}).get('date') != m['date']:
            skip = False  # (270기) 도시 대표 항목을 이 덤프로 아직 고르지 않음
        if qn == 0:
            skip = True
            log('씨앗 QID 목록 없음(place-desc 단계가 한 번 돌아야 생김)')
        print('meta=' + json.dumps(m, separators=(',', ':')))
        print('matrix=' + json.dumps({'job': list(range(a.jobs))}, separators=(',', ':')))
        print(f"any={'false' if skip else 'true'}")
        log(f"덤프 {m['date']} · {m['size'] / 2 ** 30:.1f} GiB · 씨앗 QID {qn:,} · {'건너뜀(같은 덤프·새 씨앗 없음)' if skip else '훑음'}")
        return 0
    m = json.loads(open(a.meta).read()) if os.path.exists(a.meta) else json.loads(a.meta)
    if a.cmd == 'scan':
        WANT.update(load_qids(store))
        city_setup(load_cities())
        parts = a.jobs * a.procs
        mine = [a.job * a.procs + i for i in range(a.procs)]
        import multiprocessing as mp
        with mp.get_context('fork').Pool(a.procs) as pool:  # fork — WANT 를 복사 없이 나눠 쓴다
            res = pool.map(scan_part, [(p, parts, m['size'], m['eos_bit'], m['date'], m['url']) for p in mine])
        out = {}
        for o, s in res:
            for ck, v in (o.pop('_city', None) or {}).items():  # 프로세스마다 고른 도시 항목 — 큰 쪽(sitelinks)만
                if ck not in out.setdefault('_city', {}) or v[0] > out['_city'][ck][0]:
                    out['_city'][ck] = v
            out.update(o)
            log(f"조각 {s['part']}/{parts}: 줄 {s['lines']:,} · 맞은 QID {s['hit']:,} · {s.get('mb', 0)} MB · {s['sec']}초")
        store.put_json(f"processed/desc/dump/{m['date']}/job-{a.job:03d}.json.gz", out)
        return 0
    # merge
    keys = [k for k in store.list(f"processed/desc/dump/{m['date']}/") if k.endswith('.json.gz')]
    allc, cities = {}, {}
    for k in keys:
        part = store.get_json(k, {}) or {}
        for ck, v in (part.pop('_city', None) or {}).items():
            if ck not in cities or v[0] > cities[ck][0]:
                cities[ck] = v
        allc.update(part)
    store.put_json('processed/desc/wd_dump.json.gz', allc)
    store.put_json('processed/photos/city_items.json', {'date': m['date'], 'cities': {k: {'sl': v[0], 'q': v[1], 'p': v[2]} for k, v in sorted(cities.items())}})
    n_desc = sum(1 for v in allc.values() if v[1])
    n_lab = sum(1 for v in allc.values() if (v[2] or {}).get('l'))
    store.put_json('processed/desc/dump/state.json', {'date': m['date'], 'qids': m.get('qids'), 'jobs': len(keys), 'found': len(allc),
                                                      'with_desc': n_desc, 'with_label': n_lab, 'at': datetime.datetime.now(datetime.timezone.utc).isoformat()})
    msg = (f"## Wikidata 덤프 조인 {m['date']}\n- 조각 파일 {len(keys)}개 · 씨앗 QID {m.get('qids', 0):,} 중 덤프에서 찾음 {len(allc):,} · 설명 있음 {n_desc:,} · 라벨 있음 {n_lab:,}\n"
           f"- 도시 대표 항목(P18 있음) {len(cities)}/{len(load_cities())}" + (f" · 못 찾음 {', '.join(sorted(set(c['key'] for c in load_cities()) - set(cities)))}" if len(cities) < len(load_cities()) else '') + "\n")
    p = os.environ.get('GITHUB_STEP_SUMMARY')
    if p:
        open(p, 'a', encoding='utf-8').write(msg)
    print(msg)
    return 0


if __name__ == '__main__':
    sys.exit(main())
