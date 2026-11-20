#!/usr/bin/env python3
"""사진 우선 층 — 인기 도시의 일정 장소·도시 대표 사진부터 채운다. (2026-11-20, 262기)

    python3 scripts/photo_pri.py build --store gs://<b>/collect/v1 --bucket <b> [--budget-min 50] [--dry-run]
    python3 scripts/photo_pri.py build --store <로컬 폴더> --tiles <w-*.json.gz 폴더> --out <폴더>        # 시험(네트워크는 http 인자로 바꿈)

왜 따로 도나: 11/19 기준 일정 장소 사진 0.0%. place-desc 단계(place_names.py)는 ① Wikidata P18 을 QLever wikidata 끝점에 물어
  4,250곳만 캐시(끝점 429) ② Commons 판정 상태가 'cooling' 으로 묶여 하루 0건 ③ 순서가 QID 번호 순이라 인기 도시가 먼저 오지 않았다.
이 단계는:
  1. 순서 = sources/priority_cities.json 순위 → 그 도시 상자와 겹치는 세계 타일 → 상자 안 행을 타일 점수(s) 높은 순(= 일정 후보 순).
  2. P18 = 주간 덤프 조인(processed/desc/wd_dump.json.gz, wikidata-dump.yml) + place-desc 캐시(wd.json.gz)를 먼저 쓰고,
     없는 우선 QID 만 QLever wikidata 끝점에 묻는다(200개씩, 끝점마다 따로 쉼 — 한 끝점의 429 가 다른 끝점을 막지 않게).
  3. 라이선스·저작자 = QLever wikimedia-commons 끝점(SDC P275·P170). place_names.judge 그대로: CC0·CC BY·CC BY-SA 이고 저작자가 있어야 싣는다.
  4. 도시 대표 사진(행 열쇠 'city:<key>'):
     ① UNSPLASH_ACCESS_KEY 가 있으면 Unsplash 공식 API(검색 1회/도시, 90일마다). 가이드라인대로 photo.urls 주소를 그대로(핫링크) 싣고,
        고를 때 한 번 links.download_location 을 부른다. 표기 = 작가 이름(작가 쪽 링크) + Unsplash(사진 쪽 링크), 링크마다 utm_source=triproutie&utm_medium=referral.
     ② 없으면 그 도시 Wikidata 항목의 P18(상자 안 좌표·영문 이름으로 고른 항목) → Commons 판정(①과 같은 규칙).
  5. 파일: photos-pri-NN.json.gz(행 = 세계 타일 행 id = QID) · photos-city-1.json.gz → places/v1/layers_extra/ (publish 가 함께 게시).
     판정 캐시는 processed/photos/commons.json.gz — place_names.build 가 같이 읽어 photos-wd 도 늘어난다.
사진은 링크만 — 파일을 받거나 복사하지 않는다. 결과는 Storage 에만 두고 저장소에 커밋하지 않는다. 장소 내용은 로그에 쓰지 않는다(개수만).
금지 도메인(common.forbidden)은 Http 가 막는다. Unsplash 는 열쇠로 부르는 공식 API 라 크롤러용 robots 대신 API 가이드라인을 따른다(아래 unsplash_pick).
"""
import argparse, datetime, glob, gzip, json, os, re, shutil, sys, tempfile, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, 'collect')); sys.path.insert(0, os.path.join(ROOT, 'tools'))
from common import Blocked, Http, Stop, Store, forbidden, norm_name, today  # noqa: E402
import place_names as PN  # noqa: E402

WD_EP = 'https://qlever.dev/api/wikidata'
P18_BATCH = 200
CITY_MAX = 3000            # 도시 하나에서 순서에 넣는 일정 후보 수(점수 높은 순)
PRI_SHARDS = 2
UTM = 'utm_source=triproutie&utm_medium=referral'
UNSPLASH_API = 'https://api.unsplash.com'
UNSPLASH_LIC = ('Unsplash License', 'https://unsplash.com/license')
CITY_REFRESH = 90
QID = re.compile(r'^Q\d+$')
QID_IN_ID = re.compile(r'(?:^|[:/])(Q[1-9]\d{0,9})$')  # (263기) 앱 타일 행 id 는 'kowiki:Q123' 처럼 앞 이름이 붙는다(place_desc.QID 와 같은 모양) — 맨몸 Q123 만 보면 일정 후보 0
# priority_cities 의 key → 영문 이름(검색·Wikidata 라벨). 없으면 key 를 단어로 푼다.
CITY_EN = {'okinawa_naha': 'Naha', 'hongkong': 'Hong Kong', 'danang': 'Da Nang', 'ho_chi_minh': 'Ho Chi Minh City', 'kualalumpur': 'Kuala Lumpur'}


def log(msg):
    print('[photo-pri] ' + msg, flush=True)


def city_en(key):
    return CITY_EN.get(key) or ' '.join(w.capitalize() for w in key.split('_'))


def load_cities():
    j = json.load(open(os.path.join(ROOT, 'sources', 'priority_cities.json'), encoding='utf-8'))
    return sorted((c for c in j['cities'] if isinstance(c.get('bbox'), list) and len(c['bbox']) == 4), key=lambda c: c['rank'])


def overlap(a, b):
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


# ---------- 1. 순서: 인기 도시 → 일정 후보 ----------
def rows_in_city(d, bbox, pad=0.02):
    """타일 하나(load_pack 꼴) → 상자 안 QID 행 [(점수, QID)]."""
    out = []
    for i in range(d.get('n') or len(d.get('id') or [])):
        rid = d['id'][i]
        m = QID_IN_ID.search(rid) if isinstance(rid, str) else None
        if not m:
            continue
        la, lo = d['_lat'][i], d['_lon'][i]
        if bbox[0] - pad <= lo <= bbox[2] + pad and bbox[1] - pad <= la <= bbox[3] + pad:
            s = d['s'][i] if isinstance(d.get('s'), list) else 0
            out.append((s or 0, m.group(1), rid))
    return out


def prio_order(cities, tiles_for, keys_of=None):
    """cities 순위대로 [(도시 key, [QID…])]. tiles_for(bbox) → 그 상자와 겹치는 타일(load_pack 꼴) 목록."""
    seen, out = set(), []
    for c in cities:
        rows = []
        for d in tiles_for(c['bbox']):
            rows += rows_in_city(d, c['bbox'])
        rows.sort(key=lambda x: (-x[0], int(x[1][1:]), x[2]))
        qs = []
        for _, q, rid in rows:
            if keys_of is not None and rid != q:
                keys_of.setdefault(q, set()).add(rid)  # 앱 타일 행 id(kowiki:Q123 …) — 사진 행 열쇠로 같이 싣는다
            if q not in seen:
                seen.add(q); qs.append(q)
            if len(qs) >= CITY_MAX:
                break
        out.append((c['key'], qs))
    return out


# 지역 목록의 타일 항목에는 bbox 가 없고 center(위도, 경도)·res(H3 해상도)만 있는 판이 있다(tools/world_region.py) — 그때는 중심 ± 해상도별 여유로 상자를 만든다.
H3_PAD = {3: 1.2, 4: 0.5, 5: 0.2}


