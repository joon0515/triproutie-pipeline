#!/usr/bin/env python3
"""장소 이름 5개 언어 층(names-2)·사진 층(photos-1, Commons) — place-desc 단계와 같은 Wikidata 조회를 함께 쓴다. (2026-11-18, 223기)

place_desc.py(218기)가 QID 를 묻는 SPARQL 한 번에 설명·라벨·P18(대표 사진 파일)을 함께 받아 캐시(processed/desc/wd.json.gz)에 둔다.
  캐시 한 줄 = [받은 날, {설명 칸}, {'l': {라벨 칸}, 'p': 'Commons 파일 주소'}]. 이 스크립트는 그 캐시로 층을 만든다(Wikidata 를 따로 묻지 않는다).

만드는 파일(출처마다 따로 — 라이선스를 섞지 않는다):
  names-wd-NN.json.gz   Wikidata 라벨(CC0). schema names-2, 행 {장소 id: {ko, en, ja, zh_Hans, zh_Hant: 이름|null, q}}
  photos-wd-NN.json.gz  Wikidata P18 → Wikimedia Commons 파일. Commons 구조화 데이터(SDC)의 라이선스(P275)·저작자(P170 의 P2093/P4174)를
                        QLever wikimedia-commons 끝점에서 받아, CC0·CC BY·CC BY-SA(국제판·Generic·Unported 판 번호 목록)만 싣는다.
                        저작자 이름이 없거나 라이선스가 목록 밖이면 싣지 않는다(애매하면 넣지 않음). 사진은 링크만 — 파일을 받거나 복사하지 않는다.
  names-osm-<지역>      OSM name:xx 태그(ODbL) — places-layers 의 tools/names5_osm.py 가 따로 만든다(이 스크립트는 만들지 않음).
언어 칸: ko · en · ja · zh_Hans · zh_Hant. 없는 언어는 null — 다른 언어 이름을 넣지 않고, 간체↔번체 변환·음역·번역을 하지 않는다(앱이 원어 이름을 보여 준다).
  zh_Hans ← zh-hans · zh-cn · zh-sg · zh-my, zh_Hant ← zh-hant · zh-tw · zh-hk · zh-mo (place_desc.WD_TAGS 와 같음).
  꼬리표 없는 zh 는 글자로 갈래가 보일 때만: 간체 글자만 → zh_Hans, 번체 글자만 → zh_Hant, 갈리는 글자가 없거나 둘 다 있으면 싣지 않는다
  (이름은 소개 글보다 짧아 갈래를 못 가리는 경우가 많다 — 모르면 비운다).
받는 길·robots: www.wikidata.org/w/api.php · commons.wikimedia.org/w/api.php 는 robots.txt 가 User-agent * 에 Disallow /w/ → 쓰지 않는다.
  qlever.dev(robots.txt 없음 = 허용) 의 wikidata·wikimedia-commons 끝점만. Commons 는 파일 40개씩, 15초 간격, 하루 photo_daily_limit 회.
  캐시 processed/desc/commons.json.gz {파일 주소: [받은 날, {lic, by, user?} | {}]} — 90일 지난 것만 다시 묻는다.
장소 내용은 로그에 쓰지 않는다(개수만).
"""
import datetime, glob, gzip, hashlib, json, os, re, sys, time, unicodedata, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, 'collect'))
import place_desc as PD  # noqa: E402
from common import Blocked, Http, Stop, bad_text, refresh_days, today  # noqa: E402

SLOTS = PD.SLOTS
NAME_MAX = 120
NSH = 8
FILE_MAX = 7 * 1024 * 1024
C_BATCH = 40          # Commons 끝점 VALUES 하나에 넣는 파일 수(주소 약 5KB — 100개는 414, 11/18 실측 40개 12초)
C_REFRESH = refresh_days('photos', 90)  # Commons 사진 정보 갱신 주기(sources/refresh_policy.json)
COMMONS_EP = 'https://qlever.dev/api/wikimedia-commons'
FILEPATH = 'http://commons.wikimedia.org/wiki/Special:FilePath/'
# 라이선스 Wikidata 항목 → (표기, 라이선스 주소). 이 목록 밖(나라별 판·GFDL·PD 표기·NC/ND 등)은 싣지 않는다.
LICENSES = {
    'Q6938433': ('CC0 1.0', 'https://creativecommons.org/publicdomain/zero/1.0/'),
    'Q30942811': ('CC BY 1.0', 'https://creativecommons.org/licenses/by/1.0/'),
    'Q19125117': ('CC BY 2.0', 'https://creativecommons.org/licenses/by/2.0/'),
    'Q18810333': ('CC BY 2.5', 'https://creativecommons.org/licenses/by/2.5/'),
    'Q14947546': ('CC BY 3.0', 'https://creativecommons.org/licenses/by/3.0/'),
    'Q20007257': ('CC BY 4.0', 'https://creativecommons.org/licenses/by/4.0/'),
    'Q47001652': ('CC BY-SA 1.0', 'https://creativecommons.org/licenses/by-sa/1.0/'),
    'Q19068220': ('CC BY-SA 2.0', 'https://creativecommons.org/licenses/by-sa/2.0/'),
    'Q19113751': ('CC BY-SA 2.5', 'https://creativecommons.org/licenses/by-sa/2.5/'),
    'Q14946043': ('CC BY-SA 3.0', 'https://creativecommons.org/licenses/by-sa/3.0/'),
    'Q18199165': ('CC BY-SA 4.0', 'https://creativecommons.org/licenses/by-sa/4.0/'),
}
LIC_ORDER = list(LICENSES)  # 여러 라이선스 중 고를 때 앞(덜 제한적인 것)이 먼저
KANA = re.compile(r'[぀-ヿ]')
HANGUL = re.compile(r'[가-힣]')


