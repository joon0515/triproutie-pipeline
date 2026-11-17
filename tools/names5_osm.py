#!/usr/bin/env python3
"""이름 5개 언어 층 OSM 판(names-2, names-osm-<지역>[-NN].json.gz) — 세계 타일 행에 OSM name:xx 태그를 언어 칸별로 붙인다. (2026-11-18, 223기)

    python3 names5_osm.py <out 폴더> <지역> <tile.json.gz[,tile2.json.gz,…]> <a.osm.pbf> [b.osm.pbf …] [--cols <수집 층 주소 목록 파일>]
    행 id = 세계 타일 id(앱) + --cols 를 주면 그 지역 나라 수집 층(col_*, 게시 장소) id 도.

places-layers(scripts/run_layers_region.sh)가 지역마다 영업시간·소개 층과 같은 추출본·같은 타일로 돌린다. 맞추기는 hours_osm.pairs 그대로(80m + 이름).
읽는 태그 → 칸(원천 값만, 다른 언어로 채우지 않음 · 간체↔번체 변환 없음):
  name:ko → ko · name:en → en · name:ja → ja
  name:zh-Hans|zh-CN|zh-SG → zh_Hans · name:zh-Hant|zh-TW|zh-HK → zh_Hant(꼬리표와 글자가 어긋나면 버림)
  name:zh → 글자로 간체·번체가 갈릴 때만 그 칸(place_names.zh_generic — 못 가리면 비움). 언어 표시 없는 name 은 쓰지 않는다(앱이 원어 이름으로 보여 줌).
ODbL: OSM 값만 든 따로 파일(license ODbL-1.0) — 수집 층·Wikidata 이름과 섞지 않는다(kit_1114 ODBL.md 의 분리 배포). 파일 하나 7MB 를 넘으면 id 해시로 나눈다.
"""
import os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, '..', 'scripts'))
import hours_osm as HO  # noqa: E402
import place_names as PN  # noqa: E402

TAG_MAP = {'ko': ('name:ko',), 'en': ('name:en',), 'ja': ('name:ja',),
           'zh_Hans': ('name:zh-Hans', 'name:zh-CN', 'name:zh-SG'), 'zh_Hant': ('name:zh-Hant', 'name:zh-TW', 'name:zh-HK')}
KEYS = tuple(t for ts in TAG_MAP.values() for t in ts) + ('name:zh',)


def slots_of(tags):
    return PN.slots_from_tags(tags, TAG_MAP, tags.get('name:zh'))


def osm_names(files, bbox=None):
    """(lat, lon, [이름들], {칸: 이름}, None, osm id, poi) — hours_osm.pairs 가 읽는 모양."""
    import osmium
    out = []
    for fn in files:
        t, n0 = time.time(), len(out)
        fp = osmium.FileProcessor(fn, osmium.osm.NODE | osmium.osm.WAY).with_locations().with_filter(osmium.filter.KeyFilter(*KEYS))
        for o in fp:
            tg = {k: v for k, v in o.tags}
            if not tg.get('name'):
                continue
            sl = slots_of(tg)
            if not any(sl.values()):
                continue
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
            names = [v for k, v in tg.items() if k == 'name' or k.startswith('name:') or k in ('alt_name', 'official_name', 'brand')]
            poi = bool(tg.get('amenity') or tg.get('shop') or tg.get('tourism') or tg.get('leisure') or tg.get('historic'))
            out.append((lat, lon, names, sl, None, ('node/' if o.is_node() else 'way/') + str(o.id), poi))
        print(os.path.basename(fn), len(out) - n0, round(time.time() - t, 1), 's', flush=True)
    return out


def rows_of(d, osm):
    rows = {}
    for k, (_, i) in sorted(HO.pairs(d, osm).items()):
        r = osm[k]
        rows[d['id'][i]] = dict(r[3], osm=r[5])
    return rows


def main(argv):
    cols = None
    if '--cols' in argv:
        i = argv.index('--cols'); cols = argv[i + 1]; del argv[i:i + 2]
    if len(argv) < 5:
        print(__doc__)
        return 2
    out, region = argv[1], argv[2]
    os.makedirs(out, exist_ok=True)
    d = HO.load_tiles([f for f in argv[3].split(',') if f])
    if not d['id']:
        print('no rows')
        return 1
    bb = [min(d['_lon']) - 0.01, min(d['_lat']) - 0.01, max(d['_lon']) + 0.01, max(d['_lat']) + 0.01]
    osm = osm_names(argv[4:], bb)
    rows = rows_of(d, osm)
    ncol = 0
    if cols:  # 수집 층(게시 장소) id 로도 — 같은 OSM 행, 같은 규칙(80m + 이름)
        for k, rid in HO.col_pairs(HO.col_urls(cols), osm, bb, out).items():
            if rid not in rows:
                rows[rid] = dict(osm[k][3], osm=osm[k][5]); ncol += 1
    rep = {'tile_rows': len(d['id']), 'osm_rows': len(osm), 'matched': len(rows), 'col_matched': ncol}
    if not rows:
        print(rep, 'no file')
        return 0
    os.makedirs(out, exist_ok=True)
    made = PN.write_sharded(out, f'names-osm-{region}', rows,
                            lambda p, part: PN.names_doc('osm', p, {'region': region, 'part': part, 'bbox': [round(x, 4) for x in bb], 'report': rep}), n=1)
    print(rep, {k: sum(1 for v in rows.values() if v.get(k)) for k in PN.SLOTS}, sorted(made))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
