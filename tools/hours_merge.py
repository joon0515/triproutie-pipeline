#!/usr/bin/env python3
"""영업시간 층 합치기 — OSM 판(hours_osm.py) + 세계 수집 가공본(processed col_*: 공식 사이트 JSON-LD openingHours·Wikivoyage 등)을 같은 oh 칸으로. (2026-11-15)

    python3 hours_merge.py <OSM 판 폴더(hours-*.json.gz)> <가공본 폴더(col_*.json.gz)> <출력 폴더>

규칙: 가공본 행 중 세계 타일 id(wid)에 맞춰졌고 h 가 표준형(앞에 'raw:' 없음)인 것만 쓴다.
  우선순위 = 공식 사이트(rk 'o') > OSM > 그 밖의 수집 출처(Wikivoyage·대만 관광서·열린 데이터셋). 같은 순위면 먼저 있던 값을 둔다.
  공식 값이 OSM 값과 다르면 hd=1(앱: '시간이 출처마다 다름' 표시 — tl_trust 와 같은 뜻).
출력: OSM 판 파일과 같은 이름(hours-<지역>.json.gz) — 그 지역 bbox 안의 wid 는 그 파일에 넣는다.
  어느 OSM 판에도 없는 지역(OSM 층을 아직 안 만든 나라)의 행은 hours-col-<나라>.json.gz 로 따로 낸다.
행: {"oh": "...", "src": "official|osm|wikivoyage|…", "url"?: 공식 주소, "osm"?, "q"?, "hd"?: 1}
"""
import glob, gzip, json, os, sys, time

RANK = {'official': 0, 'osm': 1}


def rank(src):
    return RANK.get(src, 2)


def col_rows(fn):
    """가공본(collect-1 열 모양) → (wid, oh, src, url, lat, lon, cc)."""
    j = json.loads(gzip.decompress(open(fn, 'rb').read()))
    la = lo = 0
    out = []
    for i in range(len(j.get('id') or [])):
        la += j['dla'][i]; lo += j['dlo'][i]
        wid, h = (j.get('wid') or [None] * (i + 1))[i], (j.get('h') or [None] * (i + 1))[i]
        if not wid or not isinstance(h, str) or not h or h.startswith('raw:'):
            continue
        src = 'official' if (j.get('rk') or [None] * (i + 1))[i] == 'o' else str((j.get('src') or [''] * (i + 1))[i] or 'collect').split('|')[0]
        url = (j.get('url') or [None] * (i + 1))[i] if src == 'official' else None
        out.append((wid, h, src, url, la / 1e5, lo / 1e5, j.get('cc') or 'xx'))
    return out


def inside(bb, lat, lon):
    return bb[0] <= lon <= bb[2] and bb[1] <= lat <= bb[3]


def put(rows, wid, oh, src, url):
    """rows[wid] 에 넣기. 바뀌면 True."""
    cur = rows.get(wid)
    if cur and cur.get('oh') and rank(cur.get('src')) <= rank(src):
        if src == 'official' and cur.get('src') == 'official' and cur['oh'] != oh:
            cur['hd'] = 1
        return False
    v = dict(cur or {})
    if v.get('oh') and v['oh'] != oh and src == 'official':
        v['hd'] = 1
    v.update({'oh': oh, 'src': src})
    if url:
        v['url'] = url[:200]
    rows[wid] = v
    return True


def merge(osm_dir, col_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    layers = {}
    for fn in sorted(glob.glob(os.path.join(osm_dir, 'hours-*.json.gz'))):
        layers[os.path.basename(fn)] = json.loads(gzip.decompress(open(fn, 'rb').read()))
    where = {}  # wid → 파일
    for f, d in layers.items():
        for wid in d.get('rows') or {}:
            where[wid] = f
    extra, rep = {}, {'col_rows': 0, 'into_osm_files': 0, 'into_col_files': 0, 'official': 0}
    for fn in sorted(glob.glob(os.path.join(col_dir, 'col_*.json.gz'))):
        for wid, oh, src, url, lat, lon, cc in col_rows(fn):
            rep['col_rows'] += 1
            f = where.get(wid) or next((k for k, d in layers.items() if isinstance(d.get('bbox'), list) and inside(d['bbox'], lat, lon)), None)
            if f:
                ch = put(layers[f]['rows'], wid, oh, src, url)
                where[wid] = f
                rep['into_osm_files'] += ch
            else:
                e = extra.setdefault(cc, {'rows': {}, 'pts': []})
                ch = put(e['rows'], wid, oh, src, url)
                e['pts'].append((lat, lon))
                rep['into_col_files'] += ch
            rep['official'] += (src == 'official' and ch)
    built = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    for f, d in layers.items():
        d['built_merge'] = built
        d['attribution'] = '© OpenStreetMap contributors (ODbL 1.0) · 공식 사이트 구조화 데이터(사실값, 주소 링크) · Wikivoyage(CC BY-SA 4.0)'
        d.setdefault('report', {})['with_oh_merged'] = sum(1 for v in d['rows'].values() if v.get('oh'))
        _write(os.path.join(out_dir, f), d)
    for cc, e in extra.items():
        if not e['rows']:
            continue
        ys, xs = [p[0] for p in e['pts']], [p[1] for p in e['pts']]
        _write(os.path.join(out_dir, f'hours-col-{cc.lower()}.json.gz'),
               {'schema': 'hours-1', 'license': 'mixed (sources/registry.yaml)', 'attribution': '공식 사이트 구조화 데이터(사실값, 주소 링크) · Wikivoyage(CC BY-SA 4.0) · 각 수집 출처',
                'built': built, 'bbox': [round(min(xs), 4), round(min(ys), 4), round(max(xs), 4), round(max(ys), 4)], 'report': {'rows': len(e['rows'])}, 'rows': e['rows']})
    return rep


def _write(path, doc):
    with gzip.open(path, 'wt', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, separators=(',', ':'))


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(2)
    print(merge(*sys.argv[1:4]))