def log(msg):
    print(f'[place-names] {msg}', flush=True)


# ---------- 이름 칸 ----------
def name_ok(v):
    """이름으로 실어도 되면 정리한 값(공백·NFC 만), 아니면 None. 여러 값(';')·주소·깨진 글은 버린다."""
    if not isinstance(v, str):
        return None
    s = re.sub(r'\s+', ' ', unicodedata.normalize('NFC', v)).strip()
    if not s or len(s) > NAME_MAX or ';' in s or 'http' in s.lower() or bad_text(s):
        return None
    return s


def zh_generic(v):
    """꼬리표 없는 zh 이름 → 'zh_Hans' | 'zh_Hant' | None(갈래를 못 가림·가나 섞임)."""
    if not v or KANA.search(v) or HANGUL.search(v):
        return None
    sc = PD.han_script(v)
    return 'zh_Hans' if sc == 'Hans' else 'zh_Hant' if sc == 'Hant' else None


def slots_from_tags(tagged, tag_map, generic=None):
    """{원천 꼬리표: 이름} → {칸: 이름|None}. tag_map = {칸: (꼬리표, …)} — 앞 꼬리표가 먼저. generic = 꼬리표 없는 zh 값."""
    out = {k: None for k in SLOTS}
    for slot, tags in tag_map.items():
        for t in tags:
            v = name_ok(tagged.get(t))
            if not v:
                continue
            if slot == 'zh_Hans' and PD.han_script(v) == 'Hant' or slot == 'zh_Hant' and PD.han_script(v) == 'Hans':
                continue  # 꼬리표와 글자가 어긋나면 싣지 않는다
            if slot in ('zh_Hans', 'zh_Hant') and (KANA.search(v) or HANGUL.search(v)):
                continue
            out[slot] = v
            break
    g = name_ok(generic)
    k = zh_generic(g)
    if k and not out[k]:
        out[k] = g
    return out


def wd_label_slots(labels):
    """Wikidata 라벨 {언어 꼬리표: 글} → 5개 칸."""
    lab = {k.lower(): v for k, v in (labels or {}).items()}
    return slots_from_tags(lab, PD.WD_TAGS, lab.get('zh'))


# ---------- 층 파일 ----------
def names_doc(src, rows, extra=None):
    lic = {'wikidata': ('CC0-1.0', 'Wikidata (CC0)', 'https://www.wikidata.org/wiki/{q}'),
           'osm': ('ODbL-1.0', '© OpenStreetMap contributors (ODbL 1.0)', 'https://www.openstreetmap.org/{osm}')}[src]
    cnt = {k: sum(1 for v in rows.values() if v.get(k)) for k in SLOTS}
    return {'schema': 'names-2', 'dataset': 'names', 'src': src, 'license': lic[0], 'credit': lic[1], 'page': lic[2], 'built': today(),
            'langs': cnt, **(extra or {}), 'rows': rows}


def write_sharded(out, prefix, rows, make_doc, n=NSH):
    """rows 를 id 해시로 n 조각 → <prefix>-NN.json.gz. 파일 하나가 FILE_MAX 를 넘으면 조각 수를 두 배로. 돌려줌 = {파일: 행 수}."""
    while True:
        parts = [{} for _ in range(n)]
        for rid in sorted(rows):
            parts[PD.shard_of(rid, n)][rid] = rows[rid]
        sizes = {}
        for k, p in enumerate(parts):
            if p:
                fn = f'{prefix}-{k:02d}.json.gz' if n < 100 else f'{prefix}-{k:03d}.json.gz'
                sizes[fn] = (PD.write_layer(os.path.join(out, fn), make_doc(p, [k, n])), len(p))
        if not sizes or max(s for s, _ in sizes.values()) <= FILE_MAX or n >= 1024:
            return {f: c for f, (_, c) in sizes.items()}
        for f in sizes:
            os.remove(os.path.join(out, f))
        n *= 2


