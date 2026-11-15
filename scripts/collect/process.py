#!/usr/bin/env python3
"""수집 2단(가공)·3단(게시).

  python3 scripts/collect/process.py process --store gs://<b>/collect/v1 --world gs://<b>/places/v1/<tag>/tiles   # 정규화·중복 제거·병합·trust·나라별 층
  python3 scripts/collect/process.py publish --store ... --bucket <b> --kms-key ... [--dry-run]                     # 품질 관문 → publish_layers.py

가공: raw/ 에서 지난 가공 뒤 새로 들어온 파일만 읽어 나라별 누적본(processed/merged/<cc>.json.gz, id → 행)에 합친다.
  같은 곳 판정 = 정규화 이름이 같거나 한쪽이 다른 쪽을 품고(3자 이상) 좌표 50m 안. 여러 출처가 맞으면 tr(일치 출처 수) 상승,
  값이 다르면 공식 사이트(rank official) 값을 쓰고 영업시간이 다르면 hd=1 (앱 tl_trust 규칙: tr≥2 '확인됨', 공식 1곳 '공식').
  places-world 타일(앱에 이미 있는 장소)과 맞는 곳은 wid(타일 id)를 달아 '보강 행'으로, 안 맞는 곳은 '새 장소'로 싣는다.
  출력 = 타일과 같은 열 모양(id·nm·ko·en·ja·zh·c(cats 번호)·dla·dlo(1e5 누적 차분)·a …), schema 'collect-1', 파일 하나 ≤ 5만 행.
게시: 관문(나라별 장소 수 -5% 넘게 감소·좌표 범위·필수 칸·글자 깨짐·금지 도메인·표본 20곳 모양) 통과 때만 새 태그로 publish_layers.py.
  지금 앱 목록의 층 파일을 모두 함께 실어(다른 층이 빠지지 않게) 새 태그를 만들고, 직전 3개 판만 남긴다.
  되돌리기(한 줄): gcloud storage cp gs://<b>/places/v1/<이전태그>/layers.json gs://<b>/places/v1/layers.json
"""
import argparse, datetime, glob, gzip, json, math, os, random, re, subprocess, sys, tempfile

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
        out.append((j['id'][i], la / 1e5, lo / 1e5, [norm_name(j[k][i]) for k in ('nm', 'ko', 'en', 'ja', 'zh') if k in j and j[k][i]]))
    return out


def load_world(world, cc):
    """world = 로컬 폴더 또는 gs://…/tiles. 그 나라 타일(w-<셀>_<cc>.<지역>.json.gz)만."""
    idx = Index()
    if not world:
        return idx, 0
    if world.startswith('gs://'):
        d = tempfile.mkdtemp()
        subprocess.run(['gcloud', 'storage', 'cp', '-q', f'{world}/w-*_{cc}.*json.gz', d], capture_output=True)
        files = glob.glob(os.path.join(d, '*.json.gz'))
    else:
        files = glob.glob(os.path.join(world, f'w-*_{cc}.*json.gz')) + glob.glob(os.path.join(world, f'w-*_{cc}.json.gz'))
    n = 0
    for f in files:
        for wid, la, lo, ns in decode_tile(json.loads(gzip.decompress(open(f, 'rb').read()))):
            idx.add(wid, la, lo, ns); n += 1
    return idx, n


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


