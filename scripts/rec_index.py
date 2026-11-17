#!/usr/bin/env python3
"""트립루티 추천 지수(rec-1) — 우리가 공개 자료로 계산한 0~100 점수. 다른 서비스의 평점·리뷰는 받지도 싣지도 않는다. (2026-11-18, 245기)

  python3 scripts/rec_index.py build --store gs://<b>/collect/v1 --extra gs://<b>/places/v1/layers_extra --out <폴더> [--chunk 0/8] [--ccs KR,JP]
  python3 scripts/rec_index.py build --store <로컬 폴더> --extra <로컬 폴더> --out <폴더> --ovt-json <시험용 Overture 행 JSON>   # 시험

계산식·신호·표시 문구는 docs/RECOMMEND_INDEX.md(공개 문서)와 같다. 바꾸면 둘 다 바꾸고 VERSION 을 올린다.

하는 일(나라 하나씩 — 러너 디스크·메모리 안에서):
 1. Overture Maps places 를 공식 공개 S3(익명, no-sign-request)에서 그 나라 상자만 읽는다(일정에 들어갈 종류만, 작은 칸만).
 2. 우리 장소(수집 층 col_*, 대부분 Foursquare OS Places·Wikidata·Wikivoyage·공공데이터를 묶은 것)와 맞춘다:
    ① Foursquare id 일치(Overture 레코드의 sources 에 Foursquare record_id 가 있음) ② Wikidata QID 일치 ③ 이름 + 거리 + 종류.
 3. 신호를 모아 점수를 낸다: 교차 등장 수 · Overture confidence · Wikidata sitelinks · 위키 문서 존재 · 관광공사 등재 ·
    블로그·영상 학습 통계의 종류별 언급 비율(숫자 분포만) · 같은 나라·같은 종류 안 상대 순위.
 4. 파일(장소 내용은 로그에 쓰지 않음 — 개수만):
    rec-<cc>-NN.json.gz        schema rec-1: {장소 id: [점수, 교차 수, conf×100|null, sitelinks|null, 위키 0/1, 관광공사 0/1|null, 언급 비율×100|null, 순위 백분위]}
    names-ovt-<cc>.json.gz     schema names-2: Overture names.rules(variant language) 의 ko·en·ja·zh_Hans·zh_Hant — 우리 장소에 붙임(CDLA-Permissive-2.0)
    col_ovtadd_<cc>.json.gz    schema collect-1: 우리에게 없는 일정 대상 장소(Overture confidence ≥ ADD_MIN_CONF), 행마다 lic·src. 가공(process)이 같은 id 를
                               실으면 다음 실행부터 여기서 빠진다(같은 곳 1행).
 출처별 라이선스와 표시 문구는 파일 머리(licenses·credit)와 행의 lic 칸에 함께 둔다.
"""
import argparse, bisect, difflib, gzip, json, math, os, re, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, os.path.join(HERE, 'collect'))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, 'tools'))
from common import Store, norm_name, map_cat, today  # noqa: E402
import coverage as COV  # noqa: E402