def names_rows(seeds, cache):
    """씨앗(QID → 장소 id)·캐시 라벨 → {장소 id: {칸…, q}}. 라벨이 없는 QID 는 건너뛴다."""
    rows = {}
    for q, ids in seeds.q.items():
        c = cache.get(q)
        if not c or len(c) < 3 or not isinstance(c[2], dict):
            continue
        sl = {k: c[2].get('l', {}).get(k) for k in SLOTS}
        if not any(sl.values()):
            continue
        for rid in ids:
            rows[rid] = dict(sl, q=q)
    return rows


# ---------- 사진(Commons SDC via QLever) ----------
def commons_sparql(urls):
    vals = ' '.join('<' + u + '>' for u in urls)
    return ('PREFIX schema: <http://schema.org/> PREFIX wdt: <http://www.wikidata.org/prop/direct/> PREFIX p: <http://www.wikidata.org/prop/> '
            'PREFIX pq: <http://www.wikidata.org/prop/qualifier/> SELECT ?u ?lic ?an ?un WHERE { VALUES ?u { ' + vals + ' } '
            '?f schema:url ?u . ?f wdt:P275 ?lic . OPTIONAL { ?f p:P170 ?s . OPTIONAL { ?s pq:P2093 ?an } OPTIONAL { ?s pq:P4174 ?un } } }')


def parse_commons(j):
    """SPARQL JSON → {파일 주소: {'lic': [QID…], 'an': {…}, 'un': {…}}}."""
    out = {}
    for b in ((j or {}).get('results') or {}).get('bindings') or []:
        try:
            u, lic = b['u']['value'], b['lic']['value'].rsplit('/', 1)[1]
        except (KeyError, IndexError):
            continue
        e = out.setdefault(u, {'lic': set(), 'an': set(), 'un': set()})
        e['lic'].add(lic)
        for k in ('an', 'un'):
            if k in b and b[k].get('value'):
                e[k].add(b[k]['value'].strip())
    return out


def judge(e):
    """한 파일의 SDC → {lic, by, user?} | {} (싣지 않음). 저작자가 둘 이상이면 모두 이어 적는다. 저작자가 없으면 싣지 않는다."""
    ok = [q for q in LIC_ORDER if q in e.get('lic', ())]
    if not ok:
        return {}
    by = sorted(x for x in e.get('an', ()) if x and len(x) <= 120)
    user = sorted(x for x in e.get('un', ()) if x and len(x) <= 80)
    if not by and not user:
        return {}
    v = {'lic': ok[0], 'by': ', '.join(by or user)[:200]}
    if user and by:
        v['user'] = user[0]
    return v


def fetch_commons(http, files, cache, budget_end, clock=time.time):
    done = 0
    for i in range(0, len(files), C_BATCH):
        if clock() > budget_end:
            return done, 'TIME'
        part = files[i:i + C_BATCH]
        url = COMMONS_EP + '?' + urllib.parse.urlencode({'query': commons_sparql(part)})
        try:
            body = http.get(url, accept='application/sparql-results+json')
        except Stop as e:
            return done, str(e)
        except Blocked as e:
            return done, f'BLOCKED {e}'
        except IOError:
            return done, 'FETCH'
        try:
            got = parse_commons(json.loads(body))
        except ValueError:
            return done, 'PARSE'
        d = today()
        for u in part:
            cache[u] = [d, judge(got.get(u) or {})]
        done += len(part)
    return done, None


def file_name(u):
    return urllib.parse.unquote(u[len(FILEPATH):]) if u.startswith(FILEPATH) else None


def photo_row(u, c):
    """Commons 판정 → photos-1 행(링크만). 앱 표시: photo_credit 그대로 + photo_page 링크."""
    name = file_name(u)
    if not name or not c or c.get('lic') not in LICENSES:
        return None
    lic, lic_url = LICENSES[c['lic']]
    enc = urllib.parse.quote(name.replace(' ', '_'))
    return {'photo_url': f'https://commons.wikimedia.org/wiki/Special:FilePath/{enc}', 'photo_thumb': f'https://commons.wikimedia.org/wiki/Special:FilePath/{enc}?width=640',
            'photo_credit': f"사진: {c['by']} · {lic} (위키미디어 공용)", 'photo_license': lic, 'photo_license_url': lic_url, 'photo_author': c['by'],
            'photo_page': f'https://commons.wikimedia.org/wiki/File:{enc}', 'photo_src': 'commons', 'photo_file': name}


