#!/usr/bin/env python3
"""편의시설 층 — OpenStreetMap(ODbL 1.0) 로컬 PBF 에서 화장실·코인락커·식수대를 뽑아 '층' 파일로 낸다.  (2026-11-13)

기존 tools/osm_lockers.py(코인락커만)를 같은 방식으로 넓힌 것이다. 장소 팩(CDLA/Apache/CC0)과 **섞지 않고** 별도 파일로 둔다
— ODbL 은 섞어서 배포하면 합친 자료 전체에 같은 조건이 걸리기 때문이다. 앱은 tlPfInstallAmenities 로 따로 올린다.

    python3 osm_amenities.py <out.json.gz> <a.osm.pbf> [b.osm.pbf …]      # PBF 는 읽기만 한다. 네트워크를 쓰지 않는다.
    python3 osm_amenities.py --split-deg 4 <out 앞부분> <a.osm.pbf> …      # 4도 칸마다 <앞부분>-n36e136.json.gz (큰 나라용)

뽑는 것(노드 + 건물로 그려진 것(way) 의 중심):
  toilet  amenity=toilets (access=private·customers·no 제외)
  locker  amenity=luggage_locker, amenity=locker(택배함 제외 — locker=parcel*, Amazon·DHL·宅配 등)
  water   amenity=drinking_water, amenity=water_point, drinking_water=yes 인 분수
  (11/13 2차 — 팩에 없거나 빈약한 분류 보충)
  parking  amenity=parking 중 누구나 쓸 수 있고(access 없음·yes·public) 이름이 있거나 유료이거나 건물식·지하식인 것(이름 없는 노상 주차 칸은 뺀다)
  exchange amenity=bureau_de_change
  atm      amenity=atm
  info     tourism=information 중 information=office·visitor_centre(안내판·지도판은 뺀다)
  pharmacy amenity=pharmacy
행: {k, osm, lat, lon, name, name_en, name_ko, wc(휠체어: yes|limited|no|''), fee(yes|no|''), hours, level, chg(기저귀 교환대 yes|''), src}
출력: {"schema":"amenities-1","license":"ODbL-1.0","attribution":"© OpenStreetMap contributors (ODbL 1.0)","source":[파일…],"built":…,"n":{종류:개수},"rows":[…]}
"""
import gzip, json, os, re, sys, time

PARCEL = re.compile(r'amazon|dhl|packstation|parcel|宅配|はこぽす|PUDO|ヤマト|佐川|ゆうパック|택배|順豐|顺丰|智能櫃', re.I)
NO_ACCESS = {'private', 'customers', 'no', 'permit', 'employees'}
WC = {'yes': 'yes', 'designated': 'yes', 'limited': 'limited', 'no': 'no'}


def classify(tg):
    """태그 → 종류('toilet'|'locker'|'water') 또는 None. 순수 함수(시험용)."""
    am = tg.get('amenity')
    if tg.get('access') in NO_ACCESS:
        return None
    if am == 'toilets':
        return 'toilet'
    if am == 'luggage_locker':
        return 'locker'
    if am == 'locker':
        nm = ' '.join(tg.get(k, '') for k in ('name', 'brand', 'operator'))
        if (tg.get('locker') or '').startswith('parcel') or PARCEL.search(nm):
            return None
        return 'locker'
    if am in ('drinking_water', 'water_point') or (am == 'fountain' and tg.get('drinking_water') == 'yes'):
        return 'water'
    if am == 'parking':
        if tg.get('access') not in (None, 'yes', 'public'):
            return None
        if tg.get('name') or tg.get('fee') == 'yes' or tg.get('parking') in ('multi-storey', 'underground'):
            return 'parking'
        return None
    if am == 'bureau_de_change':
        return 'exchange'
    if am == 'atm':
        return 'atm'
    if am == 'pharmacy':
        return 'pharmacy'
    if tg.get('tourism') == 'information' and tg.get('information') in ('office', 'visitor_centre'):
        return 'info'
    return None


