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
from common import Blocked, Http, Stop, Store, forbidden, today  # noqa: E402
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
        if not isinstance(rid, str) or not QID.match(rid):
            continue
        la, lo = d['_lat'][i], d['_lon'][i]
        if bbox[0] - pad <= lo <= bbox[2] + pad and bbox[1] - pad <= la <= bbox[3] + pad:
            s = d['s'][i] if isinstance(d.get('s'), list) else 0
            out.append((s or 0, rid))
    return out


def prio_order(cities, tiles_for):
    """cities 순위대로 [(도시 key, [QID…])]. tiles_for(bbox) → 그 상자와 겹치는 타일(load_pack 꼴) 목록."""
    seen, out = set(), []
    for c in cities:
        rows = []
        for d in tiles_for(c['bbox']):
            rows += rows_in_city(d, c['bbox'])
        rows.sort(key=lambda x: (-x[0], int(x[1][1:])))
        qs = []
        for _, q in rows:
            if q not in seen:
                seen.add(q); qs.append(q)
            if len(qs) >= CITY_MAX:
                break
        out.append((c['key'], qs))
    return out


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
        tiles += [t for t in man.get('tiles') or [] if isinstance(t.get('file'), str) and t['file'].startswith('w-') and '/' not in t['file']
                  and isinstance(t.get('bbox'), list) and len(t['bbox']) == 4]
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


def city_rows(cities, cstate, ccache, http_wd, budget_end, key=None, opener=None, clock=time.time, now_day=None):
    """도시마다 행 'city:<key>'. cstate = {key: {at, src, row?, q?, p?}} (processed/photos/cities.json)."""
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
        if st.get('src') != 'unsplash' and not st.get('q') and http_wd is not None and clock() < budget_end:
            url = WD_EP + '?' + urllib.parse.urlencode({'query': city_sparql(city_en(k))})
            try:
                hit = pick_city_item(json.loads(http_wd.get(url, accept='application/sparql-results+json')), c['bbox'])
            except (Blocked, Stop, IOError, ValueError):
                hit = None
            if hit:
                st.update(q=hit[0], p=hit[1], at=today(), src='commons')
        cstate[k] = st
        if st.get('src') == 'unsplash' and st.get('row'):
            rows[f'city:{k}'] = dict(st['row'], city=k)
        elif st.get('p'):
            cc = ccache.get(st['p'])
            v = PN.photo_row(st['p'], cc[1]) if cc else None
            if v:
                rows[f'city:{k}'] = dict(v, city=k, q=st.get('q'))
            elif not cc:
                files.append(st['p'])
    return rows, files


# ---------- 4. 실행 ----------
def judge_files(prio, cache, pcache):
    """우선 순서대로 Commons 파일 주소(중복 없이)."""
    seen, out = set(), []
    for _, qs in prio:
        for q in qs:
            _, u = p18_known(q, cache, pcache)
            if u and u.startswith(PN.FILEPATH) and u not in seen:
                seen.add(u); out.append(u)
    return out


def pri_rows(prio, cache, pcache, ccache):
    rows, by_city = {}, {}
    for k, qs in prio:
        n = 0
        for q in qs:
            _, u = p18_known(q, cache, pcache)
            cc = ccache.get(u) if u else None
            v = PN.photo_row(u, cc[1]) if cc else None
            if v:
                rows[q] = dict(v, q=q); n += 1
        by_city[k] = (len(qs), n)
    return rows, by_city


def pri_doc(rows, part):
    return {'schema': 'photos-1', 'region': 'priority', 'part': part, 'license': 'mixed — 행마다 photo_license(CC0·CC BY·CC BY-SA)',
            'attribution': '사진: 위키미디어 공용(사진마다 저작자·라이선스) · 사진 고르기: Wikidata P18 (CC0)', 'built': today(), 'rows': rows}


def city_doc(rows):
    return {'schema': 'photos-1', 'region': 'cities', 'license': 'mixed — 행마다 photo_license(Unsplash License · CC0 · CC BY · CC BY-SA)',
            'attribution': '도시 사진: Unsplash(작가·Unsplash 표기, 핫링크) 또는 위키미디어 공용(저작자·라이선스)', 'built': today(), 'rows': rows}


def run(store, bucket=None, tiles_dir=None, out=None, budget_min=50.0, http_wd=None, http_c=None, key=None, opener=None, clock=time.time, dry_run=False, src=None):
    t_end = clock() + budget_min * 60
    src = src or {}
    cities = load_cities()
    wd = tempfile.mkdtemp(dir=os.environ.get('RUNNER_TEMP') or None)
    try:
        ver, tiles_for = local_tiles_for(tiles_dir) if tiles_dir else gcs_tiles_for(bucket, wd)
        pkey = f'processed/photos/prio_{ver}.json.gz'
        cached = store.get_json(pkey, None) if ver != 'local' else None
        prio = [tuple(x) for x in cached] if cached else prio_order(cities, tiles_for)
        if not cached and ver and ver != 'local':
            store.put_json(pkey, prio)
    finally:
        shutil.rmtree(wd, ignore_errors=True)
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
    crow, cfiles = city_rows(cities, cstate, ccache, http_wd, t_end - budget_min * 60 * 0.4, key=key, opener=opener, clock=clock)
    todo = [u for u in cfiles + judge_files(prio, cache, pcache) if u not in ccache]
    todo = list(dict.fromkeys(todo))
    asked, stop = PN.fetch_commons(http_c, todo, mine, t_end, clock) if todo else (0, None)
    ccache.update({u: v for u, v in mine.items() if u in todo})
    store.put_json('processed/photos/commons.json.gz', mine)
    crow, _ = city_rows(cities, cstate, ccache, None, 0, key=None, clock=clock)  # 판정 뒤 다시(요청 없음)
    store.put_json('processed/photos/cities.json', cstate)
    rows, by_city = pri_rows(prio, cache, pcache, ccache)
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
           'by_city': by_city}
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


def report_lines(rep, cities):
    L = [f'## 사진 우선 층(photo-pri) {today()}',
         f"- P18 물음 {rep['p18_asked']:,}(남음 {rep['p18_left']:,})" + (f" · 멈춘 까닭 {rep['p18_stop']}" if rep['p18_stop'] else '')
         + f" · Commons 판정 물음 {rep['commons_asked']:,}(남음 {rep['commons_left']:,})" + (f" · 멈춘 까닭 {rep['commons_stop']}" if rep['commons_stop'] else ''),
         f"- 장소 사진 행 {rep['rows']:,} · 도시 대표 사진 {rep['cities']}/{len(cities)}(Unsplash {rep['unsplash']})",
         '| 순위 | 도시 | 일정 후보(QID) | 사진 있음 | 비율 |', '|---|---|---|---|---|']
    for c in cities:
        n, k = rep['by_city'].get(c['key'], (0, 0))
        L.append(f"| {c['rank']} | {c['key']} | {n:,} | {k:,} | {(100.0 * k / n if n else 0):.1f}% |")
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
    run(Store(a.store), a.bucket, a.tiles, a.out, a.budget_min, key=key, dry_run=a.dry_run, src=src)


if __name__ == '__main__':
    main()
