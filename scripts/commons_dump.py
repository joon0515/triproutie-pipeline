#!/usr/bin/env python3
"""Commons 구조화 데이터(SDC) 덤프로 사진 라이선스·저작자 판정 — QLever commons 끝점 대신. (2026-11-20, 270기)

  python3 scripts/commons_dump.py plan  --store gs://<b>/collect/v1 [--jobs 24 --procs 2]     # GITHUB_OUTPUT: matrix·any·meta
  python3 scripts/commons_dump.py scan  --store … --job K --jobs 24 --procs 2 --meta '<json>'
  python3 scripts/commons_dump.py merge --store … --meta '<json>'                               # → processed/photos/commons.json.gz

왜(원인, 11/20 263·270기): Commons 판정을 QLever wikimedia-commons 끝점에 묻는데 429 뒤 쉼이 하루라 도시 사진 0/38 · 장소 판정 남음 281.
  commons.wikimedia.org/w/api.php 는 robots.txt 가 User-agent * 에 Disallow: /w/ 라 쓰지 않는다.
받는 곳: 공식 덤프 https://dumps.wikimedia.org/other/wikibase/commonswiki/latest-mediainfo.json.bz2 (약 62GB, 한 스트림 bz2 — wd_dump.py 와 같은 블록 나누기·흘려 읽기).
  항목 = {"id":"M…","title":"File:…","statements":{P275 라이선스, P170 저작자(+ P2093 이름 · P4174 Commons 사용자 이름)}} — QLever 가 내주던 것과 같은 SDC 값.
판정 = place_names.judge 그대로: CC0·CC BY·CC BY-SA(판 번호 무관)이고 저작자(이름 또는 사용자 이름)가 있어야 싣는다. 그 밖은 {}(싣지 않음).
씨앗 = Wikidata 덤프 조인(processed/desc/wd_dump.json.gz)·place-desc 캐시(wd.json.gz)의 P18 + 도시 대표 항목(processed/photos/city_items.json) 중
  아직 판정이 없거나 90일 지난 파일. plan 이 processed/photos/cdump/want.json.gz 로 고정해 scan 조각이 같은 목록을 본다.
결과 꼴 = place_names 판정 캐시 {파일 주소: [덤프 날짜, 판정]} → processed/photos/commons.json.gz 에 더 새것만 덮어 쓴다(같은 입력이면 같은 결과).
사진 파일은 받지 않는다(링크만). 장소 내용·파일 이름은 로그에 쓰지 않는다(개수만).
"""
import argparse, datetime, json, os, re, sys, time, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, 'collect'))
import wd_dump as WD  # noqa: E402

DUMP = 'https://dumps.wikimedia.org/other/wikibase/commonswiki/latest-mediainfo.json.bz2'
FILEPATH = WD.FILEPATH
TITLE_RE = re.compile(rb'"title": ?"File:((?:[^"\\]|\\.)*)"')
WANT = set()
REFRESH = 90
POV_M = 50.0
TGRID = {}       # (270기) 촬영 좌표로 사진을 찾을 장소 격자 — photo_pri 가 남긴 processed/photos/targets.json.gz


def log(m):
    print(f'[commons_dump] {m}', file=sys.stderr, flush=True)


def url_of(title):
    """덤프 제목(파일 이름, 공백 그대로) → wd_dump·QLever 와 같은 P18 주소 꼴."""
    return FILEPATH + urllib.parse.quote(title, safe='')


def sdc(ent):
    """mediainfo 항목 → place_names.judge 입력 {'lic': {QID}, 'an': {이름}, 'un': {사용자}}."""
    st = ent.get('statements') or {}
    e = {'lic': set(), 'an': set(), 'un': set()}
    for c in st.get('P275') or []:
        if c.get('rank') == 'deprecated':
            continue
        v = ((c.get('mainsnak') or {}).get('datavalue') or {}).get('value')
        if isinstance(v, dict) and v.get('id'):
            e['lic'].add(v['id'])
    for c in st.get('P170') or []:
        q = c.get('qualifiers') or {}
        for p, k in (('P2093', 'an'), ('P4174', 'un')):
            for s in q.get(p) or []:
                v = (s.get('datavalue') or {}).get('value')
                if isinstance(v, str) and v.strip():
                    e[k].add(v.strip())
    return e


