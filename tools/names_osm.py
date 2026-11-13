#!/usr/bin/env python3
"""이름 보충 층(names-1) — 한글 이름이 없는 팩 행에 OSM 의 name:ko·읽는 법(name:ja-Hira/ja_kana/ja-Latn/ja_rm)·name:en 을 붙인다. (2026-11-13, 오사카 시범)

    python3 names_osm.py <out.json.gz> <pack.json.gz[,pack2.json.gz,…]> <a.osm.pbf> [b.osm.pbf …]     (세계 타일은 쉼표로 여러 개)

맞추기: 좌표가 MAX_M(80m) 이내 + 이름이 같음(공백·기호·끝의 店 을 뗀 뒤 일치, 또는 한쪽이 다른 쪽으로 시작하고 3자 이상).
출처가 ODbL 이므로 장소 팩과 섞지 않고 별도 층으로 낸다(편의시설 층과 같은 방식). PBF 는 읽기만 하고 네트워크는 쓰지 않는다.
Wikidata 한국어 라벨은 OSM 의 wikidata 태그가 있는 행만 대상(qid 를 층에 싣고, --wikidata 를 주면 wbgetentities 50개 묶음으로 채운다).
상호 사전: OSM 에서 같은 이름의 가게가 3곳 이상이고 영문 이름이 60% 이상 일치하면 체인으로 보고, 좌표로 못 맞춘 같은 상호 행에도 붙인다(via='brand').
출력: {"schema":"names-1","license":"ODbL-1.0","attribution":…,"pack":…,"report":{…},"rows":{팩 id:{ko?,ks?(검색 열쇠 전용 한글),rd?,en?,q?,osm?,via?}}}
"""
import gzip, json, math, os, re, sys, time, unicodedata

MAX_M = 80
KANA = re.compile(r'[ぁ-ゖァ-ヺ]')
KANJI = re.compile(r'[㐀-鿿]')
HANGUL = re.compile(r'[가-힣]')
STRIP = re.compile(r'[\s　・·\-‐－—.,()（）「」『』&＆\'’]+')


def norm(s):
    s = unicodedata.normalize('NFKC', s or '').lower()
    s = STRIP.sub('', s)
    return s[:-1] if len(s) > 2 and s.endswith('店') else s