def tile_box(t):
    bb = t.get('bbox')
    if isinstance(bb, list) and len(bb) == 4 and all(isinstance(x, (int, float)) for x in bb):
        return bb
    c = t.get('center')
    if isinstance(c, list) and len(c) == 2 and all(isinstance(x, (int, float)) for x in c):
        p = H3_PAD.get(t.get('res'), 1.2)
        return [c[1] - p, c[0] - p, c[1] + p, c[0] + p]
    cells = t.get('h3r5')  # (263기) 실제 지역 목록(part_manifest.py)의 타일 항목에는 bbox·center 가 없고 h3r5(해상도 5 셀 id 목록)만 있다 → 셀 가운데 ± 0.2도(셀 변 약 9km)
    if isinstance(cells, list) and cells and all(isinstance(x, str) for x in cells):
        try:
            import h3
            pts = [h3.cell_to_latlng(x) for x in cells]
        except Exception:
            return None
        return [min(p[1] for p in pts) - 0.2, min(p[0] for p in pts) - 0.2, max(p[1] for p in pts) + 0.2, max(p[0] for p in pts) + 0.2]
    return None


def gcs_tiles_for(bucket, wd):
    """gs 판: 지금 게시 판의 지역 목록에서 상자와 겹치는 타일만 받는다(판마다 한 번)."""
    import place_desc as PD
    import pop_build as PB
    base = f'gs://{bucket}/places/v1'
    ver = (PD.gs_json(f'{base}/manifest.json') or {}).get('version')
    regs = json.load(open(os.path.join(ROOT, 'tools', 'world_regions.json'), encoding='utf-8'))
    tiles = []
    for r in regs if ver else []:
        man = PD.gs_json(f'{base}/{ver}/regions/{r}.json') or PD.gs_json(f'{base}/{ver}/parts/{r}.json') or {}
        for t in man.get('tiles') or []:
            if not (isinstance(t.get('file'), str) and t['file'].startswith('w-') and '/' not in t['file']):
                continue
            bb = tile_box(t)
            if bb:
                tiles.append(dict(t, bbox=bb))
    log(f'판 {ver} · 상자를 아는 세계 타일 {len(tiles):,}')
    got = {}

    def tiles_for(bbox):
        urls = [f"{base}/{ver}/tiles/{t['file']}" for t in tiles if overlap(t['bbox'], bbox)]
        need = [u for u in urls if u not in got]
        for u, _ in PD.pull_urls(need, wd):
            p = os.path.join(wd, u.rsplit('/', 1)[1])
            got[u] = PB.load_pack(p) if os.path.exists(p) else None
        return [got[u] for u in urls if got.get(u)]
    return ver, tiles_for


def local_tiles_for(folder):
    import pop_build as PB
    packs = [PB.load_pack(f) for f in sorted(glob.glob(os.path.join(folder, 'w-*.json.gz')))]

    def tiles_for(bbox):
        out = []
        for d in packs:
            if any(bbox[1] - 0.05 <= la <= bbox[3] + 0.05 and bbox[0] - 0.05 <= lo <= bbox[2] + 0.05 for la, lo in zip(d['_lat'], d['_lon'])):
                out.append(d)
        return out
    return 'local', tiles_for


# ---------- 2. P18 ----------
def p18_sparql(qids):
    return ('PREFIX wd: <http://www.wikidata.org/entity/> PREFIX wdt: <http://www.wikidata.org/prop/direct/> SELECT ?q ?p WHERE { VALUES ?q { '
            + ' '.join('wd:' + q for q in qids) + ' } ?q wdt:P18 ?p }')


def p18_known(q, cache, pcache):
    """(알았나, 주소|None). 덤프·place-desc 캐시(3칸 꼴) → 이 단계 캐시 순."""
    c = cache.get(q)
    if c and len(c) > 2 and isinstance(c[2], dict):
        return True, c[2].get('p')
    if q in pcache:
        return True, pcache[q][1]
    return False, None


def fetch_p18(http, qids, pcache, budget_end, clock=time.time):
    done = 0
    for i in range(0, len(qids), P18_BATCH):
        if clock() > budget_end:
            return done, 'TIME'
        part = qids[i:i + P18_BATCH]
        url = WD_EP + '?' + urllib.parse.urlencode({'query': p18_sparql(part)})
        try:
            body = http.get(url, accept='application/sparql-results+json')
            j = json.loads(body)
        except Stop as e:
            return done, str(e)
        except Blocked as e:
            return done, f'BLOCKED {e}'
        except (IOError, ValueError):
            return done, 'FETCH'
        got = {}
        for b in ((j or {}).get('results') or {}).get('bindings') or []:
            try:
                q, p = b['q']['value'].rsplit('/', 1)[1], b['p']['value']
            except (KeyError, IndexError):
                continue
            got.setdefault(q, p)
        d = today()
        for q in part:
            pcache[q] = [d, got.get(q)]
        done += len(part)
    return done, None


# ---------- 3. 도시 대표 사진 ----------
def city_sparql(name):
    """영문 이름이 같은 항목 중 P18·좌표가 있는 것(사이트 링크 수 같이). 상자 검사는 pick_city_item 이 한다."""
    n = name.replace('\\', '').replace('"', '')
    return ('PREFIX wdt: <http://www.wikidata.org/prop/direct/> PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> '
            'PREFIX wikibase: <http://wikiba.se/ontology#> '
            f'SELECT ?c ?p ?co ?sl WHERE {{ ?c rdfs:label "{n}"@en . ?c wdt:P18 ?p . ?c wdt:P625 ?co . OPTIONAL {{ ?c wikibase:sitelinks ?sl }} }} LIMIT 50')


POINT = re.compile(r'Point\(\s*(-?[\d.]+)\s+(-?[\d.]+)\s*\)')


def pick_city_item(j, bbox):
    """SPARQL 결과 → (QID, P18) | None. 좌표가 상자 안인 것만, 사이트 링크 많은 순."""
    best = None
    for b in ((j or {}).get('results') or {}).get('bindings') or []:
        try:
            q, p = b['c']['value'].rsplit('/', 1)[1], b['p']['value']
            m = POINT.search(b['co']['value'])
            lo, la = float(m.group(1)), float(m.group(2))
        except (KeyError, IndexError, AttributeError, ValueError):
            continue
        if not (bbox[0] <= lo <= bbox[2] and bbox[1] <= la <= bbox[3]):
            continue
        sl = int(float(b.get('sl', {}).get('value') or 0))
        if best is None or sl > best[0]:
            best = (sl, q, p)
    return (best[1], best[2]) if best else None


def utm(u):
    return u + ('&' if '?' in u else '?') + UTM


def unsplash_row(ph):
    """Unsplash API 사진 한 장 → photos-1 행(핫링크·작가·Unsplash 표기). 칸이 모자라면 None."""
    try:
        urls, user, links = ph['urls'], ph['user'], ph['links']
        url, thumb, name = urls['regular'], urls.get('small') or urls['regular'], (user.get('name') or user['username']).strip()
        page, prof = links['html'], user['links']['html']
    except (KeyError, TypeError, AttributeError):
        return None
    if not all(isinstance(x, str) and x.startswith('https://') for x in (url, thumb, page, prof)) or not name:
        return None
    return {'photo_url': url, 'photo_thumb': thumb, 'photo_credit': f'Photo: {name[:80]} · Unsplash', 'photo_license': UNSPLASH_LIC[0],
            'photo_license_url': UNSPLASH_LIC[1], 'photo_author': name[:80], 'photo_author_url': utm(prof), 'photo_page': utm(page),
            'photo_src': 'unsplash', 'photo_id': str(ph.get('id') or '')[:40]}