def photos_rows(seeds, cache, ccache):
    rows = {}
    for q, ids in seeds.q.items():
        c = cache.get(q)
        u = c[2].get('p') if c and len(c) > 2 and isinstance(c[2], dict) else None
        cc = ccache.get(u) if u else None
        v = photo_row(u, cc[1]) if cc else None
        if v:
            for rid in ids:
                rows[rid] = dict(v, q=q)
    return rows


def photos_doc(rows, part):
    return {'schema': 'photos-1', 'region': 'wikidata', 'part': part, 'license': 'mixed — 행마다 photo_license(CC0·CC BY·CC BY-SA)',
            'attribution': '사진: 위키미디어 공용(사진마다 저작자·라이선스) · 사진 고르기: Wikidata P18 (CC0)', 'built': today(), 'rows': rows}


def order_files(seeds, cache, ccache, now_day=None):
    """물어볼 Commons 파일: 안 물은 것(앱 타일·OSM 쪽 QID 먼저) → 90일 지난 것."""
    d0 = datetime.date.fromisoformat(now_day or today())
    want = {}
    for q in seeds.q:
        c = cache.get(q)
        u = c[2].get('p') if c and len(c) > 2 and isinstance(c[2], dict) else None
        if u and u.startswith(FILEPATH):
            want[u] = min(want.get(u, 9), 0 if q in seeds.prio else 1)
    new = sorted((u for u in want if u not in ccache), key=lambda u: (want[u], u))
    old = sorted((u for u in want if u in ccache and (d0 - datetime.date.fromisoformat(ccache[u][0])).days >= C_REFRESH), key=lambda u: ccache[u][0])
    return new + old


def build(store, seeds, cache, out, src, budget_end, clock=time.time, http=None):
    """place_desc.run 이 설명 층을 만든 뒤 부른다. 돌려줌 = (보고 dict, 만든 파일 목록)."""
    made = {}
    nrows = names_rows(seeds, cache)
    for f in glob.glob(os.path.join(out, 'names-wd-*.json.gz')) + glob.glob(os.path.join(out, 'photos-wd-*.json.gz')):
        os.remove(f)
    if nrows:
        made.update(write_sharded(out, 'names-wd', nrows, lambda p, part: names_doc('wikidata', p, {'part': part})))
    ccache = store.get_json('processed/desc/commons.json.gz', {}) or {}
    st = store.get_json('processed/desc/commons_state.json', {}) or {}
    todo = order_files(seeds, cache, ccache)
    asked, stop = 0, None
    if todo and clock() < budget_end:
        if http is None:
            lim = {'daily_limit': src.get('photo_daily_limit', 1500), 'interval_s': src.get('photo_interval_s', 15), 'timeout_s': 180}
            http = Http(lim, st.setdefault('http', {}), st.setdefault('hosts', {}), deadline=budget_end)
        asked, stop = fetch_commons(http, todo, ccache, budget_end, clock)
        store.put_json('processed/desc/commons.json.gz', ccache)
    prows = photos_rows(seeds, cache, ccache)
    if prows:
        made.update(write_sharded(out, 'photos-wd', prows, photos_doc, n=1))
    langs = {k: sum(1 for v in nrows.values() if v.get(k)) for k in SLOTS}
    rep = {'names_rows': len(nrows), 'names_langs': langs, 'photo_files_asked': asked, 'photo_files_left': len(todo) - asked, 'photo_stop': stop,
           'photo_rows': len(prows), 'commons_cached': len(ccache), 'commons_ok': sum(1 for v in ccache.values() if v[1])}
    st['last'] = dict(rep, at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    store.put_json('processed/desc/commons_state.json', st)
    log(f"이름 층 행 {len(nrows):,} · 사진 층 행 {len(prows):,} · Commons 물음 {asked:,}(남음 {len(todo) - asked:,})" + (f' · 멈춘 까닭 {stop}' if stop else ''))
    return rep, sorted(made)


def report_lines(rep):
    L = ['### 이름 5개 언어(names-wd, Wikidata 라벨 CC0)', '| 행 | ' + ' | '.join(SLOTS) + ' |', '|---' * (len(SLOTS) + 1) + '|',
         f"| {rep['names_rows']:,} | " + ' | '.join(f"{rep['names_langs'][k]:,}" for k in SLOTS) + ' |',
         f"- 사진(photos-wd, Commons CC0·CC BY·CC BY-SA, 저작자 표기 있는 것만): 행 {rep['photo_rows']:,} · 이번에 물은 파일 {rep['photo_files_asked']:,}"
         f" · 남은 파일 {rep['photo_files_left']:,} · 판정 캐시 {rep['commons_cached']:,}(실을 수 있음 {rep['commons_ok']:,})" + (f" · 멈춘 까닭 {rep['photo_stop']}" if rep['photo_stop'] else '')]
    return L
