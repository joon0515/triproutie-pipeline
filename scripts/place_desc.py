#!/usr/bin/env python3
"""장소 소개 층(desc-1) — 일정의 '왜 여기?' 첫 조각에 쓸 '무엇을 보는 곳인지' 한 줄을 5개 언어 칸에 따로 싣는다. (2026-11-17, 218기)

  python3 scripts/place_desc.py build --store gs://<b>/collect/v1 --bucket <b> [--budget-min 45] [--dry-run]
  python3 scripts/place_desc.py build --store <로컬 폴더> --extra <로컬 layers_extra 폴더> --out <폴더>          # 시험

process 흐름: partition → countries → merge → **place-desc** → publish. 만든 파일은 places/v1/layers_extra/desc-*.json.gz 에 올리고,
  바로 뒤 publish 가 layers_extra 를 함께 실어 앱 목록에 넣는다(publish_layers.py kind 'desc').

출처(sources/registry.yaml 에 적힌 것만, 출처마다 파일을 따로 — 라이선스를 섞지 않는다):
  desc-wd-NN.json.gz  Wikidata 설명(CC0). 받는 길 = registry wikidata_official 의 QLever 공개 SPARQL(qlever.dev, robots.txt 없음 = 허용).
                      www.wikidata.org/w/api.php·query.wikidata.org/sparql 은 robots.txt 가 User-agent * 에 Disallow(/w/, /sparql) → 쓰지 않는다.
                      QID 씨앗 = 수집 층(col_*)의 wd 칸 · 세계 타일 id 에 든 Q-id · 영업시간 원본 층(hours_src)의 q(OSM wikidata 태그). 이름 검색은 쓰지 않는다.
  desc-kto.json.gz    한국관광공사 TourAPI 4.0 overview 첫 문장(tourapi_daily.py 가 공공누리 제1유형만 받아 kto_*.json.gz 의 ov 에 둔 것). ko 칸만.
  desc-tw.json.gz     대만 관광서 Attraction 소개 첫 문장(collectors.parse_taiwan 이 dsc 로 넣어 수집 층에 실린 것). zh_Hant 칸만.
  desc-osm-<지역>     OSM description:<lang> 태그(ODbL) — places-layers 의 tools/desc_osm.py 가 따로 만든다(이 스크립트는 만들지 않음).
쓰지 않는 것: 위키백과 본문(CC BY-SA — 판정은 docs/LAYERS.md '장소 소개'), 금지 도메인, 기계 번역·요약·생성 문장.
언어 칸: ko · en · ja · zh_Hans · zh_Hant. 없는 언어는 null(비움) — 다른 언어로 채우지 않고 간체↔번체 변환도 하지 않는다.
  zh_Hans ← zh-hans · zh-cn · zh-sg · zh-my, zh_Hant ← zh-hant · zh-tw · zh-hk · zh-mo. 꼬리표 없는 zh 는 글자로 간체·번체가 갈릴 때만 그쪽 칸
  (간체 글자만 있거나 어느 쪽 글자도 없으면 zh_Hans, 번체 글자만 있으면 zh_Hant, 둘 다 있으면 버림).
거르기: 4자 미만 · 장소 이름이나 Wikidata 라벨과 같음 · 위키미디어 동음이의/분류 문서 · 글자 깨짐 · 주소(http) · GLOSSARY 금지 낱말(추정·어림·대략·estimated 등).
  원문 문장은 고치지 않는다(앞뒤 공백·줄바꿈·HTML 표시만 정리). 긴 문단은 첫 문장만, 첫 문장이 120자를 넘으면 싣지 않는다(중간에서 자르지 않음).
캐시: Storage processed/desc/wd.json.gz {QID: [받은 날, {칸: 글}]} — 30일 지난 것만 다시 묻는다. 시간 예산이 끝나면 다음 실행이 이어서 묻는다.
장소 내용은 로그에 쓰지 않는다(개수만).
"""
import argparse, datetime, glob, gzip, hashlib, html, json, os, re, shutil, subprocess, sys, tempfile, time, unicodedata, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, os.path.join(HERE, 'collect'))
from common import Blocked, Http, Stop, Store, bad_text, norm_name, refresh_days, today  # noqa: E402