def _unsplash_get(url, key, opener=None):
    req = urllib.request.Request(url, headers={'Authorization': f'Client-ID {key}', 'Accept-Version': 'v1', 'User-Agent': 'TripRoutie-pipeline/1.0'})
    if opener:
        return opener(req)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def unsplash_pick(name, key, opener=None):
    """Unsplash 공식 API: 검색 1회 → 가로 사진 첫 장 → download_location 1회(가이드라인 '다운로드 비슷한 동작' = 우리가 도시 사진으로 고름).
    돌려줌 (행|None, 이유). 열쇠는 로그·결과에 남기지 않는다."""
    q = urllib.parse.urlencode({'query': f'{name} city', 'orientation': 'landscape', 'per_page': 10, 'content_filter': 'high'})
    try:
        j = _unsplash_get(f'{UNSPLASH_API}/search/photos?{q}', key, opener)
    except Exception as e:  # noqa: BLE001 — 이유 이름만
        return None, f'search {type(e).__name__}'
    for ph in (j or {}).get('results') or []:
        row = unsplash_row(ph)
        if not row:
            continue
        dl = ((ph.get('links') or {}).get('download_location') or '')
        if dl.startswith(UNSPLASH_API + '/'):
            try:
                _unsplash_get(dl, key, opener)
            except Exception:  # noqa: BLE001 — 집계 실패는 사진 쓰기를 막지 않는다
                pass
        return row, None
    return None, 'none'


def city_rows(cities, cstate, ccache, http_wd, budget_end, key=None, opener=None, clock=time.time, now_day=None, items=None):
    """도시마다 행 'city:<key>'. cstate = {key: {at, src, row?, q?, p?}} (processed/photos/cities.json).
    items = Wikidata 덤프에서 고른 도시 항목 {key: {q, p, sl}} (processed/photos/city_items.json, 270기) — 있으면 QLever 에 묻지 않는다."""
    d0 = datetime.date.fromisoformat(now_day or today())
    rows, files = {}, []
    for c in cities:
        k, st = c['key'], cstate.get(c['key']) or {}
        fresh = st.get('at') and (d0 - datetime.date.fromisoformat(st['at'])).days < CITY_REFRESH
        if key and not (fresh and st.get('src') == 'unsplash') and clock() < budget_end:
            row, why = unsplash_pick(city_en(k), key, opener)
            if row:
                st = {'at': today(), 'src': 'unsplash', 'row': row}
            else:
                st['unsplash_err'] = why
        it = (items or {}).get(k)
        if st.get('src') != 'unsplash' and it and it.get('p') and (st.get('q'), st.get('p')) != (it.get('q'), it['p']):
            st.update(q=it.get('q'), p=it['p'], at=today(), src='commons')
            st.pop('wd_err', None)
        if st.get('src') != 'unsplash' and not st.get('q') and http_wd is not None and clock() < budget_end:
            url = WD_EP + '?' + urllib.parse.urlencode({'query': city_sparql(city_en(k))})
            try:
                hit = pick_city_item(json.loads(http_wd.get(url, accept='application/sparql-results+json')), c['bbox'])
            except (Blocked, Stop, IOError, ValueError) as e:
                hit = None
                st['wd_err'] = type(e).__name__ + (f' {e}' if isinstance(e, (Blocked, Stop)) else '')
            if hit:
                st.update(q=hit[0], p=hit[1], at=today(), src='commons')
        cstate[k] = st
        if st.get('src') == 'unsplash' and st.get('row'):
            rows[f'city:{k}'] = dict(st['row'], city=k)
        elif st.get('p'):
            # (270기 2차) 후보 여러 장(P18·P948 배너·P2716 콜라주·같은 이름 다른 항목·상자 안 명소 P18) 중 판정이 자유 라이선스+저작자인 첫 장
            cands = list(dict.fromkeys([st['p']] + list((it or {}).get('files') or [])))
            for u in cands:
                cc = ccache.get(u)
                v = PN.photo_row(u, cc[1]) if cc else None
                if v:
                    rows[f'city:{k}'] = dict(v, city=k, q=st.get('q'))
                    break
                if not cc:
                    files.append(u)
    return rows, files


# ---------- 4. 실행 ----------
CITY_GEO_MORE = 40


def city_fallback(items, geo, n=CITY_GEO_MORE):
    """(270기) 도시 후보가 모두 비자유·저작자 없음일 때(오키나와 나하 7/7): 상자 안 명소 P18 을 sitelinks 큰 순으로 n 장 더 뒤에 붙인다(판정은 commons_dump 가 이미 함)."""
    out = {}
    for k, v in items.items():
        more = [g[4] for g in sorted((g for g in geo.get(k) or [] if isinstance(g, list) and len(g) > 4), key=lambda g: (-g[3], g[0]))[:n]]
        out[k] = dict(v, files=list(dict.fromkeys(list(v.get('files') or []) + more)))
    return out


def miss_diag(sight_ids, rows, targets, geo, sample=10):
    """(270기) 관광·명소 상위 50 중 사진 없는 행 → 원인 개수. 도시마다 앞 10개 표본과 전체를 따로 센다(장소 이름·id 는 남기지 않음).
    원인: no_name(타일 행에 이름 없음) · no_qid_no_geo(QID 없고 100m 안 Wikidata 좌표 항목도 없음) · geo_near_name_diff(100m 안 항목 있으나 이름 다름) ·
          qid_or_geo(QID 또는 이름 맞는 항목이 있으나 자유 라이선스 사진 없음)."""
    out = {}
    for k, ids in sight_ids.items():
        tmap = {t[0]: t for t in targets.get(k) or []}
        grid = {}
        for g in geo.get(k) or []:
            if isinstance(g, list) and len(g) >= 6:
                grid.setdefault((int(g[1] * 500), int(g[2] * 500)), []).append(g)
        miss = [r for r in ids[:TOP_N] if r not in rows]
        def why(r):
            t = tmap.get(r)
            if not t or not t[3]:
                return 'no_name'
            if t[4]:
                return 'qid_or_geo'
            near = [g for di in (-1, 0, 1) for dj in (-1, 0, 1) for g in grid.get((int(t[1] * 500) + di, int(t[2] * 500) + dj), ()) if _dist_m(t[1], t[2], g[1], g[2]) <= GEO_MAX_M]
            if not near:
                return 'no_qid_no_geo'
            ok = any(name_sim(a, geo_norm(b)) >= GEO_SIM for g in near for a in t[3] for b in g[5])
            return 'qid_or_geo' if ok else 'geo_near_name_diff'
        c_all, c_s = {}, {}
        for i, r in enumerate(miss):
            w = why(r)
            c_all[w] = c_all.get(w, 0) + 1
            if i < sample:
                c_s[w] = c_s.get(w, 0) + 1
        if miss:
            out[k] = {'miss': len(miss), 'sample10': c_s, 'all': c_all}
    return {k: out[k] for k in ('jeju', 'busan', 'seoul', 'tokyo', 'osaka', 'bangkok', 'paris') if k in out}