VERSION = 'rec-1.0'
ADD_MIN_CONF = 0.8          # 새로 더하는 Overture 장소의 최소 confidence
SCAN_MIN_CONF = 0.5         # 맞추기·신호용으로 읽는 Overture 최소 confidence
SLOTS = ('ko', 'en', 'ja', 'zh_Hans', 'zh_Hant')
# 가중치(합 100). 쓸 수 없는 신호(관광공사 = 한국 밖, 학습 통계 = 그 나라 표본 없음)는 빼고 나머지로 다시 나눈다 — 문서 3절.
W = {'x': 30, 'cf': 20, 'sl': 20, 'wp': 10, 'kto': 10, 'pt': 10}
RANK_SHARE = 0.2            # 최종 = 0.8 × 근거 점수 + 0.2 × (같은 나라·같은 종류 안 순위 백분위)
SL_CAP = 100                # sitelinks 100개 이상 = 만점
CELL = 0.002                # 격자 칸(도) — 약 200 m
CROSS_CAP = 4               # 교차 등장 4곳 이상 = 만점
# 교차 등장 수에 세는 공개 출처(수집 층 src 의 출처 id · Overture sources.dataset). 같은 출처는 한 번만.
OVT_DATASETS = {'meta': 'meta', 'Microsoft': 'microsoft', 'Foursquare': 'fsq_os_places', 'AllThePlaces': 'alltheplaces'}
LICENSES = {
    'overture_places': {'license': 'CDLA-Permissive-2.0', 'credit': '© Overture Maps Foundation (CDLA-Permissive-2.0)', 'url': 'https://docs.overturemaps.org/attribution/'},
    'fsq_os_places': {'license': 'Apache-2.0', 'credit': 'Foursquare Open Source Places — Copyright 2024 Foursquare Labs, Inc. (Apache-2.0)',
                      'url': 'https://docs.foursquare.com/data-products/docs/access-fsq-os-places'},
    'alltheplaces': {'license': 'CC0-1.0', 'credit': 'AllThePlaces (CC0), Overture Maps 경유', 'url': 'https://www.alltheplaces.xyz/'},
    'wikidata': {'license': 'CC0-1.0', 'credit': 'Wikidata (CC0)', 'url': 'https://www.wikidata.org/wiki/Wikidata:Licensing'},
    'tourapi': {'license': 'KOGL-1', 'credit': '한국관광공사 TourAPI 4.0 (등재 여부만 사용)', 'url': 'https://www.data.go.kr/data/15101578/openapi.do'},
    'patterns': {'license': 'internal-stats', 'credit': '트립루티 자체 학습 통계(종류별 언급 비율 숫자만 — 원문·출처는 앱에 보이지 않음)', 'url': None},
}
FIELDS = ['score', 'cross', 'conf', 'sitelinks', 'wiki', 'kto', 'mention', 'rank_pct']


# ---------------------------------------------------------------- 이름·거리·종류
def dist_m(la1, lo1, la2, lo2):
    x = math.radians(lo2 - lo1) * math.cos(math.radians((la1 + la2) / 2))
    return 6371000 * math.hypot(x, math.radians(la2 - la1))