def set_targets(cities):
    """targets.json.gz 의 cities → 격자(0.001도 칸). 각 항목 (행 id, 위도, 경도, 정규화 이름 set, QID|None)."""
    TGRID.clear()
    for items in (cities or {}).values():
        for t in items or []:
            if isinstance(t, list) and len(t) >= 5:
                TGRID.setdefault((int(t[1] * 1000), int(t[2] * 1000)), []).append((t[0], t[1], t[2], set(t[3] or ()), t[4]))


def _norm(x):
    import unicodedata
    return re.sub(r'[\s\W_]+', '', unicodedata.normalize('NFKC', x or '').lower())


def name_hit(names, texts):
    """장소 이름(정규화)이 파일 이름·설명 글에 들어 있나. 영문·숫자만인 이름은 4글자, 그 밖(한자·가나 등)은 3글자 이상만(흔한 짧은 말 오탐 방지)."""
    for n in names:
        if len(n) >= (4 if n.isascii() else 3) and any(n in t for t in texts):
            return True
    return False


def pov_hits(ent, title):
    """mediainfo 항목 → [(행 id, 순위 0=묘사 대상 QID 일치 · 1=이름 일치, 거리 m)] — 촬영 좌표(P1259)·대상 좌표(P625) 50m 이내만."""
    import math
    st = ent.get('statements') or {}
    pts = []
    for pr in ('P1259', 'P625'):
        for c in st.get(pr) or []:
            v = ((c.get('mainsnak') or {}).get('datavalue') or {}).get('value')
            if isinstance(v, dict) and isinstance(v.get('latitude'), (int, float)) and isinstance(v.get('longitude'), (int, float)):
                pts.append((v['latitude'], v['longitude']))
    if not pts:
        return []
    dep = {((c.get('mainsnak') or {}).get('datavalue') or {}).get('value', {}).get('id') for c in st.get('P180') or []} - {None}
    texts = None
    out = {}
    for la, lo in pts:
        cy, cx = int(la * 1000), int(lo * 1000)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for rid, tla, tlo, names, q in TGRID.get((cy + dy, cx + dx), ()):
                    dm = 6371000.0 * math.hypot(math.radians(lo - tlo) * math.cos(math.radians(la)), math.radians(la - tla))
                    if dm > POV_M:
                        continue
                    if q and q in dep:
                        rank = 0
                    else:
                        if texts is None:
                            texts = [_norm(title.rsplit('.', 1)[0])] + [_norm(v.get('value')) for v in (ent.get('labels') or {}).values() if isinstance(v, dict)]
                        if not name_hit(names, texts):
                            continue
                        rank = 1
                    if rid not in out or (rank, dm) < out[rid]:
                        out[rid] = (rank, dm)
    return [(r, a, b) for r, (a, b) in out.items()]


class Sink:
    """풀린 줄 → 원하는 파일만 json 으로 풀어 판정. wd_dump.Lines 와 같은 앞·뒤 조각 규칙."""

    def __init__(self, want, day, drop_first):
        import place_names as PN
        self.judge, self.want, self.day, self.drop = PN.judge, want, day, drop_first
        self.rest, self.out, self.n, self.hit, self.bad = b'', {}, 0, 0, 0

    def line(self, ln):
        self.n += 1
        m = TITLE_RE.search(ln)
        if not m:
            return
        try:
            u = url_of(json.loads(b'"' + m.group(1) + b'"'))
        except ValueError:
            self.bad += 1
            return
        geo = TGRID and (b'"P1259"' in ln or b'"P625"' in ln)
        if u not in self.want and not geo:
            return
        try:
            ent = json.loads(ln.rstrip(b',\r\n '))
        except ValueError:
            self.bad += 1
            return
        j = None
        if u in self.want:
            j = self.judge(sdc(ent))
            self.out[u] = [self.day, j]
            self.hit += 1
        if geo:
            hits = pov_hits(ent, urllib.parse.unquote(u[len(FILEPATH):]))
            if hits:
                j = self.judge(sdc(ent)) if j is None else j
                if j:  # 자유 라이선스 + 저작자인 사진만 후보
                    pv = self.out.setdefault('_pov', {})
                    for rid, rank, dm in hits:
                        v = [rank, round(dm, 1), u, j]
                        if rid not in pv or (rank, dm, u) < (pv[rid][0], pv[rid][1], pv[rid][2]):
                            pv[rid] = v

    feed = WD.Lines.feed
    tail = WD.Lines.tail


