#!/usr/bin/env python3
"""수집 2단(가공)·3단(게시).

  python3 scripts/collect/process.py process --store gs://<b>/collect/v1 --world gs://<b>/places/v1/<tag>/tiles   # 정규화·중복 제거·병합·trust·나라별 층
  python3 scripts/collect/process.py publish --store ... --bucket <b> --kms-key ... [--dry-run]                     # 품질 관문 → publish_layers.py

가공: raw/ 에서 지난 가공 뒤 새로 들어온 파일만 읽는다. 세계 전체(1억 행대)를 메모리에 올리면 러너가 죽으므로 작게 나눠 흘려보낸다.
  1) 원본 나누기: 원본 파일을 묶음으로 받아 한 파일씩 읽고 행을 나라별 대기함(processed/inbox/<cc>~….jsonl.gz)으로 보낸다.
  2) 나라별 가공: 대기함을 위도 0.1° 띠로 나눠, 띠 하나씩만 메모리에 올려 누적본(processed/bands/<cc>/m_<띠>.jsonl.gz, id 마다 한 행)에 합치고
     묶은 결과(c_<띠>.jsonl.gz)를 쓴다. 나라가 끝나면 띠 순서(= 위도 순)로 흘려 읽어 층 파일을 다시 만든다.
  체크포인트: 읽은 원본(state.json seen)·대기함·나라별 meta.json(하다 만 띠)·state.json dirty(층을 다시 만들 나라). 시간 예산(--budget-min)이
  다 되면 멈추고 다음 실행이 이어받는다. 끝낸 나라부터 latest.json 에 실린다(paths = 층 파일 → Storage 키, 손대지 않은 나라는 예전 파일 그대로).
  같은 곳 판정 = 정규화 이름이 같거나 한쪽이 다른 쪽을 품고(3자 이상) 좌표 50m 안. 여러 출처가 맞으면 tr(일치 출처 수) 상승,
  값이 다르면 공식 사이트(rank official) 값을 쓰고 영업시간이 다르면 hd=1 (앱 tl_trust 규칙: tr≥2 '확인됨', 공식 1곳 '공식').
  places-world 타일(앱에 이미 있는 장소)과 맞는 곳은 wid(타일 id)를 달아 '보강 행'으로, 안 맞는 곳은 '새 장소'로 싣는다.
  출력 = 타일과 같은 열 모양(id·nm·ko·en·ja·zh·c(cats 번호)·dla·dlo(1e5 누적 차분)·a …), schema 'collect-1', 파일 하나 ≤ 5만 행.
  알려진 한계: 띠 경계(0.1° 마다)를 사이에 둔 50m 안의 두 출처 행은 묶이지 않는다(타일 맞춤은 경계 100m 를 겹쳐 놓치지 않음).
    좌표가 바뀌어 다른 띠로 옮겨 간 같은 id 는 예전 띠에도 남는다.
게시: 관문(나라별 장소 수 -5% 넘게 감소·좌표 범위·필수 칸·글자 깨짐·금지 도메인·표본 20곳 모양) 통과 때만 새 태그로 publish_layers.py.
  지금 앱 목록의 층 파일을 모두 함께 실어(다른 층이 빠지지 않게) 새 태그를 만들고, 직전 3개 판만 남긴다.
  되돌리기(한 줄): gcloud storage cp gs://<b>/places/v1/<이전태그>/layers.json gs://<b>/places/v1/layers.json
"""
import argparse, datetime, glob, gzip, hashlib, json, math, os, random, re, resource, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import LANGS, Store, bad_text, forbidden, norm_name, now, today, valid_coord  # noqa: E402

PLACE_SETS = {'places', 'official', 'transit'}
INFO_SETS = {'advisory', 'holidays', 'transit_feeds'}
PART_ROWS = 50000
R50 = 50.0
CELL = 0.0005  # 약 55m 격자(이웃 9칸을 본다)
REGIONS = json.load(open(os.path.join(HERE, '..', '..', 'tools', 'world_regions.json'), encoding='utf-8'))
FIELDS = ('h', 'url', 'a', 'tel', 'p', 'wd')
# 러너(메모리 16GB·디스크 한정) 안에서 끝나게: 한 번에 메모리에는 '나라 하나의 위도 0.1° 띠 하나' 만 올린다.
BAND_E5 = 10000       # 띠 높이 0.1°(약 11km)
RAW_PULL = 20         # 원본 파일을 한 번에 받는 수(파일 하나 ≤ 5만 행)
INBOX_ROWS = 500000   # 나라별 대기함 파일 하나의 행 수 상한
FLUSH_ROWS = 2000000  # 원본을 이만큼 나눌 때마다 대기함을 올리고 체크포인트
PUSH_BANDS = 40       # 띠 이만큼마다 올리고 체크포인트
BUDGET_MIN = 100      # 한 실행의 시간 예산(분) — 워크플로 제한 150분 안쪽
PART_SHARE = 0.5      # 예산 가운데 원본 나누기에 쓰는 몫(나머지는 나라별 가공)


def cc_shard(cc, n):
    """나라 → 조각 번호(0..n-1). 어느 실행에서나 같은 값(sha1) — 나라 조각 병렬 실행(--stage countries --shard i/n)이 나라를 겹치지 않게 나눈다."""
    return int(hashlib.sha1(str(cc).encode()).hexdigest(), 16) % n


def cc_of(r):
    if r.get('cc'):
        return str(r['cc']).upper()[:2]
    for reg in REGIONS.values():
        for b in reg['bbox']:
            if b[0] <= r['lon'] <= b[2] and b[1] <= r['lat'] <= b[3] and len(reg['cc']) == 1:
                return reg['cc'][0]
    return 'ZZ'


def dist_m(a, b):
    dy = (a[0] - b[0]) * 111320
    dx = (a[1] - b[1]) * 111320 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dx, dy)


def same_name(x, y):
    if not x or not y:
        return False
    return x == y or (min(len(x), len(y)) >= 3 and (x in y or y in x))