SLOTS = ('ko', 'en', 'ja', 'zh_Hans', 'zh_Hant')
WD_TAGS = {'ko': ('ko',), 'en': ('en',), 'ja': ('ja',), 'zh_Hans': ('zh-hans', 'zh-cn', 'zh-sg', 'zh-my'), 'zh_Hant': ('zh-hant', 'zh-tw', 'zh-hk', 'zh-mo')}
ALL_TAGS = tuple(t for ts in WD_TAGS.values() for t in ts) + ('zh',)
QID = re.compile(r'(?:^|[:/])(Q[1-9]\d{0,9})$')
BATCH = 250          # SPARQL VALUES 하나에 넣는 QID 수(주소 길이 약 4KB, qlever 응답 약 11초 — 11/17 실측)
REFRESH_DAYS = refresh_days('desc', 30)  # 설명·라벨 캐시 갱신 주기(sources/refresh_policy.json)
MIN_LEN = 4
MAX_SENT = 120
NSH = 32             # desc-wd 파일 수(id 해시로 나눔 — 파일 하나가 앱 상한 8MB 를 넘지 않게. 넘으면 두 배로)
FILE_MAX = 7 * 1024 * 1024
DISAMBIG = 'Q4167410'
CREDIT = {'wikidata': 'Wikidata (CC0)', 'tourapi': '한국관광공사 TourAPI 4.0 (공공누리 제1유형, 출처표시)',
          'taiwan_tourism': '交通部觀光署 觀光資訊資料庫 (政府資料開放授權條款-第1版)', 'osm': '© OpenStreetMap contributors (ODbL 1.0)'}
LICENSE = {'wikidata': 'CC0-1.0', 'tourapi': 'KOGL-1', 'taiwan_tourism': 'OGDL-Taiwan-1.0', 'osm': 'ODbL-1.0'}
PAGE = {'wikidata': 'https://www.wikidata.org/wiki/{q}', 'tourapi': None, 'taiwan_tourism': 'https://data.gov.tw/dataset/7777', 'osm': 'https://www.openstreetmap.org/{osm}'}
# GLOSSARY.md(i18n_review): 추정·어림·대략과 같은 뜻의 말은 앱에 쓰지 않는다 → 그런 말이 든 원문은 싣지 않는다(고쳐 쓰지 않으므로).
BANNED_WORDS = re.compile(r'추정|어림|대략|\bestimated?\b|\bapproximately\b|\babout \d|推定|目安|估算|估計|估计|大约|大約|[約约]\s?\d', re.I)
META = re.compile(r'^(wikimedia|wikipedia|위키미디어|위키백과|ウィキメディア|ウィキペディア|维基媒体|維基媒體|维基百科|維基百科)', re.I)
# 간체·번체가 다른 흔한 글자 짝(앞 = 간체, 뒤 = 번체). 글자로 갈래만 본다 — 바꾸지는 않는다.
_PAIRS = ('区區 们們 国國 馆館 园園 东東 门門 时時 长長 广廣 场場 车車 铁鐵 楼樓 桥橋 历歷 岛島 湾灣 县縣 乡鄉 镇鎮 义義 乐樂 书書 说說 语語 读讀 '
          '设設 计計 边邊 华華 业業 丽麗 汉漢 龙龍 凤鳳 会會 开開 关關 发發 经經 济濟 热熱 带帶 级級 现現 电電 问問 间間 页頁 应應 运運 进進 远遠 '
          '这這 还還 对對 观觀 寿壽 归歸 际際 饭飯 饮飲 宫宮 纪紀 线線 贸貿 览覽 旧舊 总總 处處 务務 艺藝 术術 剧劇 厅廳 宝寶')
SIMP = {p[0] for p in _PAIRS.split() if p[0] != p[1]}
TRAD = {p[1] for p in _PAIRS.split() if p[0] != p[1]}
# (11/18 252기) 위 77짝만으로는 꼬리표 없는 zh 이름 대부분이 'neutral' 로 남아 zh_Hans 채움이 0 이었다(예: 서울 Overture zh 99개 전부 neutral).
#  Unicode Unihan 변형 표에서 뽑은 간체 전용 2,353자·번체 전용 5,372자를 더한다(sources/zh_script_chars.json — 갈래만 보고 바꾸지 않음).
try:
    _ZS = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'sources', 'zh_script_chars.json'), encoding='utf-8'))
    SIMP |= set(_ZS.get('hans_only', '')); TRAD |= set(_ZS.get('hant_only', ''))
except (OSError, ValueError):
    pass
KANA = re.compile(r'[぀-ヿ]')


def _prio_cities():
    try:
        j = json.load(open(os.path.join(ROOT, 'sources', 'priority_cities.json'), encoding='utf-8'))
        return sorted((c for c in j['cities'] if isinstance(c.get('bbox'), list) and len(c['bbox']) == 4), key=lambda c: c['rank'])
    except (OSError, ValueError, KeyError):
        return []


def log(msg):
    print(f'[place-desc] {msg}', flush=True)


