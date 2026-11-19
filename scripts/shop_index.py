#!/usr/bin/env python3
"""여행지 쇼핑 층(shop-1) — 체인·상징 매장·'꼭 갈 곳' 판정·팁 근거 코드. (2026-11-20, 256기) 문서 = docs/SHOP_EVENTS.md

  python3 scripts/shop_index.py build --store gs://<b>/collect/v1 --out <폴더> [--chunk 0/8] [--ccs JP,KR] [--patterns <patterns.clean.json>] [--brand-hist <brand_hist.json>]
  python3 scripts/shop_index.py build --store <로컬> --out <폴더> --ovt-json <시험용 Overture 행>   # 시험

자료(전부 공개·공식, 다른 서비스 평점·리뷰·글 없음):
  ① Overture Maps places — 공식 공개 S3 익명 읽기. 쇼핑 분류 + brand.wikidata(체인 식별) + confidence. AllThePlaces(브랜드 공식 매장 목록, CC0)·Foursquare·meta 출처가 섞여 있다.
  ② 우리 수집 층 col_*(OSM·Foursquare OS Places·Wikidata) — 매장 자체 QID(상징 매장: 위키백과 문서가 있는 가게) · OSM brand:wikidata(wd 칸).
  ③ Wikidata 캐시(sitelinks 수) — rec_index.wd_stats 그대로.
  ④ 학습 통계(scripts/patterns, 대표가 허락한 학습 전용) — 나라 안 '쇼핑' 종류 언급 비율 · 브랜드 QID 언급 수 이력(brand_hist.json). 숫자만, 원문·출처는 싣지 않는다.
  ⑤ sources/shop_facts.yaml — 브랜드·매장 공식 사이트에서 사람이 robots 를 확인하고 읽은 공개 사실(면세 조건·개점 연도). 쪽 주소·확인 날짜를 같이 싣는다.
판정(공개 — 앱 '?' 설명과 같음):
  점수 = round(100 × (0.30·conf + 0.30·sitelinks(매장 자체 QID, 없으면 브랜드 QID × 0.5) + 0.20·체인 규모 + 0.20·쇼핑 언급 비율))
     conf = (Overture confidence − 0.5) ÷ 0.5 · sitelinks = ln(1+n) ÷ ln(101) · 체인 규모 = min(나라 안 같은 브랜드 매장 수, 20) ÷ 20
     쇼핑 언급 비율 = 학습 통계(나라 안, 표본 30 미만이면 그 신호를 빼고 나머지 가중치로 다시 나눔)
  꼭 갈 곳(must=1) = ① 사실 파일의 상징 매장(1호점 등) ② 매장 자체 QID 의 sitelinks ≥ ICONIC_SL ③ 점수 ≥ MUST_SCORE 이면서 브랜드 sitelinks ≥ 20(잘 알려진 체인)이고
     그 브랜드에서 도시 칸(약 5 km) 안 점수 1등 매장(같은 체인을 한 도시에서 여러 번 꼽지 않음).
  트렌드(브랜드) = 학습 통계 브랜드 언급의 최근 30일 새 언급 ÷ 그 앞 30일 새 언급 − 1. 앞 30일이 TREND_MIN 미만이면 싣지 않는다(근거 부족).
출력: shop-<cc>-NN.json.gz  schema shop-1
  rows {id: [이름, 위도, 경도, 세부 분류, 브랜드 QID|null, 매장 QID|null, 점수, must 0/1, 근거 코드[]]}
  brands {QID: {n 이름, c 나라 안 매장 수, sl sitelinks|null, tr 트렌드 %|null, f 사실 id|null}} · facts {id: 사실 + url + checked}
  brands_gaz.jsonl.gz(나라 전체 한 파일, --gaz) — 학습기가 브랜드 언급 수를 세는 이름 사전({nm, brand}). 앱에는 보내지 않는다.
근거 코드: iconic(매장 자체 위키 문서) · first(사실 파일: 1호점) · chain(체인 규모) · known(브랜드 sitelinks ≥ 20) · talk(쇼핑 언급 비율 ≥ 0.3) · trend(브랜드 언급 증가 ≥ 30%) · taxfree(사실 파일)
"""
import argparse, gzip, json, math, os, re, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'collect'))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
from common import Store, norm_name, map_cat, today  # noqa: E402
import rec_index as R  # noqa: E402  (Overture 읽기·Wikidata 캐시·파일 나누기·나라 상자 — 같은 일을 두 번 하지 않는다)