def same_name(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return False
    if a == b:
        return True
    lo, hi = (a, b) if len(a) <= len(b) else (b, a)
    return len(lo) >= 3 and hi.startswith(lo)


def reading(tg):
    """읽는 법: 가나가 있으면 가나, 없으면 로마자. 없으면 ''."""
    for k in ('name:ja-Hira', 'name:ja_kana', 'name:ja-Kana', 'name:ja-Hrkt'):
        v = tg.get(k)
        if v and KANA.search(v):
            return v
    for k in ('name:ja-Latn', 'name:ja_rm'):
        v = tg.get(k)
        if v:
            return v
    return ''


def load_pack(fn):
    d = json.load(gzip.open(fn))
    la = lo = 0
    lat, lon = [], []
    for a, b in zip(d['dla'], d['dlo']):
        la += a
        lo += b
        lat.append(la / 1e5)
        lon.append(lo / 1e5)
    d['_lat'], d['_lon'] = lat, lon
    return d


def load_packs(files):
    """팩·타일 여러 개를 한 덩어리로(쉼표로 이어 준 경우). 필요한 열(id·nm·ko·c·cats·좌표)만 잇는다. 분류 번호는 파일마다 달라 이름으로 맞춘다."""
    if len(files) == 1:
        return load_pack(files[0])
    out = {'id': [], 'nm': [], 'ko': [], 'c': [], 'cats': [], '_lat': [], '_lon': []}
    cat_ix = {}
    for f in files:
        d = load_pack(f)
        cats = d.get('cats') or []
        for i in range(len(d['nm'])):
            out['id'].append(d['id'][i])
            out['nm'].append(d['nm'][i])
            out['ko'].append(d['ko'][i])
            name = cats[d['c'][i]] if cats else ''
            out['c'].append(cat_ix.setdefault(name, len(cat_ix)))
            out['_lat'].append(d['_lat'][i])
            out['_lon'].append(d['_lon'][i])
    out['cats'] = sorted(cat_ix, key=cat_ix.get)
    return out


def cell(lat, lon):
    return (int(lat * 500), int(lon * 500))  # 약 220m × 180m 칸


def dist_m(a, b, c, d):
    x = (d - b) * math.cos(math.radians((a + c) / 2)) * 111320
    y = (c - a) * 110540
    return math.hypot(x, y)


def targets(d, only=None):
    """보충 대상: 한글 이름이 없고 이름에 한자가 있는 행(가나가 섞였어도 한자 읽기는 모르므로 포함). only = 분류 이름 집합."""
    cats = d.get('cats') or []
    out = []
    for i, nm in enumerate(d['nm']):
        if not isinstance(nm, str) or d['ko'][i] is not None or not KANJI.search(nm):
            continue
        if only and cats[d['c'][i]] not in only:
            continue
        out.append(i)
    return out


def osm_named(files, bbox):
    import osmium
    s, w, n, e = bbox
    out = []
    for fn in files:
        t, n0 = time.time(), len(out)
        fp = osmium.FileProcessor(fn, osmium.osm.NODE | osmium.osm.WAY).with_locations().with_filter(osmium.filter.KeyFilter('name'))
        for o in fp:
            tg = o.tags
            ko, en, q = tg.get('name:ko'), tg.get('name:en'), tg.get('wikidata')
            rd = reading(tg)
            if not (ko or en or rd or q):
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
            if lat < s or lat > n or lon < w or lon > e:
                continue
            poi = bool((tg.get('amenity') or tg.get('shop')) and not (tg.get('railway') or tg.get('place') or tg.get('highway') or tg.get('public_transport')))
            out.append((lat, lon, tg.get('name'), ko if ko and HANGUL.search(ko) else None, rd, en, q, ('n' if o.is_node() else 'w') + str(o.id), poi))
        print(os.path.basename(fn), len(out) - n0, round(time.time() - t, 1), 's', flush=True)
    return out


def match(d, tgt, osm):
    grid = {}
    for r in osm:
        grid.setdefault(cell(r[0], r[1]), []).append(r)
    rows = {}
    for i in tgt:  # noqa
        la, lo, nm = d['_lat'][i], d['_lon'][i], d['nm'][i]
        cy, cx = cell(la, lo)
        best = None
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for r in grid.get((cy + dy, cx + dx), ()):
                    m = dist_m(la, lo, r[0], r[1])
                    if m <= MAX_M and (same_name(nm, r[2]) if r[8] else norm(nm) == norm(r[2])) and (best is None or m < best[0]):  # 역·지명은 이름이 똑같을 때만
                        best = (m, r)
        if best:
            r = best[1]
            v = {'osm': r[7]}
            if r[3]:
                v['ko' if norm(nm) == norm(r[2]) else 'ks'] = r[3]  # 이름이 앞부분만 같으면(지점명 차이) 검색 열쇠로만
            if r[4]:
                v['rd'] = r[4]
            if r[5]:
                v['en'] = r[5]
            if r[6]:
                v['q'] = r[6]
            if len(v) > 1:
                rows[d['id'][i]] = v
    return rows


def brands(osm, min_n=3):
    """상호 사전: OSM 에서 같은 이름(정규화)이 min_n 곳 이상이고 읽는 법·영문·한글 이름이 한 가지로 모이는 것(체인)."""
    by = {}
    for r in osm:
        k = norm(r[2])
        if len(k) >= 2 and KANJI.search(k) and r[8]:  # 가게·시설만(역·지명·길·강 이름은 상호가 아니다)
            by.setdefault(k, []).append(r)
    out = {}
    for k, rs in by.items():
        if len(rs) < min_n:
            continue
        # 영문 이름이 60% 이상 한 가지로 모일 때만 상호로 본다. 한글·읽는 법은 그 영문 이름을 가진 행에서만 고른다(다른 가게 값이 섞이지 않게).
        ens = [r[5] for r in rs if r[5]]
        if not ens:
            continue
        en = max(set(ens), key=ens.count)
        if ens.count(en) * 10 < len(rs) * 6:
            continue
        v = {'en': en}
        for key, ix in (('ko', 3), ('rd', 4)):
            vals = [r[ix] for r in rs if r[ix] and r[5] == en]
            if vals:
                top = max(set(vals), key=vals.count)
                if vals.count(top) >= 2:
                    v[key] = top
        out[k] = v
    return out


def apply_brands(d, tgt, rows, br):
    """좌표로 못 맞춘 행 중 이름이 상호 사전과 같거나(지점명 뗀 뒤) 그 상호로 시작하는 행에 사전 값을 붙인다(via='brand')."""
    keys = sorted(br, key=len, reverse=True)
    first = {}
    for k in keys:
        first.setdefault(k[0], []).append(k)
    n = 0
    for i in tgt:
        if d['id'][i] in rows:
            continue
        nm = norm(d['nm'][i])
        for k in first.get(nm[:1], ()):
            if nm == k or (len(k) >= 3 and nm.startswith(k)):
                v = dict(br[k], via='brand')
                if nm != k and v.get('ko'):  # 지점명이 붙은 행: 한글 상호는 검색 열쇠로만(표시 이름으로 쓰면 지점명이 사라진다)
                    v['ks'] = v.pop('ko')
                rows[d['id'][i]] = v
                n += 1
                break
    return n


def wikidata_ko(rows):
    import urllib.request, urllib.parse
    qs = sorted({v['q'] for v in rows.values() if v.get('q') and not v.get('ko')})
    lab = {}
    for i in range(0, len(qs), 50):
        u = 'https://www.wikidata.org/w/api.php?' + urllib.parse.urlencode({'action': 'wbgetentities', 'ids': '|'.join(qs[i:i + 50]), 'props': 'labels', 'languages': 'ko', 'format': 'json'})
        rq = urllib.request.Request(u, headers={'User-Agent': os.environ.get('TL_WIKI_UA', 'tl-names-pilot/0.1 (batch; contact via operator)')})
        try:
            j = json.load(urllib.request.urlopen(rq, timeout=30))
        except Exception as ex:  # 429 등: 더 두드리지 않고 멈춘다(다음 실행에서 이어 받는다)
            print('wikidata 중단:', getattr(ex, 'code', type(ex).__name__), '받은 묶음', i // 50, flush=True)
            break
        for q, e in (j.get('entities') or {}).items():
            v = ((e.get('labels') or {}).get('ko') or {}).get('value')
            if v and HANGUL.search(v):
                lab[q] = v
        time.sleep(1.0)
    n = 0
    for v in rows.values():
        if not v.get('ko') and v.get('q') in lab:
            v['ko'], v['kos'] = lab[v['q']], 'wikidata'
            n += 1
    return len(qs), n


def main(argv):
    wd = '--wikidata' in argv
    argv = [a for a in argv if a != '--wikidata']
    if len(argv) < 4:
        print(__doc__)
        return 2
    pack_files = [f for f in argv[2].split(',') if f]
    d = load_packs(pack_files)
    tgt = targets(d)
    if not tgt:
        print('보충 대상 행이 없음')
        return 1
    la, lo = [d['_lat'][i] for i in tgt], [d['_lon'][i] for i in tgt]
    bbox = (min(la) - 0.002, min(lo) - 0.002, max(la) + 0.002, max(lo) + 0.002)
    osm = osm_named(argv[3:], bbox)
    rows = match(d, tgt, osm)
    br = brands(osm)
    n_br = apply_brands(d, tgt, rows, br)
    rep = {'targets': len(tgt), 'osm_named': len(osm), 'matched': len(rows), 'ko': sum(1 for v in rows.values() if v.get('ko')), 'ks': sum(1 for v in rows.values() if v.get('ks')),
           'rd': sum(1 for v in rows.values() if v.get('rd')), 'en': sum(1 for v in rows.values() if v.get('en')), 'q': sum(1 for v in rows.values() if v.get('q')), 'brands': len(br), 'by_brand': n_br}
    if wd:
        rep['wikidata_asked'], rep['wikidata_ko'] = wikidata_ko(rows)
    doc = {'schema': 'names-1', 'license': 'ODbL-1.0', 'attribution': '© OpenStreetMap contributors (ODbL 1.0)' + (' · Wikidata (CC0)' if wd else ''),
           'pack': os.path.basename(pack_files[0]), 'packs': [os.path.basename(f) for f in pack_files],
           'bbox': [round(bbox[1], 4), round(bbox[0], 4), round(bbox[3], 4), round(bbox[2], 4)],  # [서, 남, 동, 북] — 앱이 좌표로 고를 때 쓴다
           'built': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'report': rep, 'rows': rows}
    with gzip.open(argv[1], 'wt', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, separators=(',', ':'))
    print(rep, os.path.getsize(argv[1]), 'bytes')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