def _pn():
    """place_names.py(이름 5개 언어·사진 층, 223기) — 서로 불러 쓰므로 쓸 때 들여온다."""
    import place_names
    return place_names


def han_script(s):
    """'Hans' | 'Hant' | 'neutral'(갈리는 글자 없음) | None(둘 다 있음)."""
    a, b = any(c in SIMP for c in s), any(c in TRAD for c in s)
    return None if a and b else 'Hans' if a else 'Hant' if b else 'neutral'


def clean(s):
    """원문을 고치지 않고 표시만 정리: HTML 꼬리표·문자 참조, 줄바꿈·겹 공백."""
    if not isinstance(s, str):
        return ''
    s = re.sub(r'<br\s*/?>', ' ', s, flags=re.I)
    s = html.unescape(re.sub(r'<[^>]+>', '', s))
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFC', s)).strip()


_SENT = re.compile(r'^(.+?(?:[。！？]|[.!?](?=\s|$)))')


def first_sentence(s, limit=MAX_SENT):
    """긴 소개 문단 → 첫 문장(원문 그대로). 첫 문장이 limit 자를 넘으면 None — 중간에서 자르지 않는다."""
    s = clean(s)
    if not s:
        return None
    m = _SENT.match(s)
    f = m.group(1).strip() if m else s
    return f if len(f) <= limit else None


def ok_text(s, names=()):
    """앱에 실어도 되는 글이면 정리한 글, 아니면 None."""
    s = clean(s)
    if len(s) < MIN_LEN or bad_text(s) or META.match(s) or 'http' in s.lower() or BANNED_WORDS.search(s):
        return None
    n = norm_name(s)
    if not n or any(n == norm_name(x) for x in names if x):
        return None
    return s


def pick_slots(desc, labels=None):
    """Wikidata 언어 꼬리표별 설명 {tag: 글} → 5개 칸 {칸: 글 | None}. labels(같은 모양)와 같으면 버린다."""
    labels = {k.lower(): v for k, v in (labels or {}).items()}
    desc = {k.lower(): v for k, v in (desc or {}).items()}
    out = {k: None for k in SLOTS}
    lab_all = list(labels.values())
    for slot, tags in WD_TAGS.items():
        for t in tags:
            v = ok_text(desc.get(t), lab_all)
            if v and (slot == 'zh_Hans' and han_script(v) == 'Hant' or slot == 'zh_Hant' and han_script(v) == 'Hans'):
                v = None  # 꼬리표와 글자가 어긋나는 값은 싣지 않는다
            if v:
                out[slot] = v
                break
    g = ok_text(desc.get('zh'), lab_all)
    if g and not KANA.search(g):
        sc = han_script(g)
        if sc in ('Hans', 'neutral') and not out['zh_Hans']:
            out['zh_Hans'] = g
        elif sc == 'Hant' and not out['zh_Hant']:
            out['zh_Hant'] = g
    return out


def name_filter(slots, names):
    return {k: (v if v and ok_text(v, names) else None) for k, v in slots.items()}


# ---------- Wikidata(QLever) ----------
def sparql(qids):
    vals = ' '.join('wd:' + q for q in qids)
    langs = ','.join(f'"{t}"' for t in ALL_TAGS)
    return ('PREFIX wd: <http://www.wikidata.org/entity/> PREFIX wdt: <http://www.wikidata.org/prop/direct/> PREFIX schema: <http://schema.org/> '
            'PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> SELECT ?item ?k ?v WHERE { VALUES ?item { ' + vals + ' } '
            f'{{ ?item schema:description ?v BIND("d" AS ?k) FILTER(LANG(?v) IN ({langs})) }} UNION {{ ?item rdfs:label ?v BIND("l" AS ?k) FILTER(LANG(?v) IN ({langs})) }} '
            '''UNION { ?item wdt:P18 ?v BIND("p" AS ?k) } '''  # (11/18, 223기) 대표 사진 파일 — place_names.py 의 사진 층(Commons 라이선스 확인 뒤)
            f'FILTER NOT EXISTS {{ ?item wdt:P31 wd:{DISAMBIG} }} }}')


def parse_bindings(j):
    """SPARQL JSON → {QID: {'d': {tag: 글}, 'l': {tag: 글}}}."""
    out = {}
    for b in ((j or {}).get('results') or {}).get('bindings') or []:
        try:
            q = b['item']['value'].rsplit('/', 1)[1]
            k, v, t = b['k']['value'], b['v']['value'], b['v'].get('xml:lang', '').lower()
        except (KeyError, IndexError):
            continue
        out.setdefault(q, {'d': {}, 'l': {}, 'p': {}}).setdefault(k, {})[t] = v
    return out