def city_diag(cities, cstate, ccache, items=None, made=None):
    """도시 사진이 왜 0 인지 보이게(263기 관측): 단계별 도시 수. 상태 파일의 오류 이름만 센다(주소·열쇠 없음)."""
    d = {'no_state': 0, 'unsplash': 0, 'wd_hit': 0, 'judged': 0, 'free_license': 0, 'judged_not_free': 0}
    errs = {}
    for c in cities:
        st = cstate.get(c['key']) or {}
        if not st:
            d['no_state'] += 1
        if st.get('src') == 'unsplash' and st.get('row'):
            d['unsplash'] += 1
        if st.get('p'):
            d['wd_hit'] += 1
            cc = ccache.get(st['p'])
            if cc:
                d['judged'] += 1
                if PN.photo_row(st['p'], cc[1]):
                    d['free_license'] += 1
                else:
                    d['judged_not_free'] += 1
        if made is not None and f"city:{c['key']}" not in made:  # (270기) 도시 사진이 빠진 도시: 후보 파일 판정 상태 개수(이름·주소 없음)
            fs = list(dict.fromkeys([st.get('p')] + list(((items or {}).get(c['key']) or {}).get('files') or [])))
            fs = [u for u in fs if u]
            d.setdefault('missing', {})[c['key']] = {'files': len(fs), 'unjudged': sum(1 for u in fs if u not in ccache),
                                                     'not_free': sum(1 for u in fs if u in ccache and not PN.photo_row(u, ccache[u][1]))}
        for k in ('wd_err', 'unsplash_err'):
            if st.get(k):
                errs[f"{k}:{st[k]}"] = errs.get(f"{k}:{st[k]}", 0) + 1
    d['errs'] = errs
    return d


def judge_files(prio, cache, pcache):
    """우선 순서대로 Commons 파일 주소(중복 없이)."""
    seen, out = set(), []
    for _, qs in prio:
        for q in qs:
            _, u = p18_known(q, cache, pcache)
            if u and u.startswith(PN.FILEPATH) and u not in seen:
                seen.add(u); out.append(u)
    return out


def pri_rows(prio, cache, pcache, ccache, keys_of=None):
    """행 열쇠 = QID(타일 행 id 가 QID 인 곳) + keys_of 의 수집 행 id·wid(앱 타일 행 id) — photos-wd 와 같은 열쇠."""
    keys_of = keys_of or {}
    rows, by_city = {}, {}
    for k, qs in prio:
        n = 0
        for q in qs:
            _, u = p18_known(q, cache, pcache)
            cc = ccache.get(u) if u else None
            v = PN.photo_row(u, cc[1]) if cc else None
            if v:
                for rk in {q, *keys_of.get(q, ())}:
                    rows[rk] = dict(v, q=q)
                n += 1
        by_city[k] = (len(qs), n)
    return rows, by_city


# ---------- 2b. 한국: TourAPI 공식 사진을 타일 행에 이름+좌표로 붙인다(263기) ----------
# 한국 타일 행 id 는 fsq:·해시뿐이라 Q-id(P18) 길이 없다. TourAPI 장소(kto_*.json.gz, 공공누리 제1·3유형 사진만 이미 걸러져 있음)를
# 같은 이름 · 50m 이내인 타일 행에 붙인다. 제3유형은 no_modify(자르기·보정 금지) 그대로. 사진 주소는 http 면 https 로(TourAPI 이미지 서버는 https 도 응답).
KTO_MAX_M = 50.0
KOGL_URL = {'KOGL-1': 'https://www.kogl.or.kr/info/licenseType1.do', 'KOGL-3': 'https://www.kogl.or.kr/info/licenseType3.do'}


def _https(u):
    return 'https://' + u[7:] if isinstance(u, str) and u.startswith('http://') else u


def kto_photo_row(ph):
    """카드의 photo {url, license(KOGL-1|3), credit, thumb?, no_modify?} → photos-1 행. 라이선스가 1·3 이 아니거나 주소가 https 가 아니면 None."""
    if not isinstance(ph, dict) or ph.get('license') not in KOGL_URL:
        return None
    url = _https(ph.get('url'))
    if not isinstance(url, str) or not url.startswith('https://'):
        return None
    thumb = _https(ph.get('thumb')) if isinstance(ph.get('thumb'), str) else url
    lic = ph['license']
    r = {'photo_url': url, 'photo_thumb': thumb, 'photo_credit': f"사진: 한국관광공사 · {'공공누리 제1유형' if lic == 'KOGL-1' else '공공누리 제3유형(변경 금지)'}",
         'photo_license': lic, 'photo_license_url': KOGL_URL[lic], 'photo_author': '한국관광공사', 'photo_src': 'tourapi'}
    if lic == 'KOGL-3' or ph.get('no_modify'):
        r['no_modify'] = True
    return r


def _dist_m(la1, lo1, la2, lo2):
    import math
    return math.hypot((la1 - la2) * 111320.0, (lo1 - lo2) * 111320.0 * math.cos(math.radians((la1 + la2) / 2)))


def _name_ok(a, b):
    """정규화한 이름이 같거나, 한쪽이 다른 쪽을 품는다(3글자 이상 — '공원'처럼 흔한 말이 아무데나 붙지 않게)."""
    if not a or not b:
        return False
    return a == b or (min(len(a), len(b)) >= 3 and (a in b or b in a))


def kto_match(cards, tiles, bbox, max_m=KTO_MAX_M):
    """cards = [{n, lat, lon, photo}], tiles = load_pack 꼴 목록 → ({타일 행 id: 사진 행}, 상자 안 카드 수, 사진 있는 카드 수)."""
    grid, inbox = {}, 0
    for c in cards:
        la, lo = c.get('lat'), c.get('lon')
        if not isinstance(la, (int, float)) or not isinstance(lo, (int, float)) or not (bbox[0] <= lo <= bbox[2] and bbox[1] <= la <= bbox[3]):
            continue
        row = kto_photo_row(c.get('photo'))
        if not row:
            continue
        inbox += 1
        grid.setdefault((int(la * 500), int(lo * 500)), []).append((la, lo, norm_name(c.get('n') or ''), row))  # 0.002도 칸(약 200m) — 이웃 9칸만 본다
    out = {}
    if not grid:
        return out, 0, 0
    for d in tiles:
        ids, la_, lo_ = d.get('id') or [], d.get('_lat') or [], d.get('_lon') or []
        for i in range(min(len(ids), len(la_), len(lo_))):
            la, lo = la_[i], lo_[i]
            gi, gj = int(la * 500), int(lo * 500)
            best = None
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for cla, clo, cn, row in grid.get((gi + di, gj + dj), ()):
                        dm = _dist_m(la, lo, cla, clo)
                        if dm > max_m or (best and dm >= best[0]):
                            continue
                        names = [norm_name(str(d[k][i])) for k in ('ko', 'nm', 'en') if isinstance(d.get(k), list) and i < len(d[k]) and d[k][i]]
                        if any(_name_ok(cn, nm) for nm in names):
                            best = (dm, row)
            if best and isinstance(ids[i], str):
                out[ids[i]] = dict(best[1])
    return out, inbox, len(out)