VERSION = 'shop-1.0'
W = {'cf': 30, 'sl': 30, 'ch': 20, 'pt': 20}
ICONIC_SL = 3          # 매장 자체 QID 의 sitelinks 가 이만큼이면 상징 매장
MUST_SCORE = 50
KNOWN_SL = 20          # 잘 알려진 브랜드
TREND_MIN = 10         # 앞 30일 새 언급이 이보다 적으면 트렌드를 내지 않음
TREND_UP = 0.30
CITY_CELL = 0.05       # 약 5 km 칸 — '도시 안 같은 체인 한 번'
MIN_CONF = 0.6
SHOP_RE = re.compile(r'store|shop|mall|market|boutique|outlet|departmentstore|duty|souvenir|gift|drugstore|pharmacy|cosmetic|bookstore|toy|electronics|variety')
NOT_SHOP = {'coffeeshop', 'workshop', 'bikeshop', 'repairshop', 'barbershop', 'tattooshop', 'autorepairshop', 'printshop', 'carwash', 'gasstation'}
# 체인이 아니어도 싣는 쇼핑 종류(여행자가 일부러 가는 곳). 그 밖의 무브랜드 동네 가게는 싣지 않는다(크기).
KEEP_SUBS = {'departmentstore', 'shoppingmall', 'market', 'farmersmarket', 'fleamarket', 'souvenirshop', 'giftshop', 'dutyfreeshop', 'outletstore',
             'outletmall', 'discountstore', 'shoppingcenter', 'antiqueshop', 'artsandcraftsstore'}
LICENSES = {**{k: R.LICENSES[k] for k in ('overture_places', 'fsq_os_places', 'alltheplaces', 'wikidata', 'patterns')},
            'osm': {'license': 'ODbL-1.0', 'credit': '© OpenStreetMap contributors (ODbL)', 'url': 'https://www.openstreetmap.org/copyright'},
            'official': {'license': 'facts', 'credit': '브랜드·매장 공식 사이트 공개 정보(사실 id 마다 url·확인 날짜)', 'url': None}}
FIELDS = ['name', 'lat', 'lon', 'sub', 'brand', 'qid', 'score', 'must', 'why']


def is_shop(cat):
    c = (cat or '').lower()
    return bool(c) and c not in NOT_SHOP and bool(SHOP_RE.search(c))


# ---------------------------------------------------------------- 자료 읽기
def ovt_shop_scan(cc, boxes, release, min_conf=MIN_CONF):
    """Overture 공식 S3(익명) → 그 나라 쇼핑 행(브랜드 QID 포함). 무브랜드는 KEEP_SUBS 만."""
    import importlib
    import ovt_common
    os.environ['OVERTURE_RELEASE'] = 'release/' + release
    importlib.reload(ovt_common)
    c = ovt_common.con()
    c.execute(f"SET memory_limit='{os.environ.get('DUCK_MEM', '10GB')}'"); c.execute('SET preserve_insertion_order=false')
    out, seen = [], set()
    for b in boxes:
        cur = c.execute(f"""select id, names.primary, coalesce(basic_category, taxonomy.primary), confidence, brand.wikidata, brand.names.primary,
              list_transform(sources, lambda s: s.dataset), (bbox.xmin+bbox.xmax)/2, (bbox.ymin+bbox.ymax)/2
            from {ovt_common.SRC} where bbox.xmin between {b[0]} and {b[2]} and bbox.ymin between {b[1]} and {b[3]}
            and addresses[1].country = '{cc}' and confidence >= {min_conf} and coalesce(operating_status, 'open') = 'open'""")
        while True:
            batch = cur.fetchmany(50000)
            if not batch:
                break
            for i, nm, cat, cf, bq, bn, ds, lon, lat in batch:
                o = ovt_row(i, nm, cat, cf, bq, bn, ds, lat, lon)
                if o and o['id'] not in seen:
                    seen.add(o['id']); out.append(o)
    return out


def ovt_row(i, nm, cat, cf, bq, bn, ds, lat, lon):
    sub = map_cat(cat)
    q = bq if isinstance(bq, str) and re.fullmatch(r'Q[1-9]\d*', bq or '') else None
    if not nm or lat is None or not is_shop(sub) or (not q and sub not in KEEP_SUBS):
        return None
    return {'id': 'overture_places:' + i, 'name': nm, 'sub': sub, 'cf': round(cf or 0, 3), 'bq': q, 'bn': bn or None, 'la': lat, 'lo': lon,
            'srcs': sorted({d for d in ds or () if d and d != 'Overture'})}