def fetch_wd(http, endpoint, qids, cache, budget_end, clock=time.time):
    """qids 를 BATCH 개씩 물어 cache 에 [오늘, 칸] 으로 넣는다. 돌려줌 = (물은 QID 수, 멈춘 까닭 | None)."""
    done = 0
    for i in range(0, len(qids), BATCH):
        if clock() > budget_end:
            return done, 'TIME'
        part = qids[i:i + BATCH]
        url = endpoint + '?' + urllib.parse.urlencode({'query': sparql(part)})
        try:
            body = http.get(url, accept='application/sparql-results+json')
        except Stop as e:
            return done, str(e)
        except Blocked as e:
            return done, f'BLOCKED {e}'
        except IOError:
            return done, 'FETCH'
        got = parse_bindings(json.loads(body))
        d = today()
        for q in part:
            g = got.get(q) or {}
            # [받은 날, 설명 칸, {'l': 라벨 칸, 'p': P18 파일 주소}] — 셋째 칸은 place_names.py(이름 5개 언어·사진 층)가 같은 조회를 함께 쓴다(223기)
            ex = {'l': {k: v for k, v in _pn().wd_label_slots(g.get('l')).items() if v}}
            p18 = sorted((g.get('p') or {}).values())
            if p18:
                ex['p'] = p18[0]
            cache[q] = [d, {k: v for k, v in pick_slots(g.get('d'), g.get('l')).items() if v}, ex]
        done += len(part)
    return done, None


# ---------- 씨앗 모으기 ----------
def _gz(p):
    with open(p, 'rb') as f:
        return json.loads(gzip.decompress(f.read()))


SEED_CACHE = 'processed/desc/col_seeds.json.gz'  # (295기) 수집 층 파일마다 뽑은 씨앗 — 바뀐 층 파일만 받는다
SEED_CACHE_V = 1


def col_partial(j, with_city=True):
    """수집 층 한 파일 → 씨앗 조각(JSON 으로 저장 가능). Seeds.add_col 과 같은 순서·같은 값."""
    if j.get('schema') != 'collect-1' or j.get('dataset') != 'places':
        return None
    n = len(j.get('id') or [])
    col = lambda k: j.get(k) if isinstance(j.get(k), list) and len(j[k]) == n else [None] * n
    ids, wid, wd, dsc = col('id'), col('wid'), col('wd'), col('dsc')
    dla, dlo = col('dla'), col('dlo')
    nm = [col(k) for k in ('nm', 'ko', 'en', 'ja', 'zh')]
    pt = {'n': n, 'q': [], 'names': [], 'tw': [], 'city': []}
    la = lo = 0
    for i in range(n):
        keys = [ids[i], wid[i]]
        if isinstance(dla[i], int) and isinstance(dlo[i], int):
            la += dla[i]; lo += dlo[i]
            if wd[i] and with_city and QID.search(wd[i] or ''):
                pt['city'].append([wd[i], keys, la / 1e5, lo / 1e5])
        names = [c[i] for c in nm if c[i]]
        if wd[i]:
            if QID.search(wd[i] or ''):
                pt['q'].append([wd[i], keys])
            for k in keys:
                if k:
                    pt['names'].append([k, names])
        t = first_sentence(dsc[i]) if dsc[i] else None
        t = t and ok_text(t, names)
        if t and han_script(t) != 'Hans':
            for k in keys:
                if k:
                    pt['tw'].append([k, t])
    return pt