def load_kto_cards(bucket, wd):
    """gs 판: places/v1/layers_extra/kto_*.json.gz(TourAPI 장소 층) 의 사진 있는 카드. 한 번에 모두 읽는다(시도 17개 안팎, 수 MB)."""
    import place_desc as PD
    base = f'gs://{bucket}/places/v1/layers_extra/'
    r = PD.gs('ls', base + 'kto_*.json.gz')
    urls = [u for u in (r.stdout.decode().split() if r.stdout else []) if u.rsplit('/', 1)[-1].startswith('kto_') and u.endswith('.json.gz')]
    cards = []
    for _, j in PD.pull_urls(urls, wd):
        for k, e in ((j or {}).get('rows') or {}).items():
            if isinstance(e, dict) and e.get('photo') and 'lat' in e:
                cards.append({'n': e.get('n'), 'lat': e['lat'], 'lon': e['lon'], 'photo': e['photo'], 'cid': k})
    log(f'TourAPI 사진 카드 {len(cards):,}')
    return cards


def pri_doc(rows, part):
    return {'schema': 'photos-1', 'region': 'priority', 'part': part, 'license': 'mixed — 행마다 photo_license(CC0·CC BY·CC BY-SA)',
            'attribution': '사진: 위키미디어 공용(사진마다 저작자·라이선스) · 사진 고르기: Wikidata P18 (CC0)', 'built': today(), 'rows': rows}


def city_doc(rows):
    return {'schema': 'photos-1', 'region': 'cities', 'license': 'mixed — 행마다 photo_license(Unsplash License · CC0 · CC BY · CC BY-SA)',
            'attribution': '도시 사진: Unsplash(작가·Unsplash 표기, 핫링크) 또는 위키미디어 공용(저작자·라이선스)', 'built': today(), 'rows': rows}