def col_shop_rows(j):
    """수집 층 한 파일 → 쇼핑 행(매장 QID·OSM brand:wikidata). col 의 wd = 매장 QID, bwd(있으면) = 브랜드 QID."""
    n = len(j['id'])
    col = lambda k: j.get(k) if isinstance(j.get(k), list) and len(j[k]) == n else [None] * n
    wd, bwd, src = col('wd'), col('bwd'), col('src')
    cats = j.get('cats') or []
    la = lo = 0
    out = []
    for i in range(n):
        la += j['dla'][i]; lo += j['dlo'][i]
        c = cats[j['c'][i]] if 0 <= j['c'][i] < len(cats) else 'other'
        q = re.search(r'Q[1-9]\d*', wd[i] or '')
        b = re.search(r'Q[1-9]\d*', bwd[i] or '')
        if not (is_shop(c) and (q or b or c in KEEP_SUBS)):
            continue
        out.append({'id': j['id'][i], 'name': j['nm'][i], 'sub': c, 'cf': None, 'bq': b.group(0) if b else None, 'bn': None,
                    'q': q.group(0) if q else None, 'la': la / 1e5, 'lo': lo / 1e5, 'srcs': [s for s in (src[i] or '').split('|') if s]})
    return out


def shop_share(pat):
    """patterns-1 → {나라: 쇼핑 언급 비율 0~1}(나라 안 가장 많은 종류 = 1). 표본 30 미만 나라는 없음."""
    out = {}
    for cc, t in ((pat or {}).get('groups') or {}).get('cc', {}).items():
        sh = (t.get('all') or {}).get('cat_share') if isinstance(t.get('all'), dict) else None
        n = (t.get('all') or {}).get('n_days', 0) if isinstance(t.get('all'), dict) else 0
        if sh and n >= 30 and max(sh.values()) > 0:
            out[cc.upper()] = round(sh.get('shopping', 0) / max(sh.values()), 3)
    return out


def brand_trends(hist, cc, day=None):
    """brand_hist.json({날짜: {나라: {QID: 누적 수}}}) → {QID: 증가율}. 최근 30일 새 언급 ÷ 그 앞 30일 새 언급 − 1."""
    import datetime as D
    if not hist:
        return {}
    days = sorted(hist)
    end = day or days[-1]
    t = D.date.fromisoformat(end)

    def at(delta):  # 그 날짜 또는 그 앞 가장 가까운 기록
        lim = (t - D.timedelta(days=delta)).isoformat()
        ok = [d for d in days if d <= lim]
        return (hist[ok[-1]].get(cc) or {}) if ok else None
    a, b, c = at(0), at(30), at(60)
    if a is None or b is None or c is None:
        return {}
    out = {}
    for q, n in a.items():
        new, prev = n - b.get(q, 0), b.get(q, 0) - c.get(q, 0)
        if prev >= TREND_MIN:
            out[q] = round(new / prev - 1, 3)
    return out


def load_facts(path=None):
    """sources/shop_facts.yaml → [사실]. checked·url 없는 항목은 버린다(근거 없는 문장 금지)."""
    import yaml
    p = path or os.path.join(HERE, '..', 'sources', 'shop_facts.yaml')
    if not os.path.exists(p):
        return []
    rows = (yaml.safe_load(open(p, encoding='utf-8')) or {}).get('facts') or []
    out = [dict(f, checked=str(f['checked'])) for f in rows if f.get('id') and f.get('url') and f.get('checked') and f.get('match')]
    return out


def fact_for(o, facts, cc):
    """행 하나에 맞는 사실들(브랜드 이름·QID·나라·좌표 반경)."""
    nm = norm_name(o.get('bn') or o.get('name') or '')
    hit = []
    for f in facts:
        m = f['match']
        if m.get('cc') and m['cc'] != cc:
            continue
        if m.get('qid') and m['qid'] in (o.get('bq'), o.get('q')):
            pass
        elif not any((norm_name(x) in nm) if m.get('near') else nm.startswith(norm_name(x)) for x in m.get('names') or [] if len(norm_name(x)) >= 2):
            continue  # 매장 하나(near)면 이름 안에 들어 있기만 하면(‘Original Starbucks’), 브랜드 전체면 앞부분
        if m.get('near'):
            la, lo, r = m['near']
            if R.dist_m(la, lo, o['la'], o['lo']) > r:
                continue
        hit.append(f)
    return hit


# ---------------------------------------------------------------- 나라 하나
def f_cf(cf):
    return None if cf is None else max(0.0, min(1.0, (cf - 0.5) / 0.5))


