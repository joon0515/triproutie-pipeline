#!/usr/bin/env python3
"""영업시간 층 원본(hours-1, OSM 판) — 세계 타일·팩 행에 OSM 의 opening_hours 와 wikidata 태그를 붙인다. (2026-11-15)

    python3 hours_osm.py <out.json.gz> <tile.json.gz[,tile2.json.gz,…]> <a.osm.pbf> [b.osm.pbf …] [--cols <수집 층 주소 목록 파일>]
    (11/18) --cols: 그 지역 나라의 수집 층(col_*) 행에도 맞춰 수집 층 id 로 싣는다(게시 장소 채움).

맞추기(names_osm 과 같은 규칙): 좌표 MAX_M(80m) 이내 + 이름이 같음(공백·기호를 뗀 뒤 일치, 또는 한쪽이 다른 쪽으로 시작하고 3자 이상).
  팩 행의 이름 후보 = nm·ko·en·ja·zh. OSM 쪽 = name·name:en·name:ko·name:ja·name:th·name:fr 등 name* 전부.
OSM 에서 읽는 것: opening_hours 나 wikidata 가 있는 이름 있는 노드·길(가게·명소). 수가 적어 격자에 넣고, 팩 행(많음)을 하나씩 찾는다.
출력(ODbL 이라 팩과 섞지 않고 층으로): {"schema":"hours-1","license":"ODbL-1.0","bbox":[서,남,동,북],"report":{…},
  "rows":{팩 id:{"oh":"Mo-Fr 09:00-18:00","src":"osm","osm":"n123","q":"Q42"?}}}
  q(OSM wikidata 태그)는 사진 층(photo_layer.py)이 Wikidata P18 을 찾는 씨앗으로 쓴다. oh 가 없고 q 만 있는 행도 싣는다.
공식 사이트(JSON-LD openingHours) 값은 hours_merge.py 가 같은 oh 칸에 덮어쓴다(공식 > OSM).
"""
import gzip, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from names_osm import cell, dist_m, load_packs, norm, same_name, MAX_M  # noqa: E402
from oh_check import oh_ok  # noqa: E402

NAME_COLS = ('nm', 'ko', 'en', 'ja', 'zh')


def load_tiles(files):
    """load_packs 는 nm·ko 만 잇는다 → 영업시간 맞추기에는 en·ja·zh 도 필요해 여기서 붙인다."""
    if len(files) == 1:
        d = load_packs(files)
        for k in NAME_COLS:
            if not isinstance(d.get(k), list):
                d[k] = [None] * len(d['id'])
        return d
    out = {k: [] for k in ('id', '_lat', '_lon') + NAME_COLS}
    for f in files:
        d = load_packs([f])
        n = len(d['id'])
        for k in ('id', '_lat', '_lon'):
            out[k].extend(d[k])
        for k in NAME_COLS:
            v = d.get(k)
            out[k].extend(v if isinstance(v, list) and len(v) == n else [None] * n)
    return out


def osm_hours(files, bbox=None):
    """(lat, lon, [이름들], oh, q, osm id, poi) 목록."""
    import osmium
    out = []
    for fn in files:
        t, n0 = time.time(), len(out)
        fp = osmium.FileProcessor(fn, osmium.osm.NODE | osmium.osm.WAY).with_locations().with_filter(osmium.filter.KeyFilter('opening_hours', 'wikidata'))
        for o in fp:
            tg = o.tags
            if not tg.get('name'):
                continue
            oh, q = oh_ok(tg.get('opening_hours')), tg.get('wikidata')  # (11/18) 못 알아듣는 값은 버린다(예전: 120자에서 잘라 뜻이 바뀌기도 함)
            if o.is_node():
                if not o.location.valid():
                    continue
                lat, lon = o.location.lat, o.location.lon
            else:
                pts = [(x.lat, x.lon) for x in o.nodes if x.location.valid()]
                if not pts:
                    continue
                lat, lon = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
            if bbox and (lat < bbox[1] or lat > bbox[3] or lon < bbox[0] or lon > bbox[2]):
                continue
            names = [v for k, v in tg if k == 'name' or k.startswith('name:') or k in ('alt_name', 'official_name', 'brand')]
            poi = bool(tg.get('amenity') or tg.get('shop') or tg.get('tourism') or tg.get('leisure') or tg.get('historic'))
            out.append((lat, lon, names, oh or None, q if q and q.startswith('Q') else None, ('n' if o.is_node() else 'w') + str(o.id), poi))
        print(os.path.basename(fn), len(out) - n0, round(time.time() - t, 1), 's', flush=True)
    return out


def osm_grid(osm):
    grid = {}
    for k, r in enumerate(osm):
        grid.setdefault(cell(r[0], r[1]), []).append(k)
    return grid


