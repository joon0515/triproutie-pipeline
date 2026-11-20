#!/usr/bin/env python3
"""(299기) GCS 버킷 정리 — '내용이 완전히 같은 사본'(dup)과, 따로 고른 '참조 없는 옛 판'(old) 목록을 만들고, 확인된 목록만 지운다.

객체 내용은 받지 않는다(egress 0): 목록(이름·크기·md5·crc32c·generation·만든 시각)만으로 같은 내용을 찾는다.
작은 포인터 json(최신 layers.json·manifest.json·latest.json·history.json 과 그것들이 가리키는 2단 목록·지역 목록)만 읽는다 — 읽은 바이트 합 상한 READ_CAP(50 MB).

지울 수 있는 자리(후보)는 '판마다 새로 만드는 폴더' 둘뿐이다 — 그 밖의 경로는 워크플로가 이름 규칙으로 읽으므로(띠 누적본·layers_extra·상태·캐시·coverage 등) 절대 후보가 아니다.
  - places/v1/<판>/**      (<판> = 그 바로 아래 layers.json 또는 manifest.json 이 있는 폴더, 또는 게시 이력의 태그)
  - collect/v1/processed/out/<run>/**
후보에서 늘 빼는 것: collect/v1/raw/**(수집 원본) · 0바이트 · .json(포인터) · 이름이 latest 로 시작 ·
  보존(아래)에 걸린 경로.
보존(지우지 않음):
  ① 최신 places/v1/layers.json 의 판 폴더 전체 + 그 목록(뿌리·2단·3단)이 가리키는 모든 층 파일
  ② 최신 places/v1/manifest.json(세계 타일)의 판 폴더 전체 + 지역 목록·타일 전부
  ③ collect/v1/processed/latest.json 의 paths 전부 + out/<그 run>/ 폴더 전체(finalize_layers.sh 가 이름 규칙으로 읽음)
     + 합치기 전 조각 결과(processed/shards/latest_<i>.json)의 층 + latest 보다 새 run 폴더(하다 만 실행)
  ④ 되돌리기용 바로 이전 게시 판(published/history.json 의 이전 태그 · 만든 시각으로 본 이전 layers 판·이전 세계 판) 폴더 전체 + 그 뿌리 목록의 path·parts_path
dup: 같은 (md5, size) 묶음(crc32c 도 같아야 함)에서 후보가 아닌 사본이 하나라도 있으면 후보 사본을 모두 지운다. 모두 후보이면 가장 최근에 만든 하나를 남긴다.
old: dup 뒤에도 남는 후보 전부(참조 없는 옛 판, 내용이 달라도). 지우기는 workflow 에서 따로 확인 문구를 넣어야 한다.

쓰는 법
  plan   --bucket B --out DIR [--listing L.jsonl --fake-root D]   → DIR/plan.json(+ sha256 출력) · delete_dup.txt · delete_old.txt · protected_refs.txt · summary.md
  delete --bucket B --plan DIR/plan.json --sha256 HEX --scope dup|old [--confirm-old YES-OLD]  → 그 목록만, generation 이 같을 때만 지움(다시 돌려도 같은 결과)
  verify --bucket B --out DIR   → 최신 포인터가 가리키는 모든 경로가 있는지(목록만) + 접두어별 크기
"""
import argparse, datetime, hashlib, json, os, re, subprocess, sys

READ_CAP = 50 * 1000 * 1000
RAW = 'collect/v1/raw/'
OUT = 'collect/v1/processed/out/'
PV = 'places/v1/'
NON_TAG = {'layers_extra', 'transit', 'shop', 'hours_src', 'layer_parts'}
USD_GIB_MONTH = 0.026   # storage_guard.py 와 같은 어림(지역 Standard + 여유)
KRW_USD = 1325          # COST_295 청구서 대조 환율
FREE_GIB = 5 / 1.073741824