class Index:
    """좌표 격자 + 이름. add(key, lat, lon, names) / find(lat, lon, names) → key 또는 None."""

    def __init__(self):
        self.g = {}

    def _c(self, lat, lon):
        return int(lat // CELL), int(lon // (CELL / max(0.2, math.cos(math.radians(lat)))))

    def add(self, key, lat, lon, names):
        self.g.setdefault(self._c(lat, lon), []).append((key, lat, lon, {n for n in names if n}))

    def find(self, lat, lon, names):
        names = {n for n in names if n}
        ci, cj = self._c(lat, lon)
        best = None
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for key, la, lo, ns in self.g.get((ci + di, cj + dj), ()):
                    if any(same_name(a, b) for a in names for b in ns):
                        d = dist_m((lat, lon), (la, lo))
                        if d <= R50 and (best is None or d < best[0]):
                            best = (d, key)
        return best and best[1]


def rnames(r):
    return [norm_name(r.get(k)) for k in ('nm',) + LANGS]


def decode_tile(j):
    """places-world 타일(열 모양) → [(id, lat, lon, [이름들])]."""
    out, la, lo = [], 0, 0
    for i in range(len(j.get('id') or [])):
        la += j['dla'][i]; lo += j['dlo'][i]
        out.append((j['id'][i], la / 1e5, lo / 1e5, [norm_name(j[k][i]) for k in ('nm', 'ko', 'en', 'ja', 'zh') if isinstance(j.get(k), list) and i < len(j[k]) and j[k][i]]))
    return out


def merge_cluster(rows):
    """같은 곳으로 묶인 여러 출처 행 → 한 행. 공식(rank official)이 이기고, 일치 출처 수 tr, 시간 다름 hd."""
    rows = sorted(rows, key=lambda r: (r.get('rank') != 'official', -len([k for k in FIELDS if r.get(k)])))
    base = dict(rows[0])
    srcs = sorted({r['src'] for r in rows})
    base['srcs'], base['tr'] = srcs, len(srcs)
    for r in rows[1:]:
        for k in ('nm',) + LANGS + FIELDS + ('c',):
            if not base.get(k) and r.get(k):
                base[k] = r[k]
    hs = {r['h'] for r in rows if r.get('h')}
    if len(hs) > 1 and rows[0].get('rank') == 'official':
        base['hd'] = 1
    base['ids'] = sorted({r['id'] for r in rows})[:8]
    return base


# ---------- 작게 나눠 흘려보내는 도구(메모리에 한 띠만) ----------
def band_of(lat):
    """위도 0.1° 띠 번호(0~1800). 나라 안에서 한 번에 메모리에 올리는 단위."""
    return (round(float(lat) * 1e5) + 9000000) // BAND_E5


def rss_mb():
    try:
        return int(open('/proc/self/statm').read().split()[1]) * os.sysconf('SC_PAGE_SIZE') // (1 << 20)
    except (OSError, ValueError, IndexError):
        return peak_mb()


def peak_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // (1024 if sys.platform != 'darwin' else 1 << 20)


class Log:
    """진행 줄: 경과 초 · 지금 RSS · 최대 RSS. 러너가 죽어도 어디까지 갔는지 로그에 남게 바로 내보낸다."""

    def __init__(self, clock=time.time):
        self.clock, self.t0 = clock, clock()

    def sec(self):
        return round(self.clock() - self.t0)

    def __call__(self, msg):
        print(f'[process +{self.sec()}s rss {rss_mb()}MB peak {peak_mb()}MB] {msg}', flush=True)


def dumps(o):
    return json.dumps(o, ensure_ascii=False, separators=(',', ':'))


def jl_write(path, rows, level=6):
    n, buf = 0, []
    with gzip.open(path, 'wb', compresslevel=level) as f:
        for r in rows:
            buf.append(dumps(r)); n += 1
            if len(buf) >= 5000:
                f.write(('\n'.join(buf) + '\n').encode()); buf = []
        if buf:
            f.write(('\n'.join(buf) + '\n').encode())
    return n


def jl_read(path):
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


class Spool:
    """키별 JSONL 줄을 조금 모았다가(max_buf 줄) gzip 파일에 이어 쓴다 — 전체를 메모리에 두지 않는다."""

    def __init__(self, d, name, max_buf=200000, level=3):
        os.makedirs(d, exist_ok=True)
        self.d, self.name, self.max_buf, self.level = d, name, max_buf, level
        self.buf, self.nbuf, self.counts = {}, 0, {}

    def add(self, key, line):
        self.buf.setdefault(key, []).append(line)
        self.counts[key] = self.counts.get(key, 0) + 1
        self.nbuf += 1
        if self.nbuf >= self.max_buf:
            self.flush()

    def path(self, key):
        return os.path.join(self.d, self.name(key))

    def flush(self):
        for key, lines in self.buf.items():
            with gzip.open(self.path(key), 'ab', compresslevel=self.level) as f:  # gzip 은 이어 붙인 조각도 한 파일로 읽힌다
                f.write(('\n'.join(lines) + '\n').encode())
        self.buf, self.nbuf = {}, 0


def iter_json_object(path, chunk=1 << 20):
    """gzip JSON 객체({키: 값, …}) 하나를 통째로 올리지 않고 (키, 값) 씩 — 예전 누적본(processed/merged/<cc>.json.gz) 옮기기용."""
    dec, ws = json.JSONDecoder(), ' \t\r\n'
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        buf, pos, eof, opened = f.read(chunk), 0, False, False
        while True:
            while pos < len(buf) and buf[pos] in (ws + ',' if opened else ws):
                pos += 1
            need = pos >= len(buf)
            if not need and not opened:
                if buf[pos] != '{':
                    raise ValueError('JSON 객체가 아님')
                opened, pos = True, pos + 1
                continue
            if not need and buf[pos] == '}':
                return
            if not need:
                try:
                    k, p = dec.raw_decode(buf, pos)
                    while p < len(buf) and buf[p] in ws:
                        p += 1
                    if p >= len(buf) or buf[p] != ':':
                        raise ValueError('잘림')
                    p += 1
                    while p < len(buf) and buf[p] in ws:
                        p += 1
                    v, p = dec.raw_decode(buf, p)
                    if p >= len(buf) and not eof:
                        raise ValueError('잘림')  # 값이 조각 끝에 걸렸을 수 있다 → 더 읽고 다시
                    pos = p
                    yield k, v
                    continue
                except ValueError:
                    if eof:
                        raise
            if eof:
                return
            more = f.read(chunk)
            eof = not more
            buf, pos = buf[pos:] + more, 0


def world_files(world, cc, wd):
    """world = 로컬 폴더 또는 gs://…/tiles. 그 나라 타일(w-<셀>_<cc>.<지역>.json.gz) 파일 목록(로컬)."""
    if not world:
        return []
    if world.startswith('gs://'):
        os.makedirs(wd, exist_ok=True)
        subprocess.run(['gcloud', 'storage', 'cp', '-q', f'{world}/w-*_{cc}.*json.gz', wd], capture_output=True)
        return sorted(glob.glob(os.path.join(wd, '*.json.gz')))
    return sorted(set(glob.glob(os.path.join(world, f'w-*_{cc}.*json.gz')) + glob.glob(os.path.join(world, f'w-*_{cc}.json.gz'))))


def world_split(files, need, d):
    """타일을 하나씩 풀어 띠별 파일로(필요한 띠만). 띠 경계 100m 안의 타일 장소는 이웃 띠에도 실어 경계에서 맞춤을 놓치지 않는다.
    돌려줌 = (Spool, 그 나라 타일 장소 수)."""
    sp, n = Spool(d, lambda b: f'w_{b:04d}.jsonl.gz', max_buf=100000), 0
    for f in files:
        for wid, la, lo, ns in decode_tile(json.loads(gzip.decompress(open(f, 'rb').read()))):
            n += 1
            for b in {band_of(la), band_of(la - 0.001), band_of(la + 0.001)}:
                if b in need:
                    sp.add(b, dumps([wid, la, lo, ns]))
    sp.flush()
    return sp, n


def cluster_band(merged, widx):
    """띠 하나(id → 행): 출처 사이 같은 곳 묶기 → 타일과 맞춤(wid) → (위도, 경도) 순 행 list."""
    idx, clusters = Index(), {}
    for rid, r in sorted(merged.items(), key=lambda kv: (kv[1].get('rank') != 'official', kv[0])):
        key = idx.find(r['lat'], r['lon'], rnames(r))
        if key is None:
            key = rid; idx.add(key, r['lat'], r['lon'], rnames(r))
        clusters.setdefault(key, []).append(r)
    out = []
    for key, rs in clusters.items():
        m = merge_cluster(rs)
        wid = widx.find(m['lat'], m['lon'], rnames(m))
        if wid:
            m['wid'] = wid
        out.append(m)
    out.sort(key=lambda m: (m['lat'], m['lon']))
    return out


def layer_paths(latest):
    """latest.json → {층 파일: Storage 키}. 예전 판(paths 없음)은 processed/out/<run>/<파일>."""
    p = dict((latest or {}).get('paths') or {})
    for f in (latest or {}).get('files', []):
        p.setdefault(f, f"processed/out/{latest['run']}/{f}")
    return p


def _safe_cc(cc):
    return cc if re.fullmatch(r'[A-Z0-9]{2}', cc or '') else 'ZZ'


# ---------- 2단: 가공 ----------
class _Run:
    """한 번의 가공 실행이 들고 다니는 것(저장소·상태·시계·작업 폴더·최신 판)."""

    def __init__(self, store, world, budget_min, clock, workdir, shard=None, batch=''):
        self.store, self.world, self.clock, self.log = store, world, clock, Log(clock)
        # shard=(i, n): 나라 조각 실행. 여럿이 동시에 돌므로 공유 파일(state.json·latest.json)에는 쓰지 않고 processed/shards/ 아래 제 파일에만 쓴다.
        self.shard, self.batch, self.done_cc = shard, batch, {}
        self.t_part = self.log.t0 + budget_min * 60 * PART_SHARE  # 1) 원본 → 나라별 대기함: 예산의 절반까지
        self.t_end = self.log.t0 + budget_min * 60                # 2) 나라별 띠 가공: 남은 예산
        self.wd = workdir or tempfile.mkdtemp(prefix='proc_', dir=os.environ.get('RUNNER_TEMP') or None)
        self.run = now().strftime('%Y%m%d%H%M')
        self.pst = store.get_json('processed/state.json', {}) or {}
        self.latest = store.get_json('processed/latest.json', {}) or {}
        self.paths, self.counts = layer_paths(self.latest), dict(self.latest.get('counts', {}))
        self.stats = {'read_files': 0, 'read_rows': 0, 'countries': {}}

    def tmp(self, name):
        p = os.path.join(self.wd, name)
        shutil.rmtree(p, ignore_errors=True)
        os.makedirs(p)
        return p

    def save_state(self):
        if self.shard:
            i, n = self.shard
            self.store.put_json(f'processed/shards/state_{i}.json', {'batch': self.batch, 'i': i, 'n': n, 'dirty': list(self.pst.get('dirty', [])),
                                                                     'at': now().isoformat(), 'sec': self.log.sec(), 'peak_rss_mb': peak_mb()})
            return
        self.pst['progress'] = {'at': now().isoformat(), 'run': self.run, 'sec': self.log.sec(), 'peak_rss_mb': peak_mb(),
                                'read_files': self.stats['read_files'], 'read_rows': self.stats['read_rows'], 'countries_done': len(self.stats['countries'])}
        self.store.put_json('processed/state.json', self.pst)

    def save_latest(self):
        self.stats.update(peak_rss_mb=peak_mb(), sec=self.log.sec())
        if self.shard:
            i, n = self.shard
            self.store.put_json(f'processed/shards/latest_{i}.json', {'batch': self.batch, 'i': i, 'n': n, 'cc': self.done_cc})
            return
        self.latest = {'run': self.run, 'files': sorted(self.paths), 'paths': self.paths, 'counts': self.counts, 'stats': self.stats}
        self.store.put_json('processed/latest.json', self.latest)


def partition(R, new):
    """1) 새 원본 파일을 묶음(출처/날짜/조각 폴더, 한 번에 RAW_PULL 개)으로 받아 한 파일씩 읽고, 행을 나라별 대기함 파일로 흘려보낸다.
    FLUSH_ROWS 행마다 대기함을 올리고 '읽은 원본' 을 상태에 적는다(체크포인트). 돌려줌 = 남은 원본 파일 수."""
    store, seen = R.store, R.pst.setdefault('seen', {})
    groups = {}
    for k in sorted(new):
        groups.setdefault(k.rsplit('/', 1)[0], []).append(k)
    chunks = [(g, ks[i:i + RAW_PULL]) for g, ks in sorted(groups.items()) for i in range(0, len(ks), RAW_PULL)]
    S = {'keys': [], 'info': {}, 'sp': None, 'ncc': {}, 'rows': 0}

    def flush():
        sp = S['sp']
        if sp is not None:
            sp.flush()
            store.push([sp.path(k) for k in sp.counts], 'processed/inbox')
            shutil.rmtree(sp.d, ignore_errors=True)
        for ds, rows in S['info'].items():
            cur = store.get_json(f'processed/info/{ds}.json.gz', {}) or {}
            cur.update(rows)
            store.put_json(f'processed/info/{ds}.json.gz', cur)
        for k in S['keys']:
            seen.setdefault(k.split('/')[2], []).append(k)
        R.save_state()
        R.log(f"대기함 올림: 원본 {len(S['keys'])}개 · 장소 행 {S['rows']:,} · 나라 {len(S['ncc'])}개")
        S.update(keys=[], info={}, sp=None, ncc={}, rows=0)

    left = len(new)
    for n, (g, ks) in enumerate(chunks):
        if n and R.clock() > R.t_part:
            R.log(f'시간 예산(원본 나누기) 끝 — 원본 {left}개는 다음 실행에서')
            break
        got = store.pull(ks, R.tmp('raw'), strict=False)
        nrow = nplace = 0
        for k in ks:
            if k not in got:
                continue  # 못 받은 파일은 '읽음' 으로 적지 않는다 → 다음 실행에서 다시
            with open(got[k], 'rb') as f:
                j = json.loads(gzip.decompress(f.read())) or {}
            os.remove(got[k])
            ds = j.get('dataset')
            for r in j.get('rows') or []:
                nrow += 1
                if ds in PLACE_SETS:
                    if not valid_coord(r.get('lat'), r.get('lon')) or not r.get('nm') or bad_text(r.get('nm')):
                        continue
                    if r.get('url') and forbidden(r['url']):
                        r.pop('url')
                    r['_ds'] = ds
                    cc = _safe_cc(cc_of(r))
                    if S['sp'] is None:  # 대기함 파일 이름 = <cc>~<이어지는 번호>-<조각>: 이름 순서 = 들어온 순서(뒤의 것이 이김)
                        R.pst['inbox_seq'] = seq = int(R.pst.get('inbox_seq', 0)) + 1
                        S['sp'] = Spool(R.tmp('inbox'), lambda key, seq=seq: f'{key[0]}~{seq:08d}-{key[1]:03d}.jsonl.gz')
                    i = S['ncc'].get(cc, 0)
                    S['sp'].add((cc, i // INBOX_ROWS), dumps(r))  # 대기함 파일 하나 ≤ INBOX_ROWS 행
                    S['ncc'][cc] = i + 1
                    nplace += 1
                elif ds in INFO_SETS:
                    S['info'].setdefault(ds, {})[r['id']] = r
            del j
            S['keys'].append(k); left -= 1
            R.stats['read_files'] += 1
        R.stats['read_rows'] += nrow; S['rows'] += nplace
        R.log(f'원본 {g} : 파일 {len(ks)}개 · 행 {nrow:,} · 장소 {nplace:,} (남은 원본 {left})')
        if S['rows'] >= FLUSH_ROWS:
            flush()
    if S['sp'] is not None or S['keys'] or S['info']:
        flush()
    return left


def country(R, cc, inbox, legacy):
    """2) 나라 하나: 대기함(새 행) → 위도 띠별 파일로 나눔 → 띠 하나씩(누적본 + 새 행 → 묶기 → 저장) → 층 파일 다시 만들기.
    띠 PUSH_BANDS 개마다 올리고 어디까지 했는지 meta.json 에 적는다. 시간이 다 되면 멈춘다(False) — 다음 실행이 이어서."""
    store, log, pre = R.store, R.log, f'processed/bands/{cc}'
    have = {k.rsplit('/', 1)[1] for k in store.list(pre + '/')}
    meta = store.get_json(f'{pre}/meta.json', {}) or {}
    meta.setdefault('bands', {})
    pend = meta.get('pending')
    if pend:  # 지난 실행이 하다 만 묶음: 같은 대기함 파일들로 다시 나누고, 끝낸 띠는 건너뛴다
        batch = [k for k in inbox if k.rsplit('/', 1)[1] <= pend['upto']]
    else:
        batch = list(inbox)
        pend = {'upto': max((k.rsplit('/', 1)[1] for k in batch), default=''), 'done': [], 'legacy': bool(legacy and not have - {'meta.json'})}
    if batch or pend.get('legacy'):
        sp = Spool(R.tmp('split'), lambda b: f's_{b:04d}.jsonl.gz')
        if pend.get('legacy') and legacy:  # 예전 누적본(나라 한 파일) → 띠로 옮김(처음 한 번)
            lp = store.pull([legacy], R.tmp('legacy'))[legacy]
            for _, r in iter_json_object(lp):
                sp.add(band_of(r['lat']), dumps(r))
            shutil.rmtree(os.path.dirname(lp), ignore_errors=True)
        for i in range(0, len(batch), 8):
            got = store.pull(batch[i:i + 8], R.tmp('in'))
            for k in batch[i:i + 8]:
                for r in jl_read(got[k]):
                    sp.add(band_of(r['lat']), dumps(r))
                os.remove(got[k])
        sp.flush()
        done = set(pend['done'])
        todo = sorted(b for b in sp.counts if b not in done)
        log(f'{cc}: 새 행 {sum(sp.counts.values()):,} · 띠 {len(sp.counts)}개(남은 {len(todo)})')
        wsp = Spool(R.tmp('wsplit'), str)
        if todo:
            wsp, meta['world'] = world_split(world_files(R.world, cc, os.path.join(R.tmp('tiles'), cc)), set(todo), wsp.d)
            shutil.rmtree(os.path.join(R.wd, 'tiles'), ignore_errors=True)
        bd = R.tmp('bands')
        for i in range(0, len(todo), PUSH_BANDS):
            part = todo[i:i + PUSH_BANDS]
            got = store.pull([f'{pre}/m_{b:04d}.jsonl.gz' for b in part if f'm_{b:04d}.jsonl.gz' in have], bd)
            up, nraw, nout = [], 0, 0
            for b in part:
                mp, cp = os.path.join(bd, f'm_{b:04d}.jsonl.gz'), os.path.join(bd, f'c_{b:04d}.jsonl.gz')
                merged = {r['id']: r for r in jl_read(mp)} if os.path.exists(mp) else {}
                for r in jl_read(sp.path(b)):
                    merged[r['id']] = r
                os.remove(sp.path(b))
                jl_write(mp, merged.values())
                widx = Index()
                if b in wsp.counts:
                    for wid, la, lo, ns in jl_read(wsp.path(b)):
                        widx.add(wid, la, lo, ns)
                out = cluster_band(merged, widx)
                jl_write(cp, out)
                meta['bands'][f'{b:04d}'] = {'raw': len(merged), 'places': len(out)}
                nraw += len(merged); nout += len(out)
                if len(merged) >= 200000:
                    log(f'{cc} 띠 {b:04d}: 행 {len(merged):,} → 장소 {len(out):,}')
                del merged, out, widx
                up += [mp, cp]
            store.push(up, pre)
            for p in up:
                os.remove(p)
            have.update(os.path.basename(p) for p in up)
            pend['done'] = sorted(done.union(todo[:i + len(part)]))
            meta['pending'] = pend
            store.put_json(f'{pre}/meta.json', meta)
            if cc not in R.pst.setdefault('dirty', []):
                R.pst['dirty'].append(cc); R.save_state()
            log(f'{cc}: 띠 {min(i + len(part), len(todo))}/{len(todo)} · 행 {nraw:,} → 장소 {nout:,}')
            if i + PUSH_BANDS < len(todo) and R.clock() > R.t_end:
                log(f'시간 예산 끝 — {cc} 는 다음 실행에서 이어서(띠 {len(todo) - i - len(part)}개 남음)')
                return False
        meta.pop('pending', None)
        store.put_json(f'{pre}/meta.json', meta)
        store.delete(batch)  # 띠에 다 반영한 뒤에만 대기함을 비운다(중간에 죽으면 다시 반영 — id 기준이라 결과 같음)
        if len(batch) < len(inbox):
            return country(R, cc, [k for k in inbox if k not in set(batch)], None)
    # 층 파일: 띠 순서(= 위도 순)로 흘려 읽어 5만 행씩
    bands = sorted(f for f in have if f.startswith('c_'))
    ld, files, part, st = R.tmp('layers'), [], [], {'places': 0, 'matched_world': 0, 'multi_src': 0, 'official': 0}

    def emit():
        f = f'col_{cc}_{len(files)}.json.gz'
        with open(os.path.join(ld, f), 'wb') as g:
            g.write(gzip.compress(dumps(to_layer(cc, part)).encode(), 6))
        files.append(f); part.clear()

    for i in range(0, len(bands), 40):
        got = store.pull([f'{pre}/{f}' for f in bands[i:i + 40]], R.tmp('cb'))
        for f in bands[i:i + 40]:
            for m in jl_read(got[f'{pre}/{f}']):
                st['places'] += 1; st['matched_world'] += bool(m.get('wid')); st['multi_src'] += m['tr'] >= 2; st['official'] += m.get('rank') == 'official'
                part.append(m)
                if len(part) >= PART_ROWS:
                    emit()
            os.remove(got[f'{pre}/{f}'])
    if part:
        emit()
    store.push([os.path.join(ld, f) for f in files], f'processed/out/{R.run}')
    shutil.rmtree(ld, ignore_errors=True)
    for f in [f for f in R.paths if f.startswith(f'col_{cc}_')]:
        del R.paths[f]
    for f in files:
        R.paths[f] = f'processed/out/{R.run}/{f}'
    R.counts[cc] = st['places']
    R.stats['countries'][cc] = {'raw': sum(b['raw'] for b in meta['bands'].values()), 'places': st['places'], 'matched_world': st['matched_world'],
                                'world': meta.get('world', 0), 'multi_src': st['multi_src'], 'official': st['official']}
    R.done_cc[cc] = {'files': {f: R.paths[f] for f in files}, 'count': st['places'], 'stats': R.stats['countries'][cc]}
    R.save_latest()
    if cc in R.pst.get('dirty', []):
        R.pst['dirty'].remove(cc)
    R.save_state()
    log(f"{cc} 끝: 장소 {st['places']:,} · 층 파일 {len(files)}개 · 타일과 일치 {st['matched_world']:,}")
    return True


def _info_layers(R):
    """정보 층(여행경보·공휴일·교통 피드 요약) — 작다."""
    for ds in sorted(INFO_SETS):
        cur = R.store.get_json(f'processed/info/{ds}.json.gz', {}) or {}
        if cur:
            f = f'col_info_{ds}.json.gz'
            R.store.put_json(f'processed/out/{R.run}/{f}', {'schema': 'collect-1', 'dataset': ds, 'built': today(), 'rows': cur, 'attribution': attribution(cur.values())})
            R.paths[f] = f'processed/out/{R.run}/{f}'


def merge_shards(R):
    """나라 조각들이 남긴 결과(processed/shards/latest_<i>.json · state_<i>.json, 같은 batch 것만)를 latest.json·state.json 에 합친다.
    결과 파일이 없는(실패했거나 돌지 않은) 조각의 나라는 손대지 않는다 — 예전 층 파일·dirty 표시가 그대로 남아 다음 실행이 이어받는다."""
    store, pst = R.store, R.pst
    dirty, got = list(pst.get('dirty', [])), 0
    for k in sorted(store.list('processed/shards/')):
        name, j = k.rsplit('/', 1)[1], store.get_json(k, {}) or {}
        if R.batch and j.get('batch') != R.batch:
            continue
        if name.startswith('latest_'):
            for cc, v in sorted((j.get('cc') or {}).items()):
                for f in [f for f in R.paths if f.startswith(f'col_{cc}_')]:
                    del R.paths[f]
                R.paths.update(v['files']); R.counts[cc] = v['count']; R.stats['countries'][cc] = v['stats']; got += 1
        elif name.startswith('state_'):
            dirty = [cc for cc in dirty if cc_shard(cc, j['n']) != j['i']] + list(j.get('dirty', []))
    pst['dirty'] = list(dict.fromkeys(dirty))
    _info_layers(R)
    R.stats['pending'] = {'raw_files': int(pst.get('raw_left', 0)), 'countries': list(pst['dirty'])}
    R.save_latest()
    R.save_state()
    shutil.rmtree(R.wd, ignore_errors=True)
    R.log(f"조각 합치기 {R.run}: 나라 {got}개 반영 · 하다 만 나라 {len(pst['dirty'])}개")
    return R.run, R.stats


def process(store, world=None, force_all=False, budget_min=BUDGET_MIN, clock=time.time, workdir=None, stage='all', shard=None, batch=''):
    """돌려줌 = (run, stats). stats['pending'] = 이번에 못 끝낸 일(원본 파일 수·나라 list) — 비어 있으면 다 끝남.
    한 실행은 시간 예산 안에서 멈추고(적어도 원본 묶음 하나·나라 띠 묶음 하나는 나아간다) 다음 실행이 Storage 의 체크포인트에서 이어받는다.
    stage: all(예전처럼 한 러너가 전부) | partition(원본 → 나라별 대기함만) | countries(나라별 가공만; shard=(i, n) 이면 cc_shard 가 i 인 나라만,
    러너 n 대가 동시에) | merge(조각 결과 합치기 + 정보 층). 순서: partition → countries × n → merge → publish (docs/THROUGHPUT.md)."""
    R = _Run(store, world, budget_min, clock, workdir, shard if stage == 'countries' else None, batch)
    if stage == 'merge':
        return merge_shards(R)
    pst, log = R.pst, R.log
    left = int(pst.get('raw_left', 0))
    if stage in ('all', 'partition'):
        if stage == 'partition':
            R.t_part = R.t_end  # 원본 나누기만 하므로 예산을 다 쓴다
        if force_all:
            pst['seen'] = {}
        seen = pst.setdefault('seen', {})  # 날짜 → 이미 읽은 원본 키(최근 7일만 기억)
        cutoff = (datetime.date.fromisoformat(today()) - datetime.timedelta(days=7)).isoformat()
        first = force_all or not pst.get('last_run') or bool(pst.get('backfill'))  # 처음(또는 처음 훑기가 아직 안 끝남): 날짜 가리지 않고 전부
        done = {k for v in seen.values() for k in v}
        keys = [k for k in store.list('raw/') if k.endswith('.json.gz')]
        new = [k for k in keys if k not in done and (first or k.split('/')[2] >= cutoff)]
        log(f'원본 {len(keys)}개 가운데 새 파일 {len(new)}개 · 시간 예산 {budget_min}분')
        del done, keys
        left = partition(R, new)
        if first:
            pst['backfill'] = left > 0
        if not pst.get('backfill'):
            pst['seen'] = {d: v for d, v in seen.items() if d >= cutoff}  # 처음 훑기가 끝난 뒤에만 오래된 기록을 지운다
        pst['raw_left'] = left
    if stage == 'partition':
        R.stats.update(pending={'raw_files': left, 'countries': []}, peak_rss_mb=peak_mb(), sec=log.sec())
        pst['last_run'] = R.run
        R.save_state()
        shutil.rmtree(R.wd, ignore_errors=True)
        log(f'원본 나누기 {R.run} 끝: 남은 원본 {left}')
        return R.run, R.stats
    mine = (lambda cc: cc_shard(cc, R.shard[1]) == R.shard[0]) if R.shard else (lambda cc: True)
    inbox = {}
    for k in store.list('processed/inbox/'):
        cc = k.rsplit('/', 1)[1].split('~', 1)[0]
        if mine(cc):
            inbox.setdefault(cc, []).append(k)
    legacy = {k.rsplit('/', 1)[1].split('.', 1)[0]: k for k in store.list('processed/merged/') if k.endswith('.json.gz')}
    dirty = list(pst.get('dirty', []))
    if R.shard:  # 조각 실행: 공유 state.json 의 dirty 와 지난번 제 조각 파일의 dirty 가운데 제 나라만 들고 다닌다
        own = store.get_json(f'processed/shards/state_{R.shard[0]}.json', {}) or {}
        dirty = [cc for cc in dict.fromkeys(dirty + list(own.get('dirty', []))) if mine(cc)]
        R.pst = pst = {'dirty': list(dirty)}
    todo = dirty + sorted(cc for cc in inbox if cc not in dirty)  # 하다 만 나라 먼저
    left_cc = []
    for n, cc in enumerate(todo):
        if n and clock() > R.t_end:
            left_cc = todo[n:]
            log(f'시간 예산 끝 — 나라 {len(left_cc)}개는 다음 실행에서')
            break
        if not country(R, cc, sorted(inbox.get(cc, [])), legacy.get(cc)):
            left_cc = todo[n:]
            break
    if not R.shard:
        _info_layers(R)
    R.stats['pending'] = {'raw_files': left, 'countries': left_cc}
    R.save_latest()
    if not R.shard:
        pst['last_run'] = R.run
    R.save_state()
    shutil.rmtree(R.wd, ignore_errors=True)
    log(f"가공 {R.run} 끝: 나라 {len(R.stats['countries'])}개 · 남은 원본 {left} · 남은 나라 {len(left_cc)}")
    return R.run, R.stats


def attribution(rows):
    srcs = sorted({s for r in rows for s in (r.get('srcs') or [r.get('src')]) if s})
    return 'collect: ' + ', '.join(srcs) + ' — 출처별 라이선스는 sources/registry.yaml'


def to_layer(cc, rows):
    cats = sorted({r.get('c') or 'other' for r in rows})
    ci = {c: i for i, c in enumerate(cats)}
    cols = {k: [] for k in ('id', 'nm', 'ko', 'en', 'ja', 'zh', 'c', 'dla', 'dlo', 'a', 'h', 'url', 'p', 'tel', 'wd', 'src', 'tr', 'rk', 'hd', 'wid', 'lic')}
    pla = plo = 0
    for r in rows:
        la, lo = round(r['lat'] * 1e5), round(r['lon'] * 1e5)
        cols['dla'].append(la - pla); cols['dlo'].append(lo - plo); pla, plo = la, lo
        cols['id'].append(r['id']); cols['c'].append(ci[r.get('c') or 'other'])
        for k in ('nm', 'ko', 'en', 'ja', 'zh', 'a', 'h', 'url', 'p', 'tel', 'wd', 'wid', 'lic'):
            cols[k].append(r.get(k))
        cols['src'].append('|'.join(r.get('srcs') or [r['src']])); cols['tr'].append(r.get('tr', 1))
        cols['rk'].append('o' if r.get('rank') == 'official' else None); cols['hd'].append(r.get('hd'))
    return {'schema': 'collect-1', 'dataset': 'places', 'cc': cc, 'built': today(), 'n': len(rows), 'cats': cats, **cols, 'attribution': attribution(rows)}


# ---------- 3단: 품질 관문 + 게시 ----------
def gate(layers, prev_counts, counts):
    """layers = {파일: dict}. 실패 사유 list(비면 통과)."""
    return gate_stream(list(layers), layers.__getitem__, prev_counts, counts)


def gate_stream(files, load, prev_counts, counts):
    """gate 와 같은 검사·같은 표본(씨앗 20)을 파일 하나씩 읽어서 한다 — load(파일) → dict. 층 전체를 메모리에 올리지 않는다."""
    errs = []
    for cc, n in prev_counts.items():
        if n and counts.get(cc, 0) < n * 0.95:
            errs.append(f'{cc}: 장소 수 {n} → {counts.get(cc, 0)} (-5% 넘게 감소)')
    spans = []  # (파일, 행 수) — 표본을 뽑을 모집단(파일 순서 · 행 순서)
    for f in files:
        j = load(f)
        if j.get('schema') != 'collect-1':
            errs.append(f'{f}: schema'); continue
        if j.get('dataset') != 'places':
            if not isinstance(j.get('rows'), dict) or not j['rows']:
                errs.append(f'{f}: info rows 없음')
            continue
        n = len(j['id'])
        if any(len(j[k]) != n for k in ('nm', 'dla', 'dlo', 'c', 'src', 'lic')):
            errs.append(f'{f}: 열 길이 다름'); continue
        la = lo = 0; bad = miss = garb = forb = 0
        for i in range(n):
            la += j['dla'][i]; lo += j['dlo'][i]
            if not valid_coord(la / 1e5, lo / 1e5):
                bad += 1
            if not j['id'][i] or not j['nm'][i] or not j['src'][i] or not j['lic'][i]:
                miss += 1
            if any(bad_text(j[k][i]) for k in ('nm', 'ko', 'en', 'ja', 'zh')):
                garb += 1
            if j['url'][i] and forbidden(j['url'][i]):
                forb += 1
        spans.append((f, n))
        for name, v in (('좌표 범위 밖', bad), ('필수 칸 빠짐', miss), ('글자 깨짐', garb), ('금지 도메인', forb)):
            if v:
                errs.append(f'{f}: {name} {v}행')
    total = sum(n for _, n in spans)
    cache = {}
    for g in random.Random(20).sample(range(total), min(20, total)):  # sample(list) 와 같은 자리를 고른다(자리는 모집단 길이에만 달림)
        for f, n in spans:
            if g < n:
                break
            g -= n
        i = g
        j = cache[f] if f in cache else cache.setdefault(f, load(f))
        if not (isinstance(j['id'][i], str) and isinstance(j['nm'][i], str) and isinstance(j['c'][i], int) and 0 <= j['c'][i] < len(j['cats'])
                and isinstance(j['tr'][i], int) and all(j[k][i] is None or isinstance(j[k][i], str) for k in ('ko', 'en', 'ja', 'zh', 'url', 'h'))):
            errs.append(f'{f}#{i}: 표본 모양 틀림')
    return errs


def publish(store, bucket, kms_key, dry_run=False, tools='tools', current_dir=None, keep=3):
    latest = store.get_json('processed/latest.json', None)
    if not latest:
        return None, ['가공 결과 없음']
    hist = store.get_json('published/history.json', []) or []
    if hist and hist[-1]['run'] == latest['run']:
        return None, []  # 이미 게시한 판
    # 층 파일은 한꺼번에 받아 두고(로컬 파일), 관문은 하나씩 읽어 검사한다 — 전부를 메모리에 올리지 않는다
    paths = layer_paths(latest)
    ld = tempfile.mkdtemp(dir=os.environ.get('RUNNER_TEMP') or None)
    by_dir = {}
    for f in latest['files']:
        by_dir.setdefault(paths[f].rsplit('/', 1)[0], []).append(paths[f])
    for ks in by_dir.values():
        store.pull(ks, ld, strict=False)

    def load(f):
        p = os.path.join(ld, f)
        return json.loads(gzip.decompress(open(p, 'rb').read())) if os.path.exists(p) else {}

    errs = gate_stream(latest['files'], load, hist[-1]['counts'] if hist else {}, latest['counts'])
    if errs:
        return None, errs
    tag = 'c' + latest['run']
    d = tempfile.mkdtemp(dir=os.environ.get('RUNNER_TEMP') or None)
    if current_dir:  # 시험: 지금 판의 층 파일이 든 폴더
        for f in glob.glob(os.path.join(current_dir, '*.json.gz')):
            if not os.path.basename(f).startswith('col_'):
                os.link(f, os.path.join(d, os.path.basename(f)))
    else:  # 지금 앱 목록(places/v1/layers.json)이 가리키는 층 + layers_extra 를 모두 함께 실어 다른 층이 빠지지 않게
        cur = subprocess.run(['gcloud', 'storage', 'cat', f'gs://{bucket}/places/v1/layers.json'], capture_output=True)
        if cur.returncode == 0:
            m = json.loads(cur.stdout)
            names = [e['file'] for e in m.get('layers', []) if not e['file'].startswith('col_')]
            if names:
                subprocess.run(['gcloud', 'storage', 'cp', '-q', *[f"gs://{bucket}/{m['path']}{n}" for n in names], d], check=True, capture_output=True)
        subprocess.run(['gcloud', 'storage', 'cp', '-q', f'gs://{bucket}/places/v1/layers_extra/*.json.gz', d], capture_output=True)
    for f in latest['files']:
        os.replace(os.path.join(ld, f), os.path.join(d, f))
    cmd = [sys.executable, os.path.join(HERE, '..', 'publish_layers.py'), '--tag', tag, '--dir', d, '--bucket', bucket, '--tools', tools]
    cmd += ['--dry-run'] if dry_run else ['--kms-key', kms_key]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return None, ['publish_layers 실패: ' + (r.stderr or r.stdout)[-300:]]
    hist.append({'tag': tag, 'run': latest['run'], 'counts': latest['counts'], 'at': now().isoformat(), 'dry': dry_run})
    old = [h for h in hist if not h.get('dry')][:-keep]
    if not dry_run:
        for h in old:  # 직전 3개 판만 남긴다(수집이 만든 c… 태그만)
            subprocess.run(['gcloud', 'storage', 'rm', '-r', '-q', f"gs://{bucket}/places/v1/{h['tag']}/"], capture_output=True)
        hist = [h for h in hist if h not in old]
    store.put_json('published/history.json', hist[-20:])
    return tag, r.stdout.splitlines()[:3]


def _summary(lines):
    p = os.environ.get('GITHUB_STEP_SUMMARY')
    if p:
        open(p, 'a', encoding='utf-8').write('\n'.join(lines) + '\n')
    print('\n'.join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['process', 'publish'])
    ap.add_argument('--store', required=True); ap.add_argument('--world', default='')
    ap.add_argument('--bucket', default=''); ap.add_argument('--kms-key', default=''); ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--alert', default=''); ap.add_argument('--tools', default='tools')
    ap.add_argument('--budget-min', type=float, default=BUDGET_MIN); ap.add_argument('--force-all', action='store_true')
    ap.add_argument('--stage', choices=['all', 'partition', 'countries', 'merge'], default='all')
    ap.add_argument('--shard', default='', help='countries 단계: i/n (cc_shard 가 i 인 나라만)')
    ap.add_argument('--batch', default='', help='같은 워크플로 실행의 조각끼리 맞추는 표(run_id)')
    a = ap.parse_args()
    store = Store(a.store)
    if a.cmd == 'process':
        shard = tuple(int(x) for x in a.shard.split('/')) if a.shard else None
        if shard and not (len(shard) == 2 and 0 <= shard[0] < shard[1]):
            ap.error('--shard 는 i/n (0 <= i < n)')
        run, st = process(store, a.world or None, force_all=a.force_all, budget_min=a.budget_min, stage=a.stage, shard=shard, batch=a.batch)
        pend = st['pending']
        _summary([f'## 가공 {run}' + (f' ({a.stage}' + (f' {a.shard}' if a.shard else '') + ')' if a.stage != 'all' else ''), f"- 새 원본 파일 {st['read_files']}개 · 행 {st['read_rows']:,} · {st.get('sec', 0)}초 · 최대 메모리(RSS) {st.get('peak_rss_mb', 0):,}MB",
                  f"- 다음 실행으로 넘긴 일: 원본 파일 {pend['raw_files']}개 · 나라 {len(pend['countries'])}개" + (f" ({', '.join(pend['countries'][:20])}…)" if pend['countries'] else ''),
                  '| 나라 | 장소 | 타일과 일치 | 여러 출처 | 공식 |', '|---|---|---|---|---|']
                 + [f"| {cc} | {s['places']:,} | {s['matched_world']:,} | {s['multi_src']:,} | {s['official']:,} |" for cc, s in sorted(st['countries'].items())])
        return 0
    tag, msg = publish(store, a.bucket, a.kms_key, a.dry_run, a.tools)
    if tag:
        _summary([f'## 게시 {tag}', *[f'- {m}' for m in msg], f'- 되돌리기: `gcloud storage cp gs://{a.bucket}/places/v1/<이전태그>/layers.json gs://{a.bucket}/places/v1/layers.json`'])
        return 0
    if msg:
        _summary(['## 게시 중단(품질 관문)', *[f'- {m}' for m in msg[:40]]])
        if a.alert:
            open(a.alert, 'w', encoding='utf-8').write('수집 게시가 품질 관문에서 멈췄습니다(자동 생성).\n\n' + '\n'.join(f'- {m}' for m in msg[:40]) + '\n')
        return 1
    _summary(['## 게시: 새 가공 판 없음'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
