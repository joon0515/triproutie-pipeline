#!/usr/bin/env python3
"""장소 소개 층 OSM 판(desc-1, desc-osm-<지역>.json.gz) — 세계 타일 행에 OSM 의 description 태그를 언어 칸별로 붙인다. (2026-11-17, 218기)

    python3 desc_osm.py <out desc-osm-<지역>.json.gz> <tile.json.gz[,tile2.json.gz,…]> <a.osm.pbf> [b.osm.pbf …]

places-layers(scripts/run_layers_region.sh)가 지역마다 영업시간 층과 같은 추출본·같은 타일로 돌린다. 맞추기 규칙은 hours_osm.pairs 그대로(80m + 이름).
읽는 태그 → 칸(다른 언어로 채우지 않음):
  description:ko → ko · description:en → en · description:ja → ja
  description:zh-Hans|zh-CN|zh-SG → zh_Hans · description:zh-Hant|zh-TW|zh-HK → zh_Hant · description:zh → 글자로 간체·번체가 갈릴 때만(place_desc.han_script)
  언어 표시 없는 description → 한글이면 ko, 가나가 있으면 ja. 그 밖(라틴 문자 등 언어를 알 수 없는 글)은 싣지 않는다.
글은 원문 그대로(첫 문장만, 120자 넘으면 버림), place_desc.ok_text 로 거른다(이름과 같음·4자 미만·금지 낱말·주소).
ODbL: OSM 값만 든 따로 파일(license ODbL-1.0) — 수집 층·Wikidata 소개와 한 파일에 섞지 않는다(kit_1114 ODBL.md 의 (다) 분리 배포). 공개 의무는 다른 OSM 층과 같이 대표 결정을 따른다.
"""
import gzip, json, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, '..', 'scripts'))
import hours_osm as HO  # noqa: E402
import place_desc as PD  # noqa: E402

TAGS = {'description:ko': 'ko', 'description:en': 'en', 'description:ja': 'ja',
        'description:zh-Hans': 'zh_Hans', 'description:zh-CN': 'zh_Hans', 'description:zh-SG': 'zh_Hans',
        'description:zh-Hant': 'zh_Hant', 'description:zh-TW': 'zh_Hant', 'description:zh-HK': 'zh_Hant'}
KEYS = tuple(TAGS) + ('description:zh', 'description')
HANGUL = re.compile(r'[가-힣]')


def slots_of(tags, names=()):
    """OSM 태그 dict → {칸: 글 | None}."""
    out = {k: None for k in PD.SLOTS}
    ok = lambda v: PD.ok_text(PD.first_sentence(v), names) if v else None
    for k, slot in TAGS.items():
        v = ok(tags.get(k))
        if v and not out[slot] and not (slot == 'zh_Hans' and PD.han_script(v) == 'Hant' or slot == 'zh_Hant' and PD.han_script(v) == 'Hans'):
            out[slot] = v
    v = ok(tags.get('description:zh'))
    if v and not PD.KANA.search(v):
        sc = PD.han_script(v)
        if sc in ('Hans', 'neutral') and not out['zh_Hans']:
            out['zh_Hans'] = v
        elif sc == 'Hant' and not out['zh_Hant']:
            out['zh_Hant'] = v
    v = ok(tags.get('description'))
    if v:
        if HANGUL.search(v) and not PD.KANA.search(v) and not out['ko']:
            out['ko'] = v
        elif PD.KANA.search(v) and not HANGUL.search(v) and not out['ja']:
            out['ja'] = v
    return out


def osm_desc(files, bbox=None):
    """(lat, lon, [이름들], {칸: 글}, None, osm id, poi) — hours_osm.pairs 가 읽는 모양."""
    import osmium
    out = []
    for fn in files:
        t, n0 = time.time(), len(out)
        fp = osmium.FileProcessor(fn, osmium.osm.NODE | osmium.osm.WAY).with_locations().with_filter(osmium.filter.KeyFilter(*KEYS))
        for o in fp:
            tg = {k: v for k, v in o.tags}
            if not tg.get('name'):
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
            sl = slots_of(tg, names)
            if not any(sl.values()):
                continue
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
    if len(argv) < 4:
        print(__doc__)
        return 2
    d = HO.load_tiles([f for f in argv[2].split(',') if f])
    if not d['id']:
        print('no rows')
        return 1
    bb = [min(d['_lon']) - 0.01, min(d['_lat']) - 0.01, max(d['_lon']) + 0.01, max(d['_lat']) + 0.01]
    osm = osm_desc(argv[3:], bb)
    rows = rows_of(d, osm)
    region = os.path.basename(argv[1]).replace('desc-osm-', '').split('.')[0]
    doc = PD.layer_doc('osm', rows, {'region': region, 'bbox': [round(x, 4) for x in bb], 'report': {'tile_rows': len(d['id']), 'osm_rows': len(osm), 'matched': len(rows)}})
    if not rows:
        print({'tile_rows': len(d['id']), 'osm_rows': len(osm), 'matched': 0}, 'no file')
        return 0
    PD.write_layer(argv[1], doc)
    print(doc['report'], doc['langs'], os.path.getsize(argv[1]), 'bytes')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