# ---------- 읽기(작은 json 만) ----------
class Reader:
    def __init__(self, bucket, fake_root=None, cap=READ_CAP):
        self.bucket, self.fake, self.cap, self.bytes, self.calls = bucket, fake_root, cap, 0, 0

    def __call__(self, name):
        if self.fake is not None:
            p = os.path.join(self.fake, name)
            b = open(p, 'rb').read() if os.path.isfile(p) else None
        else:
            r = subprocess.run(['gcloud', 'storage', 'cat', f'gs://{self.bucket}/{name}'], capture_output=True)
            b = r.stdout if r.returncode == 0 else None
        self.calls += 1
        self.bytes += len(b or b'')
        if self.bytes > self.cap:
            raise SystemExit(f'읽기 상한 {self.cap:,} B 넘음({self.bytes:,} B) — 멈춤')
        return b

    def json(self, name):
        b = self(name)
        return json.loads(b) if b else None


def tag_of(path):
    """'places/v1/<tag>/...' → <tag>."""
    m = re.match(r'^places/v1/([^/]+)/', path or '')
    return m.group(1) if m else None


# ---------- 목록(메타데이터만) ----------
def list_gcs(bucket):
    """돌려줌 = (객체 목록, 목록 요청(Class A) 수). 필드는 꼭 필요한 것만(응답 크기 최소)."""
    from google.cloud import storage
    c = storage.Client()
    it = c.list_blobs(bucket, page_size=1000, fields='items(name,size,md5Hash,crc32c,generation,timeCreated),nextPageToken')
    out, pages = [], 0
    for page in it.pages:
        pages += 1
        for b in page:
            out.append({'name': b.name, 'size': int(b.size or 0), 'md5': b.md5_hash, 'crc': b.crc32c, 'gen': int(b.generation or 0),
                        't': b.time_created.isoformat() if b.time_created else ''})
    return out, pages