def process(store, world=None, force_all=False):
    pst = store.get_json('processed/state.json', {}) or {}
    seen = {} if force_all else (pst.get('seen') or {})  # 날짜 → 이미 가공한 원본 키(최근 7일만 기억)
    cutoff = (datetime.date.fromisoformat(today()) - datetime.timedelta(days=7)).isoformat()
    first = force_all or not pst.get('last_run')
    done = {k for v in seen.values() for k in v}
    keys = [k for k in store.list('raw/') if k.endswith('.json.gz')]
    new = [k for k in keys if k not in done and (first or k.split('/')[2] >= cutoff)]
    bycc, info, nread = {}, {}, 0
    for k in sorted(new):
        j = store.get_json(k, {}) or {}
        ds = j.get('dataset')
        for r in j.get('rows') or []:
            nread += 1
            if ds in PLACE_SETS:
                if not valid_coord(r.get('lat'), r.get('lon')) or not r.get('nm') or bad_text(r.get('nm')):
                    continue
                if r.get('url') and forbidden(r['url']):
                    r.pop('url')
                r['_ds'] = ds
                bycc.setdefault(cc_of(r), {})[r['id']] = r
            elif ds in INFO_SETS:
                info.setdefault(ds, {})[r['id']] = r
    stats = {'read_files': len(new), 'read_rows': nread, 'countries': {}}
    out_files = []
    for cc, rows in sorted(bycc.items()):
        merged = store.get_json(f'processed/merged/{cc}.json.gz', {}) or {}
        merged.update(rows)
        store.put_json(f'processed/merged/{cc}.json.gz', merged)
        widx, nworld = load_world(world, cc)
        # 출처 사이 같은 곳 묶기
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
        stats['countries'][cc] = {'raw': len(merged), 'places': len(out), 'matched_world': sum(1 for m in out if m.get('wid')), 'world': nworld,
                                  'multi_src': sum(1 for m in out if m['tr'] >= 2), 'official': sum(1 for m in out if m.get('rank') == 'official')}
        out.sort(key=lambda m: (m['lat'], m['lon']))
        for p in range(0, len(out), PART_ROWS):
            out_files.append((f'col_{cc}_{p // PART_ROWS}.json.gz', to_layer(cc, out[p:p + PART_ROWS])))
    for ds, rows in info.items():
        cur = store.get_json(f'processed/info/{ds}.json.gz', {}) or {}
        cur.update(rows)
        store.put_json(f'processed/info/{ds}.json.gz', cur)
    for ds in sorted(INFO_SETS):
        cur = store.get_json(f'processed/info/{ds}.json.gz', {}) or {}
        if cur:
            out_files.append((f'col_info_{ds}.json.gz', {'schema': 'collect-1', 'dataset': ds, 'built': today(), 'rows': cur,
                                                          'attribution': attribution(cur.values())}))
    # 손대지 않은 나라의 기존 층도 다음 판에 그대로(수가 줄지 않게)
    prev = store.get_json('processed/latest.json', {}) or {}
    have = {f for f, _ in out_files}
    run = now().strftime('%Y%m%d%H%M')
    for f in prev.get('files', []):
        if f not in have:
            b = store.get(f"processed/out/{prev['run']}/{f}")
            if b:
                store.put(f'processed/out/{run}/{f}', b); have.add(f)
    for f, obj in out_files:
        store.put_json(f'processed/out/{run}/{f}', obj)
    counts = dict(prev.get('counts', {}))
    for cc, s in stats['countries'].items():
        counts[cc] = s['places']
    store.put_json('processed/latest.json', {'run': run, 'files': sorted(have), 'counts': counts, 'stats': stats})
    for k in new:
        seen.setdefault(k.split('/')[2], []).append(k)
    pst['seen'] = {d: v for d, v in seen.items() if d >= cutoff}
    pst['last_run'] = run
    store.put_json('processed/state.json', pst)
    return run, stats


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
    errs = []
    for cc, n in prev_counts.items():
        if n and counts.get(cc, 0) < n * 0.95:
            errs.append(f'{cc}: 장소 수 {n} → {counts.get(cc, 0)} (-5% 넘게 감소)')
    sample = []
    for f, j in layers.items():
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
            sample.append((f, i))
        for name, v in (('좌표 범위 밖', bad), ('필수 칸 빠짐', miss), ('글자 깨짐', garb), ('금지 도메인', forb)):
            if v:
                errs.append(f'{f}: {name} {v}행')
    for f, i in random.Random(20).sample(sample, min(20, len(sample))):
        j = layers[f]
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
    layers = {f: store.get_json(f"processed/out/{latest['run']}/{f}", {}) for f in latest['files']}
    errs = gate(layers, hist[-1]['counts'] if hist else {}, latest['counts'])
    if errs:
        return None, errs
    tag = 'c' + latest['run']
    d = tempfile.mkdtemp()
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
    for f, j in layers.items():
        open(os.path.join(d, f), 'wb').write(gzip.compress(json.dumps(j, ensure_ascii=False, separators=(',', ':')).encode(), 6))
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
    a = ap.parse_args()
    store = Store(a.store)
    if a.cmd == 'process':
        run, st = process(store, a.world or None)
        _summary([f'## 가공 {run}', f"- 새 원본 파일 {st['read_files']}개 · 행 {st['read_rows']:,}", '| 나라 | 장소 | 타일과 일치 | 여러 출처 | 공식 |', '|---|---|---|---|---|']
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