class Seeds:
    """QID → 장소 id 들, 장소 id → 이름들(이름과 같은 설명 거르기), 대만 소개."""

    def __init__(self):
        self.q, self.names, self.tw, self.kto, self.n_rows = {}, {}, {}, {}, 0
        self.prio = set()
        # (11/20 262기) 인기 도시 상자 안 수집 행의 QID → 행 열쇠(id·wid). photo_pri 가 사진 순서를 '일정에 나오는 행' 기준으로 잡는다.
        self.cities = _prio_cities()
        self.city_q = {c['key']: {} for c in self.cities}

    def add_q(self, q, ids, prio=False):
        m = QID.search(q or '')
        if not m:
            return
        s = self.q.setdefault(m.group(1), set())
        s.update(i for i in ids if i)
        if prio:
            self.prio.add(m.group(1))

    def add_col(self, j):
        """수집 층(collect-1) 한 파일."""
        self.apply_partial(col_partial(j, bool(self.cities)))

    def apply_partial(self, pt):
        """(295기) col_partial 결과를 씨앗에 더한다 — 파일을 다시 받지 않고 캐시에서 같은 결과를 만든다(순서도 같음)."""
        if not pt:
            return
        self.n_rows += pt['n']
        for q, keys in pt['q']:
            self.add_q(q, keys)
        for k, names in pt['names']:
            self.names[k] = names
        for k, t in pt['tw']:
            self.tw[k] = t
        if self.cities:
            for q, keys, lat, lon in pt['city']:
                self.add_city(q, keys, lat, lon)

    def add_city(self, q, keys, lat, lon, cap=5000):
        m = QID.search(q or '')
        if not m:
            return
        for c in self.cities:
            w, so, e, no = c['bbox']
            if w <= lon <= e and so <= lat <= no:
                d = self.city_q[c['key']]
                if m.group(1) in d or len(d) < cap:
                    d.setdefault(m.group(1), set()).update(k for k in keys if k)
                return

    def add_tile_ids(self, ids):
        for i in ids or ():
            if isinstance(i, str):
                self.add_q(i, [i], prio=True)

    def add_hours(self, j):
        for rid, v in (j.get('rows') or {}).items():
            if isinstance(v, dict) and v.get('q'):
                self.add_q(v['q'], [rid], prio=True)

    def add_kto(self, j):
        for rid, v in (j.get('rows') or {}).items():
            if isinstance(v, dict) and v.get('ov'):
                t = ok_text(first_sentence(v['ov']), [v.get('n')])
                if t:
                    self.kto[rid] = t


# ---------- 층 파일 ----------
def shard_of(rid, n):
    return int(hashlib.sha1(rid.encode()).hexdigest()[:8], 16) % n


def layer_doc(src, rows, extra=None):
    cnt = {k: sum(1 for v in rows.values() if v.get(k)) for k in SLOTS}
    return {'schema': 'desc-1', 'dataset': 'desc', 'src': src, 'license': LICENSE[src], 'credit': CREDIT[src], 'page': PAGE[src],
            'built': today(), 'langs': cnt, **(extra or {}), 'rows': rows}