def list_file(path):
    rows = [json.loads(l) for l in open(path, encoding='utf-8') if l.strip()]
    return rows, max(1, -(-len(rows) // 1000))


# ---------- 참조(보존) ----------
def walk_layers(read, root, exact, errs, label):
    """layers.json 뿌리 → 층 파일·2단·3단 목록 이름을 exact 에. sha256·크기가 틀리면 errs."""
    base = root.get('path') or ''
    pp = root.get('parts_path') or ''
    for e in root.get('layers', []):
        if e.get('file'):
            exact.add(base + e['file'])

    def walk(p, depth=0):
        name = pp + p['file']
        exact.add(name)
        b = read(name)
        if b is None or len(b) != p.get('size') or hashlib.sha256(b).hexdigest() != p.get('sha256'):
            errs.append(f'{label}: 2단 목록 {name} 못 읽음 또는 sha256 틀림'); return
        j = json.loads(b)
        if p.get('index'):
            for q in j.get('parts', []):
                walk(q, depth + 1)
        else:
            for e in j.get('layers', []):
                if e.get('file'):
                    exact.add((j.get('path') or base) + e['file'])
    for p in root.get('parts', []):
        walk(p)


def resolve(read, objs):
    """돌려줌 = dict(exact=참조 이름 집합, prefixes=통째 보존 폴더, tags=판 폴더, info=…, errs=…)."""
    names = {o['name']: o for o in objs}
    exact, prefixes, errs, info = set(), set(), [], {}
    # ① 앱 층 목록
    L = read.json(PV + 'layers.json')
    if not L or not L.get('path'):
        errs.append('places/v1/layers.json 못 읽음')
    else:
        exact.add(PV + 'layers.json')
        t = tag_of(L['path']); info['layers_tag'] = t
        prefixes.add(f'{PV}{t}/')
        for k in ('path', 'parts_path'):
            if L.get(k):
                prefixes.add(L[k])
        walk_layers(read, L, exact, errs, 'layers')
    # ② 세계 타일
    M = read.json(PV + 'manifest.json')
    if not M or not M.get('path'):
        errs.append('places/v1/manifest.json 못 읽음')
    else:
        exact.add(PV + 'manifest.json')
        t = tag_of(M['path']); info['world_tag'] = t
        prefixes.add(f'{PV}{t}/')
        for r in M.get('regions', []):
            exact.add(r['path'])
            b = read(r['path'])
            if b is None or (r.get('sha256') and hashlib.sha256(b).hexdigest() != r['sha256']):
                errs.append(f"지역 목록 {r['path']} 못 읽음 또는 sha256 틀림"); continue
            rj = json.loads(b)
            prefixes.add(rj.get('path') or M['path'])
            for tl in rj.get('tiles', []):
                if isinstance(tl.get('file'), str):
                    exact.add((rj.get('path') or M['path']) + tl['file'])
    # ③ 가공 최신 판
    P = read.json('collect/v1/processed/latest.json')
    if not P:
        errs.append('collect/v1/processed/latest.json 못 읽음')
    else:
        exact.add('collect/v1/processed/latest.json')
        paths = dict(P.get('paths') or {})
        for f in P.get('files', []):
            paths.setdefault(f, f"processed/out/{P['run']}/{f}")
        exact.update('collect/v1/' + v for v in paths.values())
        if P.get('run'):
            prefixes.add(f"{OUT}{P['run']}/")
        info['latest_run'] = P.get('run')
        # 아직 합치지 않은 나라 조각 결과(processed/shards/latest_<i>.json)가 가리키는 층 · latest 보다 새 run 폴더(하다 만 실행)도 보존
        for n in sorted(x for x in names if re.match(r'^collect/v1/processed/shards/latest_[^/]+\.json$', x)):
            j = read.json(n) or {}
            for v in (j.get('cc') or {}).values():
                exact.update('collect/v1/' + q for q in (v.get('files') or {}).values())
        for n in names:
            m = re.match(rf'^{re.escape(OUT)}([^/]+)/', n)
            if m and P.get('run') and m.group(1) > P['run']:
                prefixes.add(f'{OUT}{m.group(1)}/')
    # 판 폴더(후보 자리)
    tags = {m.group(1) for n in names for m in [re.match(r'^places/v1/([^/]+)/(layers|manifest)\.json$', n)] if m} - NON_TAG
    H = read.json('collect/v1/published/history.json') or []
    htags = [h['tag'] for h in H if isinstance(h, dict) and h.get('tag') and not h.get('dry')]
    tags |= set(htags) - NON_TAG
    # ④ 되돌리기용 이전 판
    prev = set()
    cur_l, cur_w = info.get('layers_tag'), info.get('world_tag')
    hp = [t for t in htags if t != cur_l]
    if hp:
        prev.add(hp[-1])

    def by_time(fname, cur):
        c = sorted(((names[n]['t'], tag_of(n)) for n in names if re.match(rf'^places/v1/[^/]+/{fname}$', n) and tag_of(n) not in NON_TAG), reverse=True)
        tc = next((tt for tt, g in c if g == cur), None)
        older = [g for tt, g in c if g != cur and (tc is None or tt <= tc)]
        return older[0] if older else None
    for p in (by_time('layers.json', cur_l), by_time('manifest.json', cur_w)):
        if p:
            prev.add(p)
    for p in sorted(prev):
        prefixes.add(f'{PV}{p}/')
        R = read.json(f'{PV}{p}/layers.json')
        if R:
            for k in ('path', 'parts_path'):
                if R.get(k):
                    prefixes.add(R[k])
    info['prev_tags'] = sorted(prev)
    info['tags'] = sorted(tags)
    return {'exact': exact, 'prefixes': sorted(prefixes), 'tags': tags, 'info': info, 'errs': errs}


def protected(name, ref):
    return name in ref['exact'] or any(name.startswith(p) for p in ref['prefixes'])


def candidate(o, ref):
    n = o['name']
    if n.startswith(RAW) or o['size'] <= 0 or n.endswith('.json') or n.rsplit('/', 1)[-1].startswith('latest') or n.endswith('/'):
        return False
    if protected(n, ref):
        return False
    t = tag_of(n)
    return (t is not None and t in ref['tags']) or bool(re.match(rf'^{re.escape(OUT)}[^/]+/.', n))


# ---------- 계획 ----------
def plan(objs, ref):
    groups = {}
    for o in objs:
        if o.get('md5') and o['size'] > 0:
            groups.setdefault((o['md5'], o['size']), []).append(o)
    dup, odd, ngroups, ngroups_del = [], 0, 0, 0
    for k, g in groups.items():
        if len(g) < 2:
            continue
        if len({o.get('crc') for o in g}) != 1:
            odd += 1; continue
        ngroups += 1
        cands = [o for o in g if candidate(o, ref)]
        keepers = [o for o in g if not candidate(o, ref)]
        if not cands:
            continue
        if not keepers:
            keep = max(cands, key=lambda o: (o['t'], o['name']))
            cands = [o for o in cands if o is not keep]
        if cands:
            ngroups_del += 1
            dup.extend(cands)
    dn = {o['name'] for o in dup}
    old = [o for o in objs if candidate(o, ref) and o['name'] not in dn]
    return dup, old, {'dup_groups': ngroups, 'dup_groups_with_delete': ngroups_del, 'crc_mismatch_groups': odd}


def check(objs, ref, dels):
    """보존·raw 와 겹치는 지울 목록 수(0 이어야 함)."""
    return {'overlap_protected': sum(1 for o in dels if protected(o['name'], ref)),
            'overlap_raw': sum(1 for o in dels if o['name'].startswith(RAW)),
            'overlap_json_or_zero': sum(1 for o in dels if o['name'].endswith('.json') or o['size'] <= 0)}


def gib(n):
    return n / 2 ** 30


def krw(g):
    return max(0.0, g - FREE_GIB) * USD_GIB_MONTH * KRW_USD


def by_prefix(objs):
    out = {}
    for o in objs:
        n = o['name']
        k = next((p for p in (RAW, 'collect/v1/processed/bands/', OUT, PV) if n.startswith(p)), 'other')
        out[k] = out.get(k, 0) + o['size']
    return {k: round(gib(v), 3) for k, v in sorted(out.items())}


def cmd_plan(a):
    objs, pages = list_file(a.listing) if a.listing else list_gcs(a.bucket)
    read = Reader(a.bucket, a.fake_root)
    ref = resolve(read, objs)
    os.makedirs(a.out, exist_ok=True)
    if ref['errs']:
        open(os.path.join(a.out, 'summary.md'), 'w').write('## dedup-gcs 계획 실패 — 포인터를 못 읽음(아무것도 지우지 않음)\n' + '\n'.join('- ' + e for e in ref['errs'][:20]) + '\n')
        print('\n'.join(ref['errs'][:20]), file=sys.stderr)
        return 2
    dup, old, st = plan(objs, ref)
    ck = {'dup': check(objs, ref, dup), 'old': check(objs, ref, old)}
    total = sum(o['size'] for o in objs)
    dsz, osz = sum(o['size'] for o in dup), sum(o['size'] for o in old)
    missing = sorted(n for n in ref['exact'] if n not in {o['name'] for o in objs})
    slim = lambda L: [{'name': o['name'], 'gen': o['gen'], 'size': o['size'], 'md5': o['md5']} for o in sorted(L, key=lambda o: o['name'])]
    body = {'schema': 'dedup-gcs-plan-1', 'bucket': a.bucket, 'at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
            'info': ref['info'], 'prefixes': ref['prefixes'], 'n_exact_refs': len(ref['exact']), 'missing_refs_before': len(missing),
            'objects': len(objs), 'list_requests_class_a': pages, 'read_bytes': read.bytes, 'read_calls': read.calls,
            'total_bytes': total, 'stats': st, 'checks': ck, 'by_prefix_gib': by_prefix(objs),
            'dup': slim(dup), 'old': slim(old)}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
    open(os.path.join(a.out, 'plan.json'), 'wb').write(raw)
    sha = hashlib.sha256(raw).hexdigest()
    open(os.path.join(a.out, 'plan.sha256'), 'w').write(sha + '\n')
    open(os.path.join(a.out, 'delete_dup.txt'), 'w').write(''.join(o['name'] + '\n' for o in body['dup']))
    open(os.path.join(a.out, 'delete_old.txt'), 'w').write(''.join(o['name'] + '\n' for o in body['old']))
    open(os.path.join(a.out, 'protected_refs.txt'), 'w').write(''.join(n + '\n' for n in sorted(ref['exact'])) + ''.join('PREFIX ' + p + '\n' for p in ref['prefixes']))
    cost_a = pages * 0.005 / 1000 * KRW_USD
    g0, g1, g2 = gib(total), gib(total - dsz), gib(total - dsz - osz)
    md = [f'## dedup-gcs 계획 (dry) — {body["at"]}',
          f'- 객체 {len(objs):,}개 · {g0:.2f} GiB · 목록 요청(Class A) {pages:,}건 ≈ ₩{cost_a:.2f} · 포인터 읽기 {read.calls}번 {read.bytes:,} B',
          f'- 판: layers {ref["info"].get("layers_tag")} · world {ref["info"].get("world_tag")} · 이전(되돌리기) {", ".join(ref["info"]["prev_tags"]) or "-"} · latest run {ref["info"].get("latest_run")} · 판 폴더 {len(ref["tags"])}개',
          f'- 참조 경로 {len(ref["exact"]):,}개(목록에 없는 것 {len(missing)}) · 통째 보존 폴더 {len(ref["prefixes"])}개',
          f'- 접두어별 GiB: {body["by_prefix_gib"]}',
          f'- **dup**: 같은 내용 묶음 {st["dup_groups"]:,}개(지울 것 있는 묶음 {st["dup_groups_with_delete"]:,}) · 지울 객체 {len(dup):,}개 · {gib(dsz):.3f} GiB · crc 불일치 묶음 {st["crc_mismatch_groups"]}',
          f'- **old**(참조 없는 옛 판, dup 뒤): 객체 {len(old):,}개 · {gib(osz):.3f} GiB',
          f'- 겹침 검사(0 이어야 함): dup {ck["dup"]} · old {ck["old"]}',
          f'- 남는 크기·월 저장 요금(어림 ${USD_GIB_MONTH}/GiB, 무료 5 GB 뺌): 지금 {g0:.2f} GiB ₩{krw(g0):,.0f} → dup 뒤 {g1:.2f} GiB ₩{krw(g1):,.0f} → dup+old 뒤 {g2:.2f} GiB ₩{krw(g2):,.0f}',
          f'- plan.json sha256 `{sha}`']
    open(os.path.join(a.out, 'summary.md'), 'w').write('\n'.join(md) + '\n')
    print('\n'.join(md))
    bad = any(v for c in ck.values() for v in c.values())
    return 3 if bad else 0


# ---------- 지우기 ----------
def cmd_delete(a):
    raw = open(a.plan, 'rb').read()
    if hashlib.sha256(raw).hexdigest() != a.sha256.strip().lower():
        raise SystemExit('plan.json sha256 이 입력과 다름 — 지우지 않음')
    P = json.loads(raw)
    if P['bucket'] != a.bucket:
        raise SystemExit('plan 의 버킷이 다름')
    if a.scope == 'old' and a.confirm_old != 'YES-OLD':
        raise SystemExit('old 지우기는 confirm_old=YES-OLD 가 있어야 함')
    items = P[a.scope]
    # 지금 다시 보존 목록을 만들어(계획 뒤 게시가 있었을 수 있음) 겹치면 멈춘다
    objs, pages = list_file(a.listing) if a.listing else list_gcs(a.bucket)
    read = Reader(a.bucket, a.fake_root)
    ref = resolve(read, objs)
    if ref['errs']:
        raise SystemExit('포인터 못 읽음 — 지우지 않음: ' + '; '.join(ref['errs'][:5]))
    bad = [o['name'] for o in items if protected(o['name'], ref) or o['name'].startswith(RAW)]
    if bad:
        raise SystemExit(f'지금 보존 대상과 겹침 {len(bad)}개(예: {bad[0]}) — 지우지 않음')
    cur = {o['name']: o for o in objs}
    if a.scope == 'dup':  # 남길 사본이 아직 있는지(같은 md5·size 의 다른 이름)
        keep = {}
        dn = {o['name'] for o in items}
        for o in objs:
            if o['name'] not in dn and o.get('md5'):
                keep[(o['md5'], o['size'])] = keep.get((o['md5'], o['size']), 0) + 1
        lost = [o['name'] for o in items if not keep.get((o['md5'], o['size']))]
        if lost:
            raise SystemExit(f'남길 사본이 없어진 묶음 {len(lost)}개 — 지우지 않음')
    todo = [o for o in items if o['name'] in cur and cur[o['name']]['gen'] == o['gen']]
    gone = sum(1 for o in items if o['name'] not in cur)
    changed = len(items) - len(todo) - gone
    deleted, failed, nbytes = delete_objs(a, todo)
    md = [f'## dedup-gcs 지우기 ({a.scope}) — {datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}',
          f'- 계획 {len(items):,}개 · 지움 {deleted:,}개 {gib(nbytes):.3f} GiB · 이미 없음 {gone} · 계획 뒤 바뀌어 건너뜀 {changed} · 실패 {failed}',
          f'- 다시 확인한 보존 목록과 겹침 0 · 목록 요청 {pages}건 · 포인터 읽기 {read.bytes:,} B']
    open(os.path.join(a.out, 'delete_summary.md'), 'w').write('\n'.join(md) + '\n')
    print('\n'.join(md))
    return 1 if failed else 0


def delete_objs(a, todo):
    if a.fake_root is not None:
        n = 0
        for o in todo:
            p = os.path.join(a.fake_root, o['name'])
            if os.path.exists(p):
                os.remove(p); n += o['size']
        if a.listing:  # 시험: 목록 파일에서도 뺀다
            dn = {o['name'] for o in todo}
            rows = [l for l in open(a.listing, encoding='utf-8') if l.strip() and json.loads(l)['name'] not in dn]
            open(a.listing, 'w', encoding='utf-8').write(''.join(rows))
        return len(todo), 0, n
    from google.cloud import storage
    from google.api_core import exceptions as gx
    from concurrent.futures import ThreadPoolExecutor
    b = storage.Client().bucket(a.bucket)

    def one(o):  # generation 이 같을 때만 — 계획 뒤 새로 쓴 객체는 지우지 않는다(다시 돌려도 안전)
        for i in range(3):
            try:
                b.blob(o['name']).delete(if_generation_match=o['gen'])
                return 'ok'
            except (gx.NotFound, gx.PreconditionFailed):
                return 'skip'
            except Exception as e:  # noqa: BLE001 — 일시 오류는 3번까지, 그래도 안 되면 실패로 셈
                err = type(e).__name__
        return 'fail:' + err
    ok = failed = nb = 0
    with ThreadPoolExecutor(32) as ex:
        for o, r in zip(todo, ex.map(one, todo)):
            if r == 'ok':
                ok += 1; nb += o['size']
            elif r.startswith('fail'):
                failed += 1
                if failed <= 3:
                    print(f'지우기 실패 {o["name"]}: {r}', file=sys.stderr)
    return ok, failed, nb


# ---------- 확인 ----------
def cmd_verify(a):
    objs, pages = list_file(a.listing) if a.listing else list_gcs(a.bucket)
    read = Reader(a.bucket, a.fake_root)
    ref = resolve(read, objs)
    have = {o['name'] for o in objs}
    missing = sorted(n for n in ref['exact'] if n not in have)
    total = sum(o['size'] for o in objs)
    md = [f'## dedup-gcs 확인 — 최신 포인터가 가리키는 경로 {len(ref["exact"]):,}개 중 없는 것 {len(missing)}' + (f'(예: {missing[:3]})' if missing else ''),
          f'- 포인터 오류 {len(ref["errs"])} · 버킷 {len(objs):,}개 {gib(total):.2f} GiB · 월 저장 어림 ₩{krw(gib(total)):,.0f} · 접두어별 GiB {by_prefix(objs)} · 목록 요청 {pages}건']
    os.makedirs(a.out, exist_ok=True)
    open(os.path.join(a.out, 'verify_summary.md'), 'w').write('\n'.join(md) + '\n')
    print('\n'.join(md))
    return 1 if (missing or ref['errs']) else 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['plan', 'delete', 'verify'])
    ap.add_argument('--bucket', required=True)
    ap.add_argument('--out', default='.')
    ap.add_argument('--plan', default='')
    ap.add_argument('--sha256', default='')
    ap.add_argument('--scope', choices=['dup', 'old'], default='dup')
    ap.add_argument('--confirm-old', default='')
    ap.add_argument('--listing', default='', help='시험: 객체 목록 jsonl(name,size,md5,crc,gen,t)')
    ap.add_argument('--fake-root', default=None, help='시험: 포인터 json 을 읽을 로컬 폴더')
    a = ap.parse_args(argv)
    return {'plan': cmd_plan, 'delete': cmd_delete, 'verify': cmd_verify}[a.cmd](a)


if __name__ == '__main__':
    sys.exit(main())