def name_sim(a, b):
    """정규화 이름 두 개 → 0~1. 같으면 1, 한쪽이 다른 쪽을 품으면(3자 이상) 0.9, 그 밖은 글자 순서 비율."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    s, l = (a, b) if len(a) <= len(b) else (b, a)
    if len(s) >= 3 and s in l:
        return 0.9
    return difflib.SequenceMatcher(None, a, b).ratio()


def cat_key(c):
    return re.sub(r'[^a-z]', '', (c or '').lower())


def type_ok(ta, tb):
    return ta == tb or 'other' in (ta, tb)


def is_match(o, p, d, sim, crowded=False):
    """o = Overture 행, p = 우리 행. 키 일치가 아니면 이름·거리·종류로만. 문서 2절 표와 같은 규칙.
    crowded = 300 m 안에 같은 이름의 Overture 장소가 또 있음(체인) → 40 m 안만 짝짓는다."""
    if not type_ok(o['t'], p['t']):
        return False
    if crowded:
        return sim >= 0.85 and d <= 40
    if sim >= 0.85 and d <= 60:
        return True
    if sim >= 0.999 and d <= 150:
        return True
    return sim >= 0.999 and d <= 300 and o['t'] == 'sight' == p['t']


class Grid:
    def __init__(self):
        self.g = {}

    def add(self, i, la, lo):
        self.g.setdefault((int(la // CELL), int(lo // CELL)), []).append(i)

    def near(self, la, lo, r=1):
        a, b = int(la // CELL), int(lo // CELL)
        for x in range(a - r, a + r + 1):
            for y in range(b - r, b + r + 1):
                yield from self.g.get((x, y), ())


def match(ovt, ours):
    """ovt, ours = 행 목록(각 행 dict: id, nm(정규화), la, lo, t, q?, fsq?(Overture 쪽 Foursquare id 목록)).
    → {우리 행 번호: (Overture 행 번호, 방법)}. Overture 행 하나는 한 번만 쓴다. 방법 = fsq | wd | name."""
    out, used = {}, set()
    by_fsq, by_q, grid = {}, {}, Grid()
    for i, o in enumerate(ovt):
        for f in o.get('fsq') or ():
            by_fsq[f] = i
        if o.get('q'):
            by_q.setdefault(o['q'], i)
        grid.add(i, o['la'], o['lo'])
    rest = []
    for j, p in enumerate(ours):  # 1단: 키 일치
        k = None
        if p['id'].startswith('fsq_os_places:'):
            i = by_fsq.get(p['id'].split(':', 1)[1])
            if i is not None and i not in used and dist_m(p['la'], p['lo'], ovt[i]['la'], ovt[i]['lo']) <= 1000:
                k = (i, 'fsq')
        if k is None and p.get('q') in by_q:
            i = by_q[p['q']]
            if i not in used and dist_m(p['la'], p['lo'], ovt[i]['la'], ovt[i]['lo']) <= 2000:
                k = (i, 'wd')
        if k:
            out[j] = k; used.add(k[0])
        else:
            rest.append(j)
    by_nm = {}
    for i, o in enumerate(ovt):
        by_nm.setdefault(o['nm'], []).append(i)
    same = {}
    for lst in by_nm.values():  # 300 m 안 같은 이름 수(체인 판정) — 같은 이름끼리만 비교
        if len(lst) == 1:
            same[lst[0]] = 0
            continue
        g = Grid()
        for i in lst:
            g.add(i, ovt[i]['la'], ovt[i]['lo'])
        for i in lst:
            o = ovt[i]
            same[i] = sum(1 for k in g.near(o['la'], o['lo'], 2) if k != i and dist_m(o['la'], o['lo'], ovt[k]['la'], ovt[k]['lo']) <= 300)
    cand = []
    for j in rest:  # 2단: 이름 + 거리 + 종류 — 후보를 다 모아 (비슷함 높은 것, 가까운 것) 순으로 한 번씩만 짝짓는다
        p = ours[j]
        for i in grid.near(p['la'], p['lo'], 2):
            if i in used:
                continue
            o = ovt[i]
            d = dist_m(p['la'], p['lo'], o['la'], o['lo'])
            if d > 300:
                continue
            la_, lb_ = len(p['nm']), len(o['nm'])
            if p['nm'] != o['nm'] and 2 * min(la_, lb_) / max(la_ + lb_, 1) < 0.85 and not (min(la_, lb_) >= 3 and (p['nm'] in o['nm'] or o['nm'] in p['nm'])):
                continue  # 길이만으로 0.85 에 못 닿음(글자 순서 비율 상한) — 비싼 비교를 건너뜀
            s = name_sim(p['nm'], o['nm'])
            if s >= 0.85 and is_match(o, p, d, s, same[i] > 0):
                cand.append((-s, d, j, i))
    for _s, _d, j, i in sorted(cand):
        if j not in out and i not in used:
            out[j] = (i, 'name'); used.add(i)
    return out


# ---------------------------------------------------------------- 점수
def f_cross(n):
    return min(max(n - 1, 0), CROSS_CAP - 1) / (CROSS_CAP - 1)


def f_conf(cf):
    return None if cf is None else min(max((cf - 0.5) / 0.5, 0.0), 1.0)


def f_sl(sl):
    return 0.0 if not sl else min(math.log1p(sl) / math.log1p(SL_CAP), 1.0)


def evidence(sig):
    """신호 → 근거 점수 0~1. 값이 None 인 신호는 '이 장소에 쓸 수 없음'(나라 밖·표본 없음)이라 빼고 다시 나눈다.
    conf 가 None(Overture 에 없음)은 0 으로 센다 — '공개 출처에 덜 나온다'는 뜻이라 빼지 않는다."""
    v = {'x': f_cross(sig['cross']), 'cf': f_conf(sig['conf']) or 0.0, 'sl': f_sl(sig['sitelinks']), 'wp': float(sig['wiki']),
         'kto': None if sig['kto'] is None else float(sig['kto']), 'pt': sig['mention']}
    num = sum(W[k] * x for k, x in v.items() if x is not None)
    den = sum(W[k] for k, x in v.items() if x is not None)
    return num / den if den else 0.0


def finalize(groups):
    """groups = {(나라, 종류): [(근거 점수, 행 참조)]} → 행 참조마다 (최종 점수 0~100, 순위 백분위 0~100).
    순위 백분위 = 같은 나라·같은 종류 안에서 근거 점수가 이 장소보다 낮은 곳의 비율(같은 값은 반만)."""
    res = {}
    for _, lst in groups.items():
        xs = sorted(e for e, _ in lst)
        n = len(xs)
        for e, ref in lst:
            lo, hi = bisect.bisect_left(xs, e), bisect.bisect_right(xs, e)
            pct = ((lo + (hi - lo - 1) / 2) / (n - 1)) if n > 1 else 0.5
            res[ref] = (round(100 * ((1 - RANK_SHARE) * e + RANK_SHARE * pct)), round(100 * pct))
    return res


# ---------------------------------------------------------------- 입력
def lang_names(rules, common=None):
    """Overture names.rules(variant language) · names.common → 5개 언어 칸. 번역·간체↔번체 변환 없음, 꼬리표 없는 zh 는 글자로 갈릴 때만."""
    import place_names as PN
    out = {}
    tags = {}
    for r in rules or ():
        if isinstance(r, dict) and r.get('variant') == 'language' and r.get('language') and r.get('value'):
            tags.setdefault(r['language'], r['value'])
    for k, v in (common or {}).items() if isinstance(common, dict) else ():
        tags.setdefault(k, v)
    low = {k.lower(): v for k, v in tags.items()}
    for slot in ('ko', 'en', 'ja'):
        if PN.name_ok(low.get(slot)):
            out[slot] = low[slot]
    for t in ('zh-hans', 'zh-cn', 'zh-sg'):
        if PN.name_ok(low.get(t)):
            out.setdefault('zh_Hans', low[t])
    for t in ('zh-hant', 'zh-tw', 'zh-hk', 'zh-mo'):
        if PN.name_ok(low.get(t)):
            out.setdefault('zh_Hant', low[t])
    z = low.get('zh')
    if PN.name_ok(z):
        k = PN.zh_generic(z)
        if k in ('zh_Hans', 'zh_Hant'):
            out.setdefault(k, z)
    return out


def ovt_scan(cc, boxes, release, min_conf=SCAN_MIN_CONF):
    """공식 공개 S3(익명)에서 나라 하나 → 일정 대상 종류 행 목록. 작은 칸만 읽는다."""
    import ovt_common
    os.environ['OVERTURE_RELEASE'] = 'release/' + release
    import importlib
    importlib.reload(ovt_common)
    c = ovt_common.con()
    c.execute(f"SET memory_limit='{os.environ.get('DUCK_MEM', '10GB')}'"); c.execute('SET preserve_insertion_order=false')
    out = []
    for b in boxes:
        cur = c.execute(f"""select id, names.primary, names.rules, coalesce(basic_category, taxonomy.primary), confidence,
              list_transform(sources, lambda s: struct_pack(d := s.dataset, r := s.record_id, l := s.license)),
              (bbox.xmin+bbox.xmax)/2, (bbox.ymin+bbox.ymax)/2
            from {ovt_common.SRC} where bbox.xmin between {b[0]} and {b[2]} and bbox.ymin between {b[1]} and {b[3]}
            and addresses[1].country = '{cc}' and confidence >= {min_conf} and coalesce(operating_status, 'open') = 'open'""")
        while True:
            batch = cur.fetchmany(50000)
            if not batch:
                break
            for i, nm, rules, cat, cf, srcs, lon, lat in batch:
                c2 = map_cat(cat)
                if nm and lat is not None and COV.itin_of(c2):
                    out.append({'id': i, 'name': nm, 'names': lang_names(rules), 'c': c2, 'cf': round(cf or 0, 3), 'la': lat, 'lo': lon,
                                'srcs': [s['d'] for s in srcs or () if s.get('d') and s['d'] != 'Overture'],
                                'fsq': [s['r'] for s in srcs or () if s.get('d') == 'Foursquare' and s.get('r')],
                                'lic': sorted({s['l'] for s in srcs or () if s.get('l')})})
    seen, uniq = set(), []
    for o in out:  # 상자가 겹치면 같은 id 가 두 번 — 한 번만
        if o['id'] not in seen:
            seen.add(o['id']); uniq.append(o)
    return uniq


def col_rows(j):
    """수집 층 한 파일 → 일정 대상 행만(dict)."""
    n = len(j['id'])
    col = lambda k: j.get(k) if isinstance(j.get(k), list) and len(j[k]) == n else [None] * n
    ids, nm, src, wd, wid = j['id'], j['nm'], col('src'), col('wd'), col('wid')
    names = {k: col(k) for k in ('ko', 'en', 'ja', 'zh')}
    cats = j.get('cats') or []
    la = lo = 0
    out = []
    for i in range(n):
        la += j['dla'][i]; lo += j['dlo'][i]
        c = cats[j['c'][i]] if 0 <= j['c'][i] < len(cats) else 'other'
        if not COV.itin_of(c):
            continue
        q = re.search(r'Q[1-9]\d*', wd[i] or '') or re.search(r'Q[1-9]\d*$', wid[i] or '')
        out.append({'id': ids[i], 'nm': norm_name(nm[i]), 'la': la / 1e5, 'lo': lo / 1e5, 'c': c, 't': COV.type_of(c), 'q': q.group(0) if q else None,
                    'src': [s for s in (src[i] or '').split('|') if s], 'have': {k: bool(v[i]) for k, v in names.items()}})
    return out


def wd_stats(store):
    """Wikidata 캐시(place_desc·wd_dump 가 남긴 것)의 sitelinks 수·위키 문서 여부 → {QID: (수, 0/1)}. 없으면 빈 dict."""
    out = {}
    for key in ('processed/desc/wd.json.gz', 'processed/desc/wd_dump.json.gz'):
        for q, v in (store.get_json(key, None) or {}).items():
            ex = v[2] if isinstance(v, list) and len(v) > 2 and isinstance(v[2], dict) else {}
            if 's' in ex or 'w' in ex:
                out[q] = (int(ex.get('s') or 0), int(bool(ex.get('w'))))
    return out


def kto_rows(paths):
    out = []
    for p in paths:
        try:
            j = json.loads(gzip.decompress(open(p, 'rb').read()))
        except Exception:
            continue
        for rid, v in (j.get('rows') or {}).items():
            if isinstance(v, dict) and v.get('lat') is not None and v.get('n'):
                out.append({'id': rid, 'nm': norm_name(v['n']), 'la': float(v['lat']), 'lo': float(v['lon'])})
    return out


def mention_share(pat):
    """patterns-1(블로그·영상 학습 통계, 숫자 분포만) → {나라: {종류: 0~1}}. 나라 안 종류별 표본 수 ÷ 가장 많은 종류의 표본 수.
    장소 하나하나의 언급은 쓰지 않는다(학습기가 장소 이름을 결과에 남기지 않는다 — docs/PATTERNS_POLICY.md)."""
    m = {'sight': 'sight', 'museum': 'sight', 'park': 'sight', 'activity': 'sight', 'food': 'food', 'cafe': 'food', 'lodging': 'lodging', 'transit': 'transport'}
    out = {}
    for cc, t in ((pat or {}).get('groups') or {}).get('cc', {}).items():
        cnt = {}
        for typ, d in t.items():
            if typ in m and isinstance(d, dict):
                n = max([v.get('n', 0) for v in d.values() if isinstance(v, dict)] + [d.get('n', 0) if isinstance(d.get('n'), int) else 0])
                cnt[m[typ]] = cnt.get(m[typ], 0) + n
        top = max(cnt.values() or [0])
        if top >= 30:  # 표본이 적으면 쓰지 않음
            out[cc.upper()] = {k: round(v / top, 3) for k, v in cnt.items()}
    return out


# ---------------------------------------------------------------- 나라 하나
def build_cc(cc, layers, ovt, wd, kto, share, prev_ids=frozenset()):
    """layers = 수집 층 dict 목록, ovt = Overture 행 목록. → (rec rows, names rows, 새 장소 rows, 통계)."""
    ours = [r for j in layers for r in col_rows(j)]
    O = [{**o, 'nm': norm_name(o['name']), 't': COV.type_of(o['c'])} for o in ovt]
    m = match(O, ours)
    st = {'ours': len(ours), 'ovt': len(O), 'm_fsq': 0, 'm_wd': 0, 'm_name': 0, 'kto': 0, 'added': 0, 'names': {k: 0 for k in SLOTS}}
    kg = Grid()
    for i, k in enumerate(kto):
        kg.add(i, k['la'], k['lo'])
    groups, sigs, names_rows = {}, {}, {}
    for j, p in enumerate(ours):
        srcs = {s for s in p['src']}
        cf = None
        if j in m:
            o = O[m[j][0]]
            st['m_' + m[j][1]] += 1
            srcs.add('overture_places'); srcs.update(OVT_DATASETS.get(d, d.lower()) for d in o['srcs'])
            cf = o['cf']
            gain = {k: v for k, v in o['names'].items() if not (k in ('ko', 'en', 'ja') and p['have'][k])}
            if gain:
                names_rows[p['id']] = {k: gain.get(k) for k in SLOTS}
                for k in gain:
                    st['names'][k] += 1
        sl, wp = wd.get(p['q'], (None, 0)) if p['q'] else (None, 0)
        if p['q']:
            srcs.add('wikidata')
        k = None
        if cc == 'KR':
            k = 0
            for i in kg.near(p['la'], p['lo'], 1):
                if name_sim(p['nm'], kto[i]['nm']) >= 0.85 and dist_m(p['la'], p['lo'], kto[i]['la'], kto[i]['lo']) <= 150:
                    k = 1; break
            st['kto'] += k
            if k:
                srcs.add('tourapi')
        sig = {'cross': len(srcs), 'conf': cf, 'sitelinks': sl, 'wiki': wp, 'kto': k, 'mention': (share.get(cc) or {}).get(p['t'])}
        sigs[p['id']] = sig
        groups.setdefault(p['t'], []).append((evidence(sig), p['id']))
    used = {i for i, _ in m.values()}
    adds = []
    for i, o in enumerate(O):  # 우리에게 없는 일정 대상 장소
        if i in used or o['cf'] < ADD_MIN_CONF or ('overture_places:' + o['id']) in prev_ids:
            continue
        rid = 'overture_places:' + o['id']
        srcs = {'overture_places'} | {OVT_DATASETS.get(d, d.lower()) for d in o['srcs']}
        sig = {'cross': len(srcs), 'conf': o['cf'], 'sitelinks': None, 'wiki': 0, 'kto': None if cc != 'KR' else 0, 'mention': (share.get(cc) or {}).get(o['t'])}
        sigs[rid] = sig
        groups.setdefault(o['t'], []).append((evidence(sig), rid))
        r = {'id': rid, 'nm': o['name'][:120], 'lat': round(o['la'], 6), 'lon': round(o['lo'], 6), 'c': o['c'], 'src': 'overture_places',
             'srcs': sorted(srcs & {'overture_places', 'fsq_os_places'}) or ['overture_places'], 'tr': 1,
             'lic': '|'.join(o['lic']) or 'CDLA-Permissive-2.0'}
        for k2, v in o['names'].items():
            r['zh' if k2.startswith('zh') else k2] = v
        adds.append(r)
    st['added'] = len(adds)
    fin = finalize(groups)
    rec = {rid: [fin[rid][0], s['cross'], None if s['conf'] is None else round(100 * s['conf']), s['sitelinks'], s['wiki'], s['kto'],
                 None if s['mention'] is None else round(100 * s['mention']), fin[rid][1]] for rid, s in sigs.items()}
    st['rec'] = len(rec)
    st['rec2'] = sum(1 for s in sigs.values() if n_signals(s) >= 2)
    return rec, names_rows, adds, st


def n_signals(s):
    """근거로 쓴 독립 신호 수(교차 2곳 이상·conf·sitelinks·위키·관광공사·언급 비율)."""
    return (s['cross'] >= 2) + (s['conf'] is not None) + bool(s['sitelinks']) + bool(s['wiki']) + bool(s['kto']) + (s['mention'] is not None)


def head(schema, cc, src, extra=None):
    used = ['overture_places', 'fsq_os_places', 'alltheplaces', 'wikidata', 'tourapi', 'patterns'] if schema == 'rec-1' else ['overture_places']
    return {'schema': schema, 'cc': cc, 'src': src, 'built': today(), 'version': VERSION,
            'licenses': {k: LICENSES[k] for k in used}, 'credit': ' · '.join(LICENSES[k]['credit'] for k in used if k != 'patterns'), **(extra or {})}


def write(path, obj):
    with open(path + '.tmp', 'wb') as f:
        f.write(gzip.compress(json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode(), 6))
    os.replace(path + '.tmp', path)


def emit(out, cc, rec, names_rows, adds, per_file=400000):
    files = []
    keys = sorted(rec)
    for n, i in enumerate(range(0, len(keys), per_file)):
        p = os.path.join(out, f'rec-{cc}-{n:02d}.json.gz')
        write(p, {**head('rec-1', cc, 'trip_routie_rec', {'fields': FIELDS, 'method': 'docs/RECOMMEND_INDEX.md',
                                                         'label': {'ko': '트립루티 추천 지수', 'en': 'TripRoutie Pick Score', 'ja': 'TripRoutie おすすめ指数',
                                                                   'zh_Hans': 'TripRoutie 推荐指数', 'zh_Hant': 'TripRoutie 推薦指數'}}),
                  'rows': {k: rec[k] for k in keys[i:i + per_file]}})
        files.append(p)
    if names_rows:
        p = os.path.join(out, f'names-ovt-{cc}.json.gz')
        langs = {k: sum(1 for v in names_rows.values() if v.get(k)) for k in SLOTS}
        write(p, {**head('names-2', cc, 'overture_places', {'license': 'CDLA-Permissive-2.0', 'page': 'https://docs.overturemaps.org/attribution/', 'langs': langs}),
                  'rows': names_rows})
        files.append(p)
    if adds:
        sys.path.insert(0, os.path.join(HERE, 'collect'))
        from process import to_layer
        p = os.path.join(out, f'col_ovtadd_{cc}.json.gz')
        write(p, to_layer(cc, adds))
        files.append(p)
    return files


# ---------------------------------------------------------------- 실행
def cc_boxes():
    reg = json.load(open(os.path.join(ROOT, 'tools', 'world_regions.json'), encoding='utf-8'))
    out = {}
    for r, v in reg.items():
        for cc in v['cc']:
            out.setdefault(cc, []).extend(v['bbox'])
    return out


def latest_release():
    import urllib.request
    b = urllib.request.urlopen('https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com/?list-type=2&prefix=release/&delimiter=/', timeout=60).read()
    rels = re.findall(rb'<Prefix>release/([0-9][0-9.\-]+)/</Prefix>', b)
    return max(x.decode() for x in rels)


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
    ccs = sorted(by_cc)
    if a.ccs:
        ccs = [c for c in ccs if c in a.ccs.split(',')]
    if a.chunk:
        i, n = (int(x) for x in a.chunk.split('/'))
        ccs = [c for k, c in enumerate(ccs) if k % n == i]
    extra = Store(a.extra) if a.extra else None
    wd = wd_stats(store)
    share = mention_share(json.loads(open(a.patterns).read())) if a.patterns and os.path.exists(a.patterns) else {}
    kto = []
    if 'KR' in ccs and extra:
        d = tempfile.mkdtemp()
        keys = [k for k in extra.list('kto_') if k.endswith('.json.gz')]
        got = extra.pull(keys, d, strict=False) if hasattr(extra, 'pull') else {}
        kto = kto_rows(got.values())
    boxes = cc_boxes()
    test_ovt = json.load(open(a.ovt_json)) if a.ovt_json else None
    rel = None if test_ovt is not None else (a.release or latest_release())
    summary = {'version': VERSION, 'release': rel, 'built': today(), 'wd_q': len(wd), 'kto_rows': len(kto), 'share_cc': len(share), 'cc': {}}
    for cc in ccs:
        t0 = time.time()
        d = tempfile.mkdtemp(dir=os.environ.get('RUNNER_TEMP') or None)
        got = store.pull(by_cc[cc], d, strict=False)
        layers = [json.loads(gzip.decompress(open(p, 'rb').read())) for p in got.values()]
        prev = {i for j in layers for i in j['id'] if i.startswith('overture_places:')}
        ovt = test_ovt.get(cc, []) if test_ovt is not None else ovt_scan(cc, boxes.get(cc, []), rel)
        rec, nm, adds, st = build_cc(cc, layers, ovt, wd, kto, share, prev)
        emit(a.out, cc, rec, nm, adds)
        st['sec'] = round(time.time() - t0)
        summary['cc'][cc] = st
        print(cc, json.dumps(st, ensure_ascii=False), flush=True)
        import shutil
        shutil.rmtree(d, ignore_errors=True)
    json.dump(summary, open(os.path.join(a.out, f"rec_summary_{(a.chunk or 'all').replace('/', 'of')}.json"), 'w'), ensure_ascii=False)
    return summary


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd', required=True)
    b = sp.add_parser('build')
    b.add_argument('--store', required=True); b.add_argument('--extra'); b.add_argument('--out', required=True)
    b.add_argument('--ccs'); b.add_argument('--chunk'); b.add_argument('--release'); b.add_argument('--patterns'); b.add_argument('--ovt-json')
    a = ap.parse_args()
    build(a)


if __name__ == '__main__':
    main()