def write_layer(path, doc):
    b = gzip.compress(json.dumps(doc, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode(), 9, mtime=0)
    with open(path, 'wb') as f:
        f.write(b)
    return len(b)


def build_layers(seeds, cache, out):
    """씨앗·캐시 → desc-*.json.gz. 돌려줌 = {파일: {'rows': n, 칸: n}}."""
    os.makedirs(out, exist_ok=True)
    wd_rows = {}
    for q, ids in seeds.q.items():
        c = cache.get(q)
        if not c or not c[1]:
            continue
        for rid in ids:
            v = name_filter({k: c[1].get(k) for k in SLOTS}, seeds.names.get(rid, ()))
            if any(v.values()):
                wd_rows[rid] = dict(v, q=q)
    rep, n = {}, NSH
    while True:
        parts = [{} for _ in range(n)]
        for rid in sorted(wd_rows):
            parts[shard_of(rid, n)][rid] = wd_rows[rid]
        sizes = {f'desc-wd-{k:02d}.json.gz': write_layer(os.path.join(out, f'desc-wd-{k:02d}.json.gz'), layer_doc('wikidata', p, {'part': [k, n]}))
                 for k, p in enumerate(parts) if p}
        if not sizes or max(sizes.values()) <= FILE_MAX or n >= 1024:
            break
        for f in sizes:
            os.remove(os.path.join(out, f))
        n *= 2
    for k, p in enumerate(parts):
        if p:
            rep[f'desc-wd-{k:02d}.json.gz'] = {'rows': len(p), **layer_doc('wikidata', p)['langs']}
    for src, fn, m, slot in (('tourapi', 'desc-kto.json.gz', seeds.kto, 'ko'), ('taiwan_tourism', 'desc-tw.json.gz', seeds.tw, 'zh_Hant')):
        if m:
            rows = {rid: dict({k: None for k in SLOTS}, **{slot: t}) for rid, t in sorted(m.items())}
            write_layer(os.path.join(out, fn), layer_doc(src, rows))
            rep[fn] = {'rows': len(rows), **layer_doc(src, rows)['langs']}
    return rep


def summarize(rep):
    tot = {'rows': 0, **{k: 0 for k in SLOTS}}
    for v in rep.values():
        for k in tot:
            tot[k] += v.get(k, 0)
    return tot


# ---------- 실행 ----------
def gs(*args, stdin=None):
    return subprocess.run(['gcloud', 'storage', *args], input=stdin, capture_output=True)


def gs_json(url):
    r = gs('cat', url)
    try:
        return json.loads(gzip.decompress(r.stdout) if url.endswith('.gz') else r.stdout) if r.returncode == 0 else None
    except ValueError:
        return None


def pull_urls(urls, d, batch=40):
    """gs:// 주소들을 batch 개씩 받아 하나씩 넘긴다(받은 파일은 곧 지움)."""
    for i in range(0, len(urls), batch):
        os.makedirs(d, exist_ok=True)
        gs('cp', '-q', '-I', d.rstrip('/') + '/', stdin='\n'.join(urls[i:i + batch]).encode())
        for u in urls[i:i + batch]:
            p = os.path.join(d, u.rsplit('/', 1)[1])
            if os.path.exists(p):
                try:
                    yield u, _gz(p)
                except (ValueError, OSError):
                    pass
                os.remove(p)


def world_tile_urls(bucket):
    base = f'gs://{bucket}/places/v1'
    ver = (gs_json(f'{base}/manifest.json') or {}).get('version')
    if not ver:
        return None, []
    regs = json.load(open(os.path.join(ROOT, 'tools', 'world_regions.json'), encoding='utf-8'))
    files = set()
    for r in regs:
        man = gs_json(f'{base}/{ver}/regions/{r}.json') or gs_json(f'{base}/{ver}/parts/{r}.json') or {}
        files.update(t['file'] for t in man.get('tiles') or [] if isinstance(t.get('file'), str) and t['file'].startswith('w-') and '/' not in t['file'])
    return ver, [f'{base}/{ver}/tiles/{f}' for f in sorted(files)]


def tile_qids(store, bucket, budget_end, clock=time.time):
    """세계 타일 id 에 든 Q-id. 판(version)마다 한 번 훑어 processed/desc/tiles_<판>.json.gz 에 둔다(시간이 모자라면 이어서)."""
    ver, urls = world_tile_urls(bucket)
    if not ver:
        return []
    key = f'processed/desc/tiles_{ver}.json.gz'
    st = store.get_json(key, {}) or {}
    done, ids = set(st.get('done') or []), list(st.get('ids') or [])
    todo = [u for u in urls if u.rsplit('/', 1)[1] not in done]
    wd = tempfile.mkdtemp(dir=os.environ.get('RUNNER_TEMP') or None)
    try:
        for n, (u, j) in enumerate(pull_urls(todo, wd)):
            ids.extend(i for i in j.get('id') or [] if isinstance(i, str) and QID.search(i))
            done.add(u.rsplit('/', 1)[1])
            if n % 200 == 199:
                store.put_json(key, {'done': sorted(done), 'ids': ids})
            if clock() > budget_end:
                break
    finally:
        shutil.rmtree(wd, ignore_errors=True)
    store.put_json(key, {'done': sorted(done), 'ids': ids})
    log(f'세계 타일 {len(done)}/{len(urls)}개 훑음 · Q-id 가 든 id {len(ids):,}')
    return ids


def gather(store, bucket=None, extra_dir=None, budget_end=None, clock=time.time):
    """씨앗: 수집 층 · 영업시간 원본 층 · TourAPI 층 · (gs 일 때) 세계 타일."""
    S = Seeds()
    latest = store.get_json('processed/latest.json', {}) or {}
    paths = latest.get('paths') or {f: f"processed/out/{latest.get('run')}/{f}" for f in latest.get('files', [])}
    keys = sorted(v for k, v in paths.items() if k.startswith('col_') and not k.startswith('col_info_'))
    # (295기) 11/17~19 에 place-desc 가 매 실행 수집 층 전체(약 5 GiB)를 받아 해외 egress 가 쌓였다 → 파일마다 뽑은 씨앗을 캐시하고
    #   처음 보는 Storage 키(= 새로 가공된 층 파일, 키에 가공 판이 들어 있어 내용이 바뀌면 키도 바뀜)만 받는다.
    pc = store.get_json(SEED_CACHE, {}) or {}
    parts = pc.get('parts', {}) if pc.get('v') == SEED_CACHE_V and pc.get('city') == bool(S.cities) else {}
    need = [k for k in keys if k not in parts]
    wd = tempfile.mkdtemp(dir=os.environ.get('RUNNER_TEMP') or None)
    try:
        for i in range(0, len(need), 40):
            got = store.pull(need[i:i + 40], wd, strict=False)
            for k in need[i:i + 40]:
                if k in got:
                    parts[k] = col_partial(_gz(got[k]), bool(S.cities)); os.remove(got[k])
    finally:
        shutil.rmtree(wd, ignore_errors=True)
    for k in keys:
        S.apply_partial(parts.get(k))
    log(f'수집 층 {len(keys)}개 중 새로 받은 것 {len(need)}개(나머지는 씨앗 캐시)')
    if need:
        store.put_json(SEED_CACHE, {'v': SEED_CACHE_V, 'city': bool(S.cities), 'parts': {k: parts[k] for k in keys if k in parts}})
    if extra_dir:  # 시험: 로컬 폴더(hours-*.json.gz · kto_*.json.gz · w-*.json.gz)
        for p in sorted(glob.glob(os.path.join(extra_dir, '*.json.gz'))):
            b = os.path.basename(p)
            j = _gz(p)
            if b.startswith('hours-'):
                S.add_hours(j)
            elif b.startswith('kto_'):
                S.add_kto(j)
            elif b.startswith('w-'):
                S.add_tile_ids(j.get('id'))
    elif bucket:
        base = f'gs://{bucket}/places/v1'
        for prefix, fn in (('hours_src/hours-', S.add_hours), ('layers_extra/kto_', S.add_kto)):
            r = gs('ls', f'{base}/{prefix}*.json.gz')
            urls = [u for u in r.stdout.decode().split() if u.endswith('.json.gz')] if r.returncode == 0 else []
            for _, j in pull_urls(urls, tempfile.mkdtemp(dir=os.environ.get('RUNNER_TEMP') or None)):
                fn(j)
        S.add_tile_ids(tile_qids(store, bucket, budget_end or (clock() + 1200), clock))
    log(f'씨앗: 수집 층 행 {S.n_rows:,} · QID {len(S.q):,}(앱 타일·OSM 쪽 {len(S.prio):,}) · TourAPI 소개 {len(S.kto):,} · 대만 소개 {len(S.tw):,}')
    return S


def order_qids(seeds, cache, now_day=None):
    """물어볼 QID 순서: 한 번도 안 물은 것(앱 타일·OSM 쪽 먼저) → 30일 지난 것(오래된 것 먼저)."""
    d0 = datetime.date.fromisoformat(now_day or today())
    new = sorted((q for q in seeds.q if q not in cache), key=lambda q: (q not in seeds.prio, int(q[1:])))
    old = sorted((q for q in seeds.q if q in cache and ((d0 - datetime.date.fromisoformat(cache[q][0])).days >= REFRESH_DAYS or len(cache[q]) < 3)),
                 key=lambda q: (len(cache[q]) >= 3, cache[q][0]))  # 라벨·사진 칸이 없는 예전 캐시(11/17)도 다시 묻는다
    return new + old


def load_registry():
    import yaml
    return yaml.safe_load(open(os.path.join(ROOT, 'sources', 'registry.yaml'), encoding='utf-8'))


def run(store, bucket=None, extra_dir=None, out=None, budget_min=45.0, http=None, clock=time.time, dry_run=False):
    t_end = clock() + budget_min * 60
    reg = load_registry()
    src = next(s for s in reg['sources'] if s['id'] == 'wikidata_official')
    assert src.get('status') == 'active' and 'descriptions' in (src.get('fields') or []), 'registry wikidata_official 에 descriptions 가 없음'
    seeds = gather(store, bucket, extra_dir, clock() + budget_min * 60 * 0.3, clock)
    # (11/18 233기) 씨앗 QID 를 남긴다 — wikidata-dump.yml(scripts/wd_dump.py)이 같은 씨앗으로 주간 덤프를 훑는다(223기 이름 층과 같은 조인)
    store.put_json('processed/desc/qids.json.gz', {'built': today(), 'q': sorted(int(q[1:]) for q in seeds.q), 'prio': sorted(int(q[1:]) for q in seeds.prio)})
    # (11/20 262기) 인기 도시별 QID → 행 열쇠 — scripts/photo_pri.py 가 사진 순서·행 열쇠로 쓴다
    store.put_json('processed/photos/prio_map.json.gz', {'built': today(), 'cities': [[c['key'], [[q, sorted(k)] for q, k in seeds.city_q[c['key']].items()]] for c in seeds.cities]})
    cache = store.get_json('processed/desc/wd.json.gz', {}) or {}
    for q, v in (store.get_json('processed/desc/wd_dump.json.gz', None) or {}).items():  # 덤프 조인 결과(받은 날 = 덤프 날짜) — 더 새것만 덮어 씀
        if q not in cache or len(cache[q]) < 3 or cache[q][0] < v[0]:
            cache[q] = v
    todo = order_qids(seeds, cache)
    st = store.get_json('processed/desc/state.json', {}) or {}
    if http is None:
        lim = dict(src, daily_limit=src.get('desc_daily_limit', 3000))
        http = Http(lim, st.setdefault('http', {}), st.setdefault('hosts', {}), deadline=t_end)
    asked, stop = fetch_wd(http, src['url'], todo, cache, t_end, clock) if todo else (0, None)
    store.put_json('processed/desc/wd.json.gz', cache)
    left = len(todo) - asked
    out = out or tempfile.mkdtemp(prefix='desc_', dir=os.environ.get('RUNNER_TEMP') or None)
    for f in glob.glob(os.path.join(out, 'desc-*.json.gz')):
        os.remove(f)
    rep = build_layers(seeds, cache, out)
    nrep, nfiles = _pn().build(store, seeds, cache, out, src, t_end + 60 * float(src.get('photo_extra_min', 10)), clock)  # 이름·사진 층(223기) — 같은 씨앗·캐시
    tot = summarize(rep)
    st.update(last={'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'asked': asked, 'left': left, 'stop': stop, 'qids': len(seeds.q),
                    'cached': len(cache), 'rows': tot, 'names': nrep})
    store.put_json('processed/desc/state.json', st)
    if bucket and not dry_run:
        dest = f'gs://{bucket}/places/v1/layers_extra/'
        mine = sorted(glob.glob(os.path.join(out, 'desc-*.json.gz')))
        if mine:
            r = gs('cp', '-q', '-I', dest, stdin='\n'.join(mine).encode())
            if r.returncode != 0:
                raise SystemExit('desc 층 올리기 실패')
        r = gs('ls', dest + 'desc-*.json.gz')  # 이번에 만들지 않은 desc-wd/kto/tw 파일(조각 수가 바뀐 경우)은 지운다. desc-osm-* 은 places-layers 몫이라 두지 않음
        stale = [u for u in (r.stdout.decode().split() if r.returncode == 0 else []) if not u.rsplit('/', 1)[1].startswith('desc-osm-')
                 and u.rsplit('/', 1)[1] not in {os.path.basename(m) for m in mine}]
        if stale:
            gs('rm', '-q', '-I', stdin='\n'.join(stale).encode())
        # 이름·사진 층(names-wd-*, photos-wd-*): 같은 곳에 올리고, 이번에 만들지 않은 같은 이름 꼴 파일은 지운다(names-osm-* 은 places-layers 몫)
        mine2 = [os.path.join(out, f) for f in nfiles]
        if mine2:
            r = gs('cp', '-q', '-I', dest, stdin='\n'.join(mine2).encode())
            if r.returncode != 0:
                raise SystemExit('이름·사진 층 올리기 실패')
        r = gs('ls', dest + 'names-wd-*.json.gz', dest + 'photos-wd-*.json.gz')
        stale = [u for u in (r.stdout.decode().split() if r.stdout else []) if u.endswith('.json.gz') and u.rsplit('/', 1)[1] not in set(nfiles)]
        if stale:
            gs('rm', '-q', '-I', stdin='\n'.join(stale).encode())
    lines = [f'## 장소 소개(place-desc) {today()}', f"- QID {len(seeds.q):,} · 이번에 물은 것 {asked:,} · 남은 것 {left:,} · 캐시 {len(cache):,}" + (f' · 멈춘 까닭 {stop}' if stop else ''),
             '| 파일 | 행 | ' + ' | '.join(SLOTS) + ' |', '|---' * (len(SLOTS) + 2) + '|']
    lines += [f"| {f} | {v['rows']:,} | " + ' | '.join(f'{v[k]:,}' for k in SLOTS) + ' |' for f, v in sorted(rep.items()) if not f.startswith('desc-wd-')]
    wd = summarize({f: v for f, v in rep.items() if f.startswith('desc-wd-')})
    lines.append(f"| desc-wd-* ({sum(1 for f in rep if f.startswith('desc-wd-'))}개) | {wd['rows']:,} | " + ' | '.join(f'{wd[k]:,}' for k in SLOTS) + ' |')
    lines += _pn().report_lines(nrep)
    p = os.environ.get('GITHUB_STEP_SUMMARY')
    if p:
        open(p, 'a', encoding='utf-8').write('\n'.join(lines) + '\n')
    print('\n'.join(lines))
    return rep, st['last']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['build'])
    ap.add_argument('--store', required=True); ap.add_argument('--bucket', default=''); ap.add_argument('--extra', default='')
    ap.add_argument('--out', default=''); ap.add_argument('--budget-min', type=float, default=45.0); ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    run(Store(a.store), a.bucket or None, a.extra or None, a.out or None, a.budget_min, dry_run=a.dry_run)
    return 0


if __name__ == '__main__':
    sys.exit(main())