def run(store, bucket=None, tiles_dir=None, out=None, budget_min=50.0, http_wd=None, http_c=None, key=None, opener=None, clock=time.time, dry_run=False, src=None, kto_cards=None, kr_key=None):
    t_end = clock() + budget_min * 60
    src = src or {}
    cities = load_cities()
    pm = None if tiles_dir else store.get_json('processed/photos/prio_map.json.gz', None)
    keys_of = {}
    if pm and any(rows for _, rows in pm.get('cities') or []):
        # place-desc 가 남긴 '인기 도시 상자 안 수집 행' 지도(행 열쇠 = 앱 타일 행 id 포함) — 이것을 먼저 쓴다
        prio = []
        for key, rows in pm['cities']:
            prio.append((key, [q for q, _ in rows]))
            for q, ks in rows:
                keys_of.setdefault(q, set()).update(ks)
        log(f"순서(수집 행 지도 {pm.get('built')}): 도시 {sum(1 for _, qs in prio if qs)}/{len(prio)} · QID {sum(len(qs) for _, qs in prio):,}")
    wd = tempfile.mkdtemp(dir=os.environ.get('RUNNER_TEMP') or None)
    try:
        ver, tiles_for = local_tiles_for(tiles_dir) if tiles_dir else gcs_tiles_for(bucket, wd)
        pkey = f'processed/photos/prio_{ver}.json.gz'
        cached = store.get_json(pkey, None) if ver != 'local' and not keys_of else None
        if not keys_of:
            if isinstance(cached, dict):  # 263기 모양: {prio, keys}. 옛 모양(목록)은 행 id 가 없어 다시 계산
                prio = [tuple(x) for x in cached['prio']]
                keys_of = {q: set(v) for q, v in (cached.get('keys') or {}).items()}
            else:
                tk = {}
                prio = prio_order(cities, tiles_for, tk)
                keys_of = tk
                if ver and ver != 'local' and any(qs for _, qs in prio):  # 빈 순서는 남기지 않는다(다음 실행에서 다시)
                    store.put_json(pkey, {'prio': prio, 'keys': {q: sorted(v) for q, v in tk.items()}})
        log(f"순서: 도시 {sum(1 for _, qs in prio if qs)}/{len(prio)} · 일정 후보 QID {sum(len(qs) for _, qs in prio):,}")
        kto_rows, kto_stat = {}, {}
        try:
            cards = kto_cards if kto_cards is not None else (load_kto_cards(bucket, wd) if bucket and not tiles_dir else [])
            for c in cities:
                if any(isinstance(x.get('lat'), (int, float)) and c['bbox'][1] <= x['lat'] <= c['bbox'][3] and c['bbox'][0] <= x['lon'] <= c['bbox'][2] for x in cards):
                    got, nc, nm = kto_match(cards, tiles_for(c['bbox']), c['bbox'])
                    kto_rows.update(got)
                    kto_stat[c['key']] = (nc, nm)
        except Exception as e:  # noqa: BLE001 — TourAPI 붙이기가 실패해도 P18 길은 계속(이름만 기록)
            log(f'TourAPI 붙이기 실패 {type(e).__name__}')
        city_ids, geo_hits, sight_ids, targets = {}, {}, {}, {}
        geo = (store.get_json('processed/photos/geo_items.json.gz', None) or {}).get('cities') or {}
        qnames = {g[0]: g[5] for items in geo.values() for g in items if isinstance(g, list) and len(g) >= 6}  # (270기) QID → 라벨·별칭(ja·th·fr·en 등 20개 언어)
        try:
            for c in cities:
                ts = tiles_for(c['bbox'])
                city_ids[c['key']] = city_tile_ids(ts, c['bbox'])
                sight_ids[c['key']] = city_tile_ids(ts, c['bbox'], sight_only=True)
                if geo.get(c['key']):
                    geo_hits.update(geo_match(ts, geo[c['key']], c['bbox']))
                targets[c['key']] = city_targets(ts, c['bbox'], city_ids[c['key']][:TOP_N] + sight_ids[c['key']][:TARGET_SIGHT], geo_hits, qnames)
        except Exception as e:  # noqa: BLE001 — 측정 실패는 층 만들기를 막지 않는다(이름만)
            log(f'도시별 타일 행 측정 실패 {type(e).__name__}')
    finally:
        shutil.rmtree(wd, ignore_errors=True)
    if any(targets.values()) and not dry_run:
        store.put_json('processed/photos/targets.json.gz', {'built': today(), 'cities': targets})  # commons_dump 가 촬영 좌표로 사진을 찾을 장소(270기)
    cache = store.get_json('processed/desc/wd.json.gz', {}) or {}
    for q, v in (store.get_json('processed/desc/wd_dump.json.gz', None) or {}).items():
        if q not in cache or len(cache[q]) < 3 or cache[q][0] < v[0]:
            cache[q] = v
    pcache = store.get_json('processed/photos/p18.json.gz', {}) or {}
    st = store.get_json('processed/photos/state.json', {}) or {}
    # 끝점마다 상태를 따로 둔다(한 끝점의 429·403 쉼이 다른 끝점을 막지 않게). 쉼 자체는 그대로 지킨다.
    if http_wd is None:
        http_wd = Http({'daily_limit': src.get('p18_daily_limit', 300), 'interval_s': src.get('interval_s', 15), 'timeout_s': 180},
                       st.setdefault('http_wd', {}), st.setdefault('hosts_wd', {}), deadline=t_end)
    if http_c is None:
        http_c = Http({'daily_limit': src.get('daily_limit', 1200), 'interval_s': src.get('interval_s', 15), 'timeout_s': 180},
                      st.setdefault('http_c', {}), st.setdefault('hosts_c', {}), deadline=t_end)
    need = [q for _, qs in prio for q in qs if not p18_known(q, cache, pcache)[0]]
    p_asked, p_stop = fetch_p18(http_wd, need, pcache, t_end - budget_min * 60 * 0.5, clock) if need else (0, None)
    store.put_json('processed/photos/p18.json.gz', pcache)
    ccache = store.get_json('processed/desc/commons.json.gz', {}) or {}
    mine = store.get_json('processed/photos/commons.json.gz', {}) or {}
    for u, v in mine.items():
        if u not in ccache or ccache[u][0] < v[0]:
            ccache[u] = v
    cstate = store.get_json('processed/photos/cities.json', {}) or {}
    items = (store.get_json('processed/photos/city_items.json', None) or {}).get('cities') or {}
    items = city_fallback(items, (store.get_json('processed/photos/geo_items.json.gz', None) or {}).get('cities') or {})
    crow, cfiles = city_rows(cities, cstate, ccache, http_wd, t_end - budget_min * 60 * 0.4, key=key, opener=opener, clock=clock, items=items)
    todo = [u for u in cfiles + judge_files(prio, cache, pcache) if u not in ccache]
    todo = list(dict.fromkeys(todo))
    asked, stop = PN.fetch_commons(http_c, todo, mine, t_end, clock) if todo else (0, None)
    ccache.update({u: v for u, v in mine.items() if u in todo})
    store.put_json('processed/photos/commons.json.gz', mine)
    crow, _ = city_rows(cities, cstate, ccache, None, 0, key=None, clock=clock, items=items)  # 판정 뒤 다시(요청 없음)
    store.put_json('processed/photos/cities.json', cstate)
    cdiag = city_diag(cities, cstate, ccache, items, crow)
    cdiag['cooling'] = {f'{n}:{k}': {x: y for x, y in (v or {}).items() if x in ('cool_until', 'code', 'ra')} for n, hs in (('wd', st.get('hosts_wd')), ('c', st.get('hosts_c'))) for k, v in list((hs or {}).items())[:3]}
    rows, by_city = pri_rows(prio, cache, pcache, ccache, keys_of)
    for rk, v in kto_rows.items():
        rows.setdefault(rk, v)  # P18(Commons)가 이미 있는 행이 먼저
    kr_got, kr_why, kr_asked = {}, {}, 0
    if targets:
        lcache = store.get_json('processed/photos/kr_loc2.json.gz', {}) or {}
        sight_t = {k: [t for t in targets.get(k) or [] if t[0] in set(sight_ids.get(k, [])[:TOP_N])] for k in KR_CITIES}
        kr_got, kr_why, kr_asked = kr_loc(sight_t, rows, lcache, kr_key)
        if kr_asked:
            store.put_json('processed/photos/kr_loc2.json.gz', lcache)
        for rk, v in kr_got.items():
            rows.setdefault(rk, v)
    pov_n = 0
    for rk, v in sorted(((store.get_json('processed/photos/pov.json.gz', None) or {}).get('rows') or {}).items()):
        # (270기) Commons 덤프의 촬영 좌표·묘사 대상으로 고른 사진(자유 라이선스+저작자, 50m) — P18·TourAPI·이름 맞춤이 없는 행만
        r = PN.photo_row(v[2], v[3]) if isinstance(v, list) and len(v) > 3 else None
        if r and rk not in rows:
            rows[rk] = dict(r, match='pov_' + str(v[0])); pov_n += 1
    geo_n = 0
    for rk, (q, u) in sorted(geo_hits.items()):  # (270기 2차) 이름+좌표로 맞춘 덤프 항목의 P18 — 판정이 자유+저작자인 것만
        cc = ccache.get(u)
        v = PN.photo_row(u, cc[1]) if cc else None
        if v and rk not in rows:
            rows[rk] = dict(v, q=q, match='name_geo'); geo_n += 1
    out = out or tempfile.mkdtemp(prefix='ppri_', dir=os.environ.get('RUNNER_TEMP') or None)
    os.makedirs(out, exist_ok=True)
    for f in glob.glob(os.path.join(out, 'photos-pri-*.json.gz')) + glob.glob(os.path.join(out, 'photos-city-*.json.gz')):
        os.remove(f)
    made = {}
    if rows:
        made.update(PN.write_sharded(out, 'photos-pri', rows, pri_doc, n=PRI_SHARDS))
    if crow:
        p = os.path.join(out, 'photos-city-1.json.gz')
        with open(p, 'wb') as f:
            f.write(gzip.compress(json.dumps(city_doc(crow), ensure_ascii=False, separators=(',', ':')).encode()))
        made['photos-city-1.json.gz'] = len(crow)
    rep = {'p18_asked': p_asked, 'p18_left': len(need) - p_asked, 'p18_stop': p_stop, 'commons_asked': asked, 'commons_left': len(todo) - asked,
           'commons_stop': stop, 'rows': len(rows), 'cities': len(crow), 'unsplash': sum(1 for v in crow.values() if v.get('photo_src') == 'unsplash'),
           'by_city': by_city, 'city_diag': cdiag, 'kto': {k: list(v) for k, v in kto_stat.items()}, 'tile_cov': tile_cov(city_ids, rows), 'geo_matched': len(geo_hits), 'geo_rows': geo_n, 'pov_rows': pov_n, 'sight_cov': tile_cov(sight_ids, rows),
           'kr_loc': {'asked': kr_asked, 'rows': len(kr_got), 'why': kr_why}, 'miss': miss_diag(sight_ids, rows, targets, geo)}
    st['last'] = dict(rep, at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    store.put_json('processed/photos/state.json', st)
    if bucket and not dry_run:
        import place_desc as PD
        dest = f'gs://{bucket}/places/v1/layers_extra/'
        paths = [os.path.join(out, f) for f in sorted(made)]
        if paths and PD.gs('cp', '-q', '-I', dest, stdin='\n'.join(paths).encode()).returncode != 0:
            raise SystemExit('사진 우선 층 올리기 실패')
        r = PD.gs('ls', dest + 'photos-pri-*.json.gz')
        stale = [u for u in (r.stdout.decode().split() if r.stdout else []) if u.endswith('.json.gz') and u.rsplit('/', 1)[1] not in made]
        if stale:
            PD.gs('rm', '-q', '-I', stdin='\n'.join(stale).encode())
    lines = report_lines(rep, cities)
    p = os.environ.get('GITHUB_STEP_SUMMARY')
    if p:
        open(p, 'a', encoding='utf-8').write('\n'.join(lines) + '\n')
    print('\n'.join(lines))
    return rep, sorted(made)


TOP_N = 50
KR_LOC_API = 'https://apis.data.go.kr/B551011/KorService2/locationBasedList2'
KR_LOC_TYPES = {'12', '14', '15'}   # 관광지·문화시설·축제공연행사
KR_LOC_RADIUS = 200
KR_LOC_MAX = 150                    # 하루 호출 한도(tourapi_daily 900 회와 합쳐 data.go.kr 1,000 안쪽 — 도시 3곳 × 상위 50)
KR_LOC_REFRESH = 30
KR_CITIES = ('jeju', 'busan', 'seoul')
KR_BR = re.compile(r'\[[^\]]*\]|\([^)]*\)|（[^）]*）|<[^>]*>')


def kr_keys(s):
    """한국어 이름 정규화 열쇠: NFKC·소문자·기호 제거 + 괄호·대괄호 덧붙임 뺀 꼴."""
    if not isinstance(s, str):
        return set()
    return {k for k in (norm_name(s), norm_name(KR_BR.sub(' ', s))) if len(k) >= 2}


def kr_loc_get(la, lo, key, opener=None):
    q = urllib.parse.urlencode({'serviceKey': key, 'MobileOS': 'ETC', 'MobileApp': 'TripRoutie', '_type': 'json', 'mapX': f'{lo:.6f}', 'mapY': f'{la:.6f}',
                                'radius': KR_LOC_RADIUS, 'numOfRows': 30, 'pageNo': 1, 'arrange': 'E'})
    req = urllib.request.Request(f'{KR_LOC_API}?{q}', headers={'User-Agent': 'TripRoutie-pipeline/1.0'})
    if opener:
        return opener(req)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


class KrApiError(Exception):
    pass


def kr_loc_items(j):
    """응답 → 항목 목록. 머리(resultCode)가 정상(0000)이 아니면 KrApiError(코드만) — 오류 응답을 '항목 없음'으로 캐시하지 않게(270기)."""
    hd = (((j or {}).get('response') or {}).get('header') or {})
    if not hd or str(hd.get('resultCode')) not in ('0000', '00', '0'):
        raise KrApiError(str(hd.get('resultCode') or 'nohdr')[:12])
    it = ((((j or {}).get('response') or {}).get('body') or {}).get('items') or {})
    it = it.get('item') if isinstance(it, dict) else []
    return [it] if isinstance(it, dict) else (it or [])


def kr_loc_pick(t, items):
    """대상 장소 t=[행 id, 위도, 경도, 정규화 이름들, QID] + 위치기반 목록 → (사진 행|None, 원인).
    원인: ok · no_near(200m 안 관광지·문화시설·축제 없음) · name(가까이 있으나 이름 다름) · no_photo(맞았으나 사진 없음) · license(공공누리 1·3유형 아님)."""
    near = [x for x in items if str(x.get('contenttypeid') or '') in KR_LOC_TYPES]
    if not items:
        return None, 'no_items'  # 200m 안에 TourAPI 항목이 하나도 없음
    if not near:
        return None, 'no_near:' + ','.join(sorted({str(x.get('contenttypeid') or '?') for x in items}))  # 있으나 관광지·문화시설·축제 아님(유형 번호)
    mine = set(t[3] or ())
    hit = None
    for x in near:
        ks = kr_keys(x.get('title'))
        if any(a == b or (min(len(a), len(b)) >= 3 and (a in b or b in a)) for a in mine for b in ks):
            hit = x
            break
    if hit is None:
        return None, 'name'
    lic = {'Type1': 'KOGL-1', 'Type3': 'KOGL-3'}.get(str(hit.get('cpyrhtDivCd') or ''))
    if not (hit.get('firstimage') or '').startswith('http'):
        return None, 'no_photo'
    if not lic:
        return None, 'license'
    ph = {'url': hit['firstimage'], 'license': lic, 'credit': '한국관광공사'}
    if (hit.get('firstimage2') or '').startswith('http'):
        ph['thumb'] = hit['firstimage2']
    r = kto_photo_row(ph)
    if not r:
        return None, 'license'
    return dict(r, kto_cid=str(hit.get('contentid') or '')), 'ok'


def kr_loc(targets, rows, lcache, key, opener=None, cap=KR_LOC_MAX, now_day=None, sleep=time.sleep):
    """한국 3도시 관광·명소 대상 중 사진 없는 행마다 위치기반 목록 1회(30일 캐시). 돌려줌 ({행: 사진 행}, {원인: 수}, 물은 수)."""
    d0 = datetime.date.fromisoformat(now_day or today())
    got, why, asked = {}, {}, 0
    for k in KR_CITIES:
        for t in (targets.get(k) or [])[:TOP_N * 2]:
            rid = t[0]
            if rid in rows:
                continue
            c = lcache.get(rid)
            if not (c and (d0 - datetime.date.fromisoformat(c[0])).days < KR_LOC_REFRESH):
                if not key or asked >= cap:
                    why['not_asked'] = why.get('not_asked', 0) + 1
                    continue
                asked += 1
                try:
                    items = [{x: i.get(x) for x in ('contentid', 'contenttypeid', 'title', 'firstimage', 'firstimage2', 'cpyrhtDivCd')} for i in kr_loc_items(kr_loc_get(t[1], t[2], key, opener))]
                except Exception as e:  # noqa: BLE001 — 이유 이름·HTTP 코드만(열쇠가 든 주소를 남기지 않음)
                    code = getattr(e, 'code', None) or (str(e) if isinstance(e, KrApiError) else None)
                    k2 = 'err:' + type(e).__name__ + (f':{code}' if code else '')
                    why[k2] = why.get(k2, 0) + 1
                    if code in (401, 403, 429) or why[k2] >= 5:  # 열쇠·한도 문제면 그날 멈춤(헛호출 없이)
                        why['stop'] = k2
                        return got, why, asked
                    continue
                c = lcache[rid] = [now_day or today(), items]
                sleep(0.5)
            r, w = kr_loc_pick(t, c[1])
            why[w] = why.get(w, 0) + 1
            if r:
                got[rid] = r
    return got, why, asked
GEO_MAX_M = 100.0
GEO_SIM = 0.8
GEO_TIE_M = 25.0


def geo_norm(s):
    return norm_name(s) if isinstance(s, str) else ''


def name_sim(a, b):
    """정규화한 두 이름의 닮음 0~1. 같으면 1, 한쪽이 다른 쪽을 품으면 짧은/긴 길이 비, 그 밖은 0(글자 순서까지 같아야 — 오탐 방지)."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    s, l = (a, b) if len(a) <= len(b) else (b, a)
    return len(s) / len(l) if len(s) >= 3 and s in l else 0.0


def geo_match(tiles, items, bbox, max_m=GEO_MAX_M, sim=GEO_SIM):
    """(270기 2차) 해외 타일 행(QID 없음) ↔ 덤프 항목(좌표 P625 + 라벨·별칭 다국어 + P18). 100m 이내 · 이름 닮음 ≥ 0.8 · 가장 닮고 가까운 것.
    같은 행에 서로 다른 항목이 같은 점수로 겹치면 버린다(오탐 방지). 돌려줌 {타일 행 id: (QID, P18 주소)}."""
    grid = {}
    for g in items or []:
        if not (isinstance(g, list) and len(g) >= 6):
            continue
        q, la, lo, _, p, names = g[:6]
        ns = {geo_norm(n) for n in names} - {''}
        if ns:
            grid.setdefault((int(la * 500), int(lo * 500)), []).append((la, lo, q, p, ns))
    out = {}
    if not grid:
        return out
    for d in tiles:
        ids, la_, lo_ = d.get('id') or [], d.get('_lat') or [], d.get('_lon') or []
        cols = [d[k] for k in ('nm', 'ko', 'en', 'ja', 'zh') if isinstance(d.get(k), list)]
        for i in range(min(len(ids), len(la_), len(lo_))):
            rid, la, lo = ids[i], la_[i], lo_[i]
            if not isinstance(rid, str) or rid in out or QID_IN_ID.search(rid) or not (bbox[0] <= lo <= bbox[2] and bbox[1] <= la <= bbox[3]):
                continue
            mine = {geo_norm(c[i]) for c in cols if i < len(c)} - {''}
            if not mine:
                continue
            gi, gj = int(la * 500), int(lo * 500)
            cand = []
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for gla, glo, q, p, ns in grid.get((gi + di, gj + dj), ()):
                        dm = _dist_m(la, lo, gla, glo)
                        if dm > max_m:
                            continue
                        sc = max(name_sim(a, b) for a in mine for b in ns)
                        if sc >= sim:
                            cand.append((-sc, dm, q, p))
            if not cand:
                continue
            cand.sort()
            b0 = cand[0]
            # 같은 닮음의 다른 항목이 GEO_TIE_M 안쪽 거리 차로 또 있으면 어느 것인지 모름 → 버린다(오탐 방지)
            if any(c[2] != b0[2] and c[0] == b0[0] and c[1] - b0[1] < GEO_TIE_M for c in cand[1:]):
                continue
            out[rid] = (b0[2], b0[3])
    return out


SIGHT_CATS = ('sight', 'nature')
TARGET_SIGHT = 100


def is_sight(d, i):
    cats, c = d.get('cats') or [], d.get('c')
    return isinstance(c, list) and i < len(c) and isinstance(c[i], int) and 0 <= c[i] < len(cats) and cats[c[i]] in SIGHT_CATS


def city_targets(tiles, bbox, want_ids, geo_hits, qnames=None):
    """(270기) 촬영 좌표 사진을 찾을 장소: [행 id, 위도, 경도, 정규화 이름들, QID|None] — 상위 50 + 관광·명소 상위 100.
    QID 를 알면 그 항목의 다국어 라벨·별칭도 이름에 더한다(Commons 파일 이름·설명은 영어·현지어가 많다)."""
    qnames = qnames or {}
    want, out = set(want_ids), {}
    for d in tiles:
        ids, la_, lo_ = d.get('id') or [], d.get('_lat') or [], d.get('_lon') or []
        cols = [d[k] for k in ('nm', 'ko', 'en', 'ja', 'zh') if isinstance(d.get(k), list)]
        for i in range(min(len(ids), len(la_), len(lo_))):
            rid = ids[i]
            if rid in want and rid not in out:
                m = QID_IN_ID.search(rid)
                q = m.group(1) if m else (geo_hits.get(rid) or (None,))[0]
                ns = sorted(({geo_norm(c[i]) for c in cols if i < len(c)} | {geo_norm(n) for n in qnames.get(q) or ()}) - {''})
                out[rid] = [rid, round(la_[i], 6), round(lo_[i], 6), ns, q]
    return [out[r] for r in want_ids if r in out]


def city_tile_ids(tiles, bbox, sight_only=False):
    """(270기) 도시 상자 안 타일 행 id 전부(점수 높은 순, 중복 없음) — QID 가 없는 한국·해외 행도 센다(측정 분모). sight_only = 관광·명소 분류만."""
    rows = []
    for d in tiles:
        ids, la_, lo_ = d.get('id') or [], d.get('_lat') or [], d.get('_lon') or []
        sc = d.get('s') if isinstance(d.get('s'), list) else None
        for i in range(min(len(ids), len(la_), len(lo_))):
            if isinstance(ids[i], str) and bbox[0] <= lo_[i] <= bbox[2] and bbox[1] <= la_[i] <= bbox[3] and (not sight_only or is_sight(d, i)):
                rows.append((-(sc[i] or 0) if sc else 0, ids[i]))
    rows.sort()
    return list(dict.fromkeys(r for _, r in rows))


def tile_cov(city_ids, rows):
    """{도시: [타일 행 수, 사진 있는 행, 상위 50 중 사진]} — 사진 층 행 열쇠(타일 행 id)와 같은 열쇠로 센다."""
    return {k: [len(ids), sum(1 for r in ids if r in rows), sum(1 for r in ids[:TOP_N] if r in rows)] for k, ids in city_ids.items()}


def report_lines(rep, cities):
    L = [f'## 사진 우선 층(photo-pri) {today()}',
         f"- P18 물음 {rep['p18_asked']:,}(남음 {rep['p18_left']:,})" + (f" · 멈춘 까닭 {rep['p18_stop']}" if rep['p18_stop'] else '')
         + f" · Commons 판정 물음 {rep['commons_asked']:,}(남음 {rep['commons_left']:,})" + (f" · 멈춘 까닭 {rep['commons_stop']}" if rep['commons_stop'] else ''),
         f"- 장소 사진 행 {rep['rows']:,} · 도시 대표 사진 {rep['cities']}/{len(cities)}(Unsplash {rep['unsplash']})",
         f"- 도시 사진 단계: {rep.get('city_diag')}",
         f"- 덤프 이름+좌표(100m·닮음 0.8) 맞춤 타일 행 {rep.get('geo_matched', 0):,} · 그중 사진(자유+저작자) {rep.get('geo_rows', 0):,}",
         f"- 한국 TourAPI 사진 붙임(도시: 상자 안 사진 카드 · 붙은 타일 행): {rep.get('kto')}",
         f"- 한국 위치기반 목록(관광지·문화시설·축제, 200m·이름): {rep.get('kr_loc')}",
         f"- 관광·명소 상위 50 중 사진 없는 행의 원인(개수, 앞 10개 표본 · 이름 없음): {rep.get('miss')}",
         f"- 촬영 좌표·묘사 대상(Commons 덤프) 사진 붙인 행 {rep.get('pov_rows', 0):,}",
         '| 순위 | 도시 | 일정 후보(QID) | QID 사진 | 타일 행(전체, QID 없는 행 포함) | 사진 있음 | 비율 | 상위 50 중 사진 | 관광·명소 행 | 관광·명소 사진 비율 | 관광·명소 상위 50 중 사진 |',
         '|---|---|---|---|---|---|---|---|---|---|---|']
    tc, sc = rep.get('tile_cov') or {}, rep.get('sight_cov') or {}
    for c in cities:
        n, k = rep['by_city'].get(c['key'], (0, 0))
        tn, th, t50 = tc.get(c['key'], (0, 0, 0))
        sn, sh, s50 = sc.get(c['key'], (0, 0, 0))
        L.append(f"| {c['rank']} | {c['key']} | {n:,} | {k:,} | {tn:,} | {th:,} | {(100.0 * th / tn if tn else 0):.1f}% | {t50}/{min(TOP_N, tn)} | {sn:,} | "
                 f"{(100.0 * sh / sn if sn else 0):.1f}% | {s50}/{min(TOP_N, sn)} |")
    return L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['build'])
    ap.add_argument('--store', required=True); ap.add_argument('--bucket', default=None); ap.add_argument('--tiles', default=None)
    ap.add_argument('--out', default=None); ap.add_argument('--budget-min', type=float, default=50.0); ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    import yaml
    reg = yaml.safe_load(open(os.path.join(ROOT, 'sources', 'registry.yaml'), encoding='utf-8'))
    src = next((s for s in reg['sources'] if s['id'] == 'photo_pri'), {})
    key = os.environ.get('UNSPLASH_ACCESS_KEY') or None
    if key and any(forbidden(u) for u in (UNSPLASH_API,)):
        key = None
    kr_key = (os.environ.get('DATA_GO_KR_KEY') or '').strip() or None  # (270기) 원인: 비밀 값 끝 줄바꿈 → %0A 로 붙어 403 SERVICE_KEY_IS_NOT_REGISTERED. tourapi_daily 와 같이 strip
    if kr_key and forbidden(KR_LOC_API):
        kr_key = None
    run(Store(a.store), a.bucket, a.tiles, a.out, a.budget_min, key=key, dry_run=a.dry_run, src=src, kr_key=kr_key)


if __name__ == '__main__':
    main()