def scan_part(args):
    p, parts, size, eos_bit, day, url = args
    opener = WD.http_opener(url)
    a, b = size * p // parts, size * (p + 1) // parts
    t0 = time.time()
    e = eos_bit
    if p < parts - 1:
        re_ = WD.Remote(max(0, b - 8), opener)
        e = min(WD.next_mark(re_, b * 8) or eos_bit, eos_bit)
        if re_.f is not None:
            re_.f.close()
    rd = WD.Remote(max(0, a - 8), opener)
    s = WD.next_mark(rd, a * 8)
    if s is None or s >= e:
        return {}, {'part': p, 'lines': 0, 'hit': 0, 'sec': 0}
    L = Sink(WANT, day, p > 0)
    WD.decode_range(rd, s, e, L.feed)
    cur, done = e, False
    while cur < eos_bit and not done:
        nxt = min(WD.next_mark(rd, cur + 1) or eos_bit, eos_bit)
        got = []
        WD.decode_range(rd, cur, nxt, lambda x: got.append(x) or False)
        done = L.tail(b''.join(got)) if got else False
        cur = nxt
    if e >= eos_bit and L.rest.strip():
        L.line(L.rest)
    return L.out, {'part': p, 'lines': L.n, 'hit': L.hit, 'bad': L.bad, 'sec': round(time.time() - t0), 'mb': round((e - s) / 8 / 2 ** 20)}


def want_files(store, now_day=None):
    """판정할 파일 주소(정렬). 판정이 없거나 90일 지난 것만."""
    d0 = datetime.date.fromisoformat(now_day or datetime.date.today().isoformat())
    urls = set()
    for k in ('processed/desc/wd_dump.json.gz', 'processed/desc/wd.json.gz'):
        for v in (store.get_json(k, None) or {}).values():
            if isinstance(v, list) and len(v) > 2 and isinstance(v[2], dict) and isinstance(v[2].get('p'), str) and v[2]['p'].startswith(FILEPATH):
                urls.add(v[2]['p'])
    for v in ((store.get_json('processed/photos/city_items.json', None) or {}).get('cities') or {}).values():
        for u in [v.get('p')] + list(v.get('files') or []):  # (270기 2차) 도시 후보 여러 장
            if isinstance(u, str) and u.startswith(FILEPATH):
                urls.add(u)
    for items in ((store.get_json('processed/photos/geo_items.json.gz', None) or {}).get('cities') or {}).values():
        for g in items:  # 도시 상자 안 좌표+P18 항목(해외 타일 행 이름 맞추기)
            if isinstance(g, list) and len(g) > 4 and isinstance(g[4], str) and g[4].startswith(FILEPATH):
                urls.add(g[4])
    known = {}
    for k in ('processed/desc/commons.json.gz', 'processed/photos/commons.json.gz'):
        for u, v in (store.get_json(k, None) or {}).items():
            if u not in known or known[u] < v[0]:
                known[u] = v[0]
    return sorted(u for u in urls if u not in known or (d0 - datetime.date.fromisoformat(known[u][:10])).days >= REFRESH), len(urls)