def merge(ovt, col):
    """같은 가게를 한 행으로: 수집 행(매장 QID)을 Overture 행에 붙인다(이름 비슷 ≥ 0.85·60 m 안 또는 Foursquare 출처 같음). 짝 없는 수집 행은 따로."""
    grid = {}
    for k, o in enumerate(ovt):
        grid.setdefault((round(o['la'] / R.CELL), round(o['lo'] / R.CELL)), []).append(k)
    used, out = set(), [dict(o, q=None) for o in ovt]
    for c in col:
        gx, gy = round(c['la'] / R.CELL), round(c['lo'] / R.CELL)
        best, bs = None, 0
        cn = norm_name(c['name'])
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for k in grid.get((gx + dx, gy + dy), ()):
                    if k in used:
                        continue
                    o = ovt[k]
                    d = R.dist_m(c['la'], c['lo'], o['la'], o['lo'])
                    s = R.name_sim(cn, norm_name(o['name']))
                    if d <= 60 and s >= 0.85 and s > bs:
                        best, bs = k, s
        if best is None:
            out.append(dict(c))
        else:
            used.add(best)
            t = out[best]
            t['q'] = t.get('q') or c.get('q'); t['bq'] = t.get('bq') or c.get('bq')
            t['srcs'] = sorted(set(t['srcs']) | set(c['srcs']))
    return out


def build_cc(cc, ovt, col, wd, share, trends, facts):
    rows = merge(ovt, col)
    brand_n, brand_name = {}, {}
    for o in rows:
        if o.get('bq'):
            brand_n[o['bq']] = brand_n.get(o['bq'], 0) + 1
            if o.get('bn'):
                brand_name.setdefault(o['bq'], o['bn'])
    pt = share.get(cc)
    scored = []
    for o in rows:
        own = wd.get(o.get('q') or '', (None, 0))[0] if o.get('q') else None
        bsl = wd.get(o.get('bq') or '', (None, 0))[0] if o.get('bq') else None
        sig = {'cf': f_cf(o.get('cf')), 'sl': R.f_sl(own) if own else (0.5 * R.f_sl(bsl) if bsl else 0.0),
               'ch': min(brand_n.get(o.get('bq'), 0), 20) / 20 if o.get('bq') else 0.0, 'pt': pt}
        use = {k: v for k, v in sig.items() if v is not None}
        score = round(100 * sum(W[k] * v for k, v in use.items()) / sum(W[k] for k in use)) if use else 0
        fs = fact_for(o, facts, cc)
        why = []
        if own and own >= ICONIC_SL:
            why.append('iconic')
        if any(f.get('first_year') for f in fs):
            why.append('first')
        if o.get('bq') and brand_n.get(o['bq'], 0) >= 5:
            why.append('chain')
        if bsl and bsl >= KNOWN_SL:
            why.append('known')
        if pt is not None and pt >= 0.3:
            why.append('talk')
        if o.get('bq') and trends.get(o['bq'], -1) >= TREND_UP:
            why.append('trend')
        if any(f.get('taxfree') for f in fs):
            why.append('taxfree')
        scored.append((o, score, why, [f['id'] for f in fs], bsl))
    # 꼭 갈 곳: 상징 매장 · 1호점 · (잘 알려진 체인의 도시 칸 1등, 점수 ≥ MUST_SCORE)
    best_in_cell = {}
    for k, (o, score, why, fids, bsl) in enumerate(scored):
        if o.get('bq') and 'known' in why and score >= MUST_SCORE:
            key = (o['bq'], round(o['la'] / CITY_CELL), round(o['lo'] / CITY_CELL))
            if key not in best_in_cell or score > scored[best_in_cell[key]][1]:
                best_in_cell[key] = k
    chosen = set(best_in_cell.values())
    out, n_must = {}, 0
    for k, (o, score, why, fids, bsl) in enumerate(scored):
        must = int('iconic' in why or 'first' in why or k in chosen)
        n_must += must
        out[o['id']] = [o['name'][:80], round(o['la'], 5), round(o['lo'], 5), o['sub'], o.get('bq'), o.get('q'), score, must, why + [f'f:{x}' for x in fids]]
    brands = {q: {'n': brand_name.get(q), 'c': n, 'sl': wd.get(q, (None, 0))[0], 'tr': round(trends[q] * 100) if q in trends else None}
              for q, n in brand_n.items()}
    st = {'rows': len(out), 'must': n_must, 'brands': len(brands), 'iconic': sum(1 for v in out.values() if 'iconic' in v[8]),
          'trend': sum(1 for b in brands.values() if b['tr'] is not None), 'facts': sum(1 for v in out.values() if any(w.startswith('f:') for w in v[8]))}
    return out, brands, st