def pairs(d, osm, grid=None):
    """팩 행 → OSM 행 짝 {osm 번호: (거리, 팩 행 번호)}. 같은 OSM 행은 가장 가까운 팩 행 하나에만. (desc_osm.py 도 씀)"""
    grid = grid if grid is not None else osm_grid(osm)
    best = {}  # osm 번호 → (거리, 팩 행 번호)
    for i in range(len(d['id'])):
        la, lo = d['_lat'][i], d['_lon'][i]
        cy, cx = cell(la, lo)
        cand = [k for dy in (-1, 0, 1) for dx in (-1, 0, 1) for k in grid.get((cy + dy, cx + dx), ())]
        if not cand:
            continue
        mine = [d[c][i] for c in NAME_COLS if isinstance(d[c][i], str) and d[c][i]]
        if not mine:
            continue
        top = None
        for k in cand:
            r = osm[k]
            m = dist_m(la, lo, r[0], r[1])
            if m > MAX_M:
                continue
            ok = any((same_name(a, b) if r[6] else norm(a) == norm(b)) for a in mine for b in r[2])
            if ok and (top is None or m < top[0]):
                top = (m, k)
        if top and (top[1] not in best or top[0] < best[top[1]][0]):
            best[top[1]] = (top[0], i)
    return best


def col_urls(path):
    """--cols 파일(한 줄에 gs:// 주소 또는 로컬 경로 하나)."""
    return [x.strip() for x in open(path, encoding='utf-8') if x.strip()] if path else []


def stream_cols(urls, workdir, batch=40):
    """수집 층(col_*, collect-1) 파일을 batch 개씩 받아 하나씩 (파일 이름, 열 dict) 로 — 받은 파일은 곧 지운다(러너 디스크)."""
    import subprocess, tempfile
    for i in range(0, len(urls), batch):
        part = urls[i:i + batch]
        d = tempfile.mkdtemp(dir=workdir)
        gsu = [u for u in part if u.startswith('gs://')]
        if gsu:
            subprocess.run(['gcloud', 'storage', 'cp', '-q', '-I', d + '/'], input='\n'.join(gsu).encode(), capture_output=True)
        for u in part:
            p = os.path.join(d, u.rsplit('/', 1)[1]) if u.startswith('gs://') else u
            if not os.path.exists(p):
                continue
            try:
                yield os.path.basename(p), load_tiles([p])
            except (ValueError, OSError, KeyError):
                pass
            if u.startswith('gs://'):
                os.remove(p)
        os.rmdir(d) if not os.listdir(d) else None


def col_pairs(urls, osm, bbox, workdir=None):
    """(11/18, 223기) 수집 층 행(게시 장소 — Foursquare 등, 세계 타일에 없는 곳 포함)에도 같은 규칙(80m + 이름)으로 OSM 행을 맞춘다.
    bbox 밖 행은 건너뛴다. 같은 OSM 행은 모든 파일을 통틀어 가장 가까운 수집 행 하나에만. 돌려줌 = {osm 번호: 수집 층 장소 id}."""
    grid, best = osm_grid(osm), {}
    w, s, e, n = bbox
    for _, d in stream_cols(urls, workdir):
        keep = [i for i in range(len(d['id'])) if s <= d['_lat'][i] <= n and w <= d['_lon'][i] <= e]
        if not keep:
            continue
        sub = {k: [d[k][i] for i in keep] for k in ('id', '_lat', '_lon') + NAME_COLS}
        for k, (m, i) in pairs(sub, osm, grid).items():
            if k not in best or m < best[k][0]:
                best[k] = (m, sub['id'][i])
    return {k: rid for k, (_, rid) in best.items()}


def match(d, osm):
    """팩 행 → OSM 행. 같은 OSM 행은 가장 가까운 팩 행 하나에만."""
    best = pairs(d, osm)
    rows = {}
    for k, (_, i) in best.items():
        r = osm[k]
        v = {'src': 'osm', 'osm': r[5]}
        if r[3]:
            v['oh'] = r[3]
        if r[4]:
            v['q'] = r[4]
        rows[d['id'][i]] = v
    return rows


def main(argv):
    cols = None
    if '--cols' in argv:
        i = argv.index('--cols'); cols = argv[i + 1]; del argv[i:i + 2]
    if len(argv) < 4:
        print(__doc__)
        return 2
    tiles = [f for f in argv[2].split(',') if f]
    d = load_tiles(tiles)
    if not d['id']:
        print('no rows')
        return 1
    bb = [min(d['_lon']) - 0.01, min(d['_lat']) - 0.01, max(d['_lon']) + 0.01, max(d['_lat']) + 0.01]
    osm = osm_hours(argv[3:], bb)
    rows = match(d, osm)
    ncol = 0
    if cols:  # (11/18) 수집 층 id 로도 싣는다 — 게시 장소(col_*)의 영업시간 채움. 타일 id 행과 같은 값
        for k, rid in col_pairs(col_urls(cols), osm, bb, os.path.dirname(os.path.abspath(argv[1]))).items():
            r = osm[k]
            if rid not in rows and (r[3] or r[4]):
                v = {'src': 'osm', 'osm': r[5]}
                if r[3]:
                    v['oh'] = r[3]
                if r[4]:
                    v['q'] = r[4]
                rows[rid] = v; ncol += 1
    rep = {'tile_rows': len(d['id']), 'osm_rows': len(osm), 'matched': len(rows), 'col_matched': ncol, 'with_oh': sum(1 for v in rows.values() if 'oh' in v),
           'with_q': sum(1 for v in rows.values() if 'q' in v)}
    doc = {'schema': 'hours-1', 'license': 'ODbL-1.0', 'attribution': '© OpenStreetMap contributors (ODbL 1.0)', 'built': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
           'bbox': [round(x, 4) for x in bb], 'report': rep, 'rows': rows}
    with gzip.open(argv[1], 'wt', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, separators=(',', ':'))
    print(rep, os.path.getsize(argv[1]), 'bytes')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