def main():
    from common import Store
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['plan', 'scan', 'merge'])
    ap.add_argument('--store', required=True)
    ap.add_argument('--jobs', type=int, default=24); ap.add_argument('--procs', type=int, default=2)
    ap.add_argument('--job', type=int, default=0); ap.add_argument('--meta', default='')
    a = ap.parse_args()
    store = Store(a.store)
    if a.cmd == 'plan':
        m = WD.meta_of(DUMP)
        want, total = want_files(store)
        store.put_json(f"processed/photos/cdump/{m['date']}/want.json.gz", want)
        tg = store.get_json('processed/photos/targets.json.gz', None) or {}
        import hashlib
        th = hashlib.sha1((m['date'] + json.dumps(tg.get('cities') or {}, sort_keys=True)).encode()).hexdigest()[:16] if tg.get('cities') else None
        pov_todo = th is not None and (store.get_json('processed/photos/cdump/state.json', None) or {}).get('pov') != th
        if pov_todo:
            store.put_json(f"processed/photos/cdump/{m['date']}/targets.json.gz", tg)
        m['want'], m['pov'] = len(want), th if pov_todo else None
        print('meta=' + json.dumps(m, separators=(',', ':')))
        print('matrix=' + json.dumps({'job': list(range(a.jobs))}, separators=(',', ':')))
        print(f"any={'true' if want or pov_todo else 'false'}")
        log(f"촬영 좌표로 찾을 장소 {sum(len(v) for v in (tg.get('cities') or {}).values()):,} · {'훑음' if pov_todo else '같은 덤프·같은 장소 — 건너뜀'}")
        log(f"덤프 {m['date']} · {m['size'] / 2 ** 30:.1f} GiB · P18 파일 {total:,} 중 판정할 것 {len(want):,}")
        return 0
    m = json.loads(open(a.meta).read()) if os.path.exists(a.meta) else json.loads(a.meta)
    if a.cmd == 'scan':
        WANT.update(store.get_json(f"processed/photos/cdump/{m['date']}/want.json.gz", []) or [])
        set_targets((store.get_json(f"processed/photos/cdump/{m['date']}/targets.json.gz", None) or {}).get('cities'))
        parts = a.jobs * a.procs
        import multiprocessing as mp
        with mp.get_context('fork').Pool(a.procs) as pool:
            res = pool.map(scan_part, [(p, parts, m['size'], m['eos_bit'], m['date'], m['url']) for p in (a.job * a.procs + i for i in range(a.procs))])
        out = {}
        for o, s in res:
            for rid, v in (o.pop('_pov', None) or {}).items():
                cur = out.setdefault('_pov', {}).get(rid)
                if cur is None or tuple(v[:3]) < tuple(cur[:3]):
                    out['_pov'][rid] = v
            out.update(o)
            log(f"조각 {s['part']}/{parts}: 줄 {s['lines']:,} · 맞은 파일 {s['hit']:,} · {s.get('mb', 0)} MB · {s['sec']}초")
        store.put_json(f"processed/photos/cdump/{m['date']}/job-{a.job:03d}.json.gz", out)
        return 0
    keys = [k for k in store.list(f"processed/photos/cdump/{m['date']}/") if k.rsplit('/', 1)[-1].startswith('job-')]
    got, pov = {}, {}
    for k in keys:
        part = store.get_json(k, {}) or {}
        for rid, v in (part.pop('_pov', None) or {}).items():
            if rid not in pov or tuple(v[:3]) < tuple(pov[rid][:3]):
                pov[rid] = v
        got.update(part)
    want = store.get_json(f"processed/photos/cdump/{m['date']}/want.json.gz", []) or []
    mine = store.get_json('processed/photos/commons.json.gz', {}) or {}
    for u, v in got.items():
        if u not in mine or mine[u][0] <= v[0]:
            mine[u] = v
    if len(keys) >= a.jobs:  # 조각이 다 왔을 때만 '덤프에 없음(지워진 파일)'을 빈 판정으로 적는다
        for u in want:
            if u not in got and (u not in mine or mine[u][0] < m['date']):
                mine[u] = [m['date'], {}]
    store.put_json('processed/photos/commons.json.gz', mine)
    if m.get('pov') and len(keys) >= a.jobs:
        store.put_json('processed/photos/pov.json.gz', {'date': m['date'], 'rows': pov})
        store.put_json('processed/photos/cdump/state.json', {'pov': m['pov'], 'rows': len(pov), 'at': datetime.datetime.now(datetime.timezone.utc).isoformat()})
    ok = sum(1 for v in got.values() if v[1])
    msg = (f"## Commons SDC 덤프 판정 {m['date']}\n- 조각 파일 {len(keys)}/{a.jobs} · 판정할 파일 {len(want):,} 중 덤프에서 찾음 {len(got):,}"
           f" · 실을 수 있음(CC0·CC BY·CC BY-SA + 저작자) {ok:,} · 판정 캐시 {len(mine):,}\n"
           f"- 촬영 좌표·묘사 대상으로 사진 찾은 장소 {len(pov):,}(묘사 대상 일치 {sum(1 for v in pov.values() if v[0] == 0):,} · 이름 일치 {sum(1 for v in pov.values() if v[0] == 1):,})\n")
    p = os.environ.get('GITHUB_STEP_SUMMARY')
    if p:
        open(p, 'a', encoding='utf-8').write(msg)
    print(msg)
    return 0


if __name__ == '__main__':
    sys.exit(main())