def head(cc, facts_used, extra=None):
    return {'schema': 'shop-1', 'cc': cc, 'src': 'trip_routie_shop', 'built': today(), 'version': VERSION, 'fields': FIELDS, 'method': 'docs/SHOP_EVENTS.md',
            'licenses': LICENSES, 'credit': ' · '.join(v['credit'] for k, v in LICENSES.items() if k not in ('patterns', 'official')),
            'facts': facts_used, **(extra or {})}


def emit(out, cc, rows, brands, facts, first=200000):
    used = {w[2:] for v in rows.values() for w in v[8] if w.startswith('f:')}
    fx = {f['id']: {k: f[k] for k in f if k != 'match'} for f in facts if f['id'] in used}
    return R.write_split(out, f'shop-{cc}', sorted(rows), lambda ks: {**head(cc, fx, {'brands': {q: brands[q] for q in {rows[k][4] for k in ks} if q}}),
                                                                         'rows': {k: rows[k] for k in ks}}, first)


def gaz_lines(brands_all):
    """{QID: 이름} → 학습기 이름 사전 줄(JSON lines)."""
    return [json.dumps({'nm': n, 'brand': q}, ensure_ascii=False) for q, n in sorted(brands_all.items()) if n]


def build(a):
    store = Store(a.store)
    os.makedirs(a.out, exist_ok=True)
    latest = store.get_json('processed/latest.json', None) or {}
    paths = latest.get('paths') or {f: f"processed/out/{latest.get('run')}/{f}" for f in latest.get('files', [])}
    by_cc = {}
    for f, k in paths.items():
        m = re.match(r'col_([A-Z]{2})', f)
        if m and not f.startswith('col_info_'):
            by_cc.setdefault(m.group(1), []).append(k)
    test_ovt = json.load(open(a.ovt_json)) if a.ovt_json else None
    ccs = sorted(set(by_cc) | set(test_ovt or {}))
    if a.ccs:
        ccs = [c for c in ccs if c in a.ccs.split(',')]
    if a.chunk:
        i, n = (int(x) for x in a.chunk.split('/'))
        ccs = [c for k, c in enumerate(ccs) if k % n == i]
    wd = R.wd_stats(store)
    share = shop_share(json.load(open(a.patterns))) if a.patterns and os.path.exists(a.patterns) else {}
    hist = json.load(open(a.brand_hist)) if a.brand_hist and os.path.exists(a.brand_hist) else {}
    facts = load_facts(a.facts)
    boxes = R.cc_boxes()
    rel = None if test_ovt is not None else (a.release or R.latest_release())
    summary = {'version': VERSION, 'release': rel, 'built': today(), 'facts': len(facts), 'share_cc': len(share), 'cc': {}}
    names = {}
    for cc in ccs:
        t0 = time.time()
        d = tempfile.mkdtemp(dir=os.environ.get('RUNNER_TEMP') or None)
        got = store.pull(by_cc.get(cc, []), d, strict=False) if by_cc.get(cc) else {}
        col = [r for p in got.values() for r in col_shop_rows(json.loads(gzip.decompress(open(p, 'rb').read())))]
        ovt = [o for o in (ovt_row(**x) if 'cat' in x else x for x in test_ovt.get(cc, [])) if o] if test_ovt is not None else ovt_shop_scan(cc, boxes.get(cc, []), rel)
        rows, brands, st = build_cc(cc, ovt, col, wd, share, brand_trends(hist, cc), facts)
        emit(a.out, cc, rows, brands, facts)
        names.update({q: b['n'] for q, b in brands.items() if b['n']})
        st['sec'] = round(time.time() - t0)
        summary['cc'][cc] = st
        print(cc, json.dumps(st, ensure_ascii=False), flush=True)  # 개수만
        import shutil
        shutil.rmtree(d, ignore_errors=True)
    if names:
        with gzip.open(os.path.join(a.out, f"brands_gaz_{(a.chunk or 'all').replace('/', 'of')}.jsonl.gz"), 'wt', encoding='utf-8') as f:
            f.write('\n'.join(gaz_lines(names)) + '\n')
    json.dump(summary, open(os.path.join(a.out, f"shop_summary_{(a.chunk or 'all').replace('/', 'of')}.json"), 'w'), ensure_ascii=False)
    return summary


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd', required=True)
    b = sp.add_parser('build')
    b.add_argument('--store', required=True); b.add_argument('--out', required=True)
    b.add_argument('--ccs'); b.add_argument('--chunk'); b.add_argument('--release'); b.add_argument('--patterns'); b.add_argument('--brand-hist')
    b.add_argument('--facts'); b.add_argument('--ovt-json')
    build(ap.parse_args())


if __name__ == '__main__':
    main()