def row(kind, osm_id, lat, lon, tg, src):
    nm = tg.get('name') or tg.get('name:ja') or tg.get('name:ko') or tg.get('name:zh') or (tg.get('operator') or tg.get('brand') or '' if kind in ('atm', 'exchange', 'pharmacy') else '')
    fee = tg.get('fee', '')
    return {'k': kind, 'osm': osm_id, 'lat': round(lat, 6), 'lon': round(lon, 6), 'name': nm, 'name_en': tg.get('name:en'), 'name_ko': tg.get('name:ko'),
            'wc': WC.get(tg.get('wheelchair') or tg.get('toilets:wheelchair') or '', ''), 'fee': fee if fee in ('yes', 'no') else '',
            'hours': (tg.get('opening_hours') or '')[:60], 'level': tg.get('level'), 'chg': 'yes' if tg.get('changing_table') == 'yes' else '', 'src': src}


def extract(files):
    import osmium
    flt = osmium.filter.TagFilter(('amenity', 'toilets'), ('amenity', 'luggage_locker'), ('amenity', 'locker'), ('amenity', 'drinking_water'),
                                  ('amenity', 'water_point'), ('amenity', 'fountain'), ('amenity', 'parking'), ('amenity', 'bureau_de_change'),
                                  ('amenity', 'atm'), ('amenity', 'pharmacy'), ('tourism', 'information'))
    out, seen = [], set()
    for fn in files:
        if not os.path.exists(fn):
            print('없음', fn, flush=True)
            continue
        t, n0, src = time.time(), len(out), os.path.basename(fn)
        fp = osmium.FileProcessor(fn, osmium.osm.NODE | osmium.osm.WAY).with_locations().with_filter(flt)
        for o in fp:
            tg = dict(o.tags)
            kind = classify(tg)
            if not kind:
                continue
            if o.is_node():
                if not o.location.valid():
                    continue
                oid, lat, lon = f'n{o.id}', o.location.lat, o.location.lon
            else:
                pts = [(n.lat, n.lon) for n in o.nodes if n.location.valid()]
                if not pts:
                    continue
                oid, lat, lon = f'w{o.id}', sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
            if oid in seen:
                continue
            seen.add(oid)
            out.append(row(kind, oid, lat, lon, tg, src))
        print(src, len(out) - n0, round(time.time() - t, 1), 's', flush=True)
    return out


def write(path, rows, sources):
    n = {}
    for r in rows:
        n[r['k']] = n.get(r['k'], 0) + 1
    doc = {'schema': 'amenities-1', 'license': 'ODbL-1.0', 'attribution': '© OpenStreetMap contributors (ODbL 1.0)',
           'source': sources, 'built': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'n': n, 'rows': rows}
    with gzip.open(path, 'wt', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, separators=(',', ':'))
    return n


def split_cells(rows, deg):
    """행을 위도·경도 deg 도 칸으로 나눈다 → {'n36e136': [행…]}. 큰 나라의 파일 하나가 앱 상한(8MB)을 넘지 않게 하는 용도."""
    import math
    out = {}
    for r in rows:
        la, lo = math.floor(r['lat'] / deg) * deg, math.floor(r['lon'] / deg) * deg
        key = f"{'n' if la >= 0 else 's'}{abs(int(la)):02d}{'e' if lo >= 0 else 'w'}{abs(int(lo)):03d}"
        out.setdefault(key, []).append(r)
    return out


def main(argv):
    deg = 0
    if '--split-deg' in argv:
        i = argv.index('--split-deg')
        deg = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if len(argv) < 3:
        print(__doc__)
        return 2
    rows = extract(argv[2:])
    if deg > 0:  # argv[1] = 파일 이름 앞부분: <앞부분>-n36e136.json.gz …
        cells = split_cells(rows, deg)
        for key, rs in sorted(cells.items()):
            fn = f'{argv[1]}-{key}.json.gz'
            write(fn, rs, [os.path.basename(f) for f in argv[2:]])
            print(os.path.basename(fn), len(rs), os.path.getsize(fn), 'bytes')
        print('total', len(rows), 'cells', len(cells))
        return 0
    n = {}
    for r in rows:
        n[r['k']] = n.get(r['k'], 0) + 1
    doc = {'schema': 'amenities-1', 'license': 'ODbL-1.0', 'attribution': '© OpenStreetMap contributors (ODbL 1.0)',
           'source': [os.path.basename(f) for f in argv[2:]], 'built': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'n': n, 'rows': rows}
    with gzip.open(argv[1], 'wt', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, separators=(',', ':'))
    print('total', len(rows), n, os.path.getsize(argv[1]), 'bytes')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
