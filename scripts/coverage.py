#!/usr/bin/env python3
"""커버리지 측정 — '구글 지도 수준' 목표를 숫자로 잰다(docs/COVERAGE.md). 구글 데이터는 받지도 저장하지도 않는다.

  python3 scripts/coverage.py --status collect/status.json                       # 수집 단계(저장소에 있는 요약만으로)
  python3 scripts/coverage.py --layers <층 파일 폴더> [--status …]               # 게시 층 파일(col_*.json.gz, hours·photos 층)을 직접 읽어
  python3 scripts/coverage.py --store gs://<bucket>/collect/v1 [--status …]      # Storage 의 최신 가공 판을 40개씩 받아 읽고 지움
     --json out.json --md out.md --prev 어제.json

재는 것: (a) 출처별 받은 조각·행 ÷ 공개 총수 (b) 종류(명소·식당·숙소·교통)·나라·도시별 장소 수와 속성 채움 비율
(c) 게시 장소 수 ÷ 공개 출처 합집합(하한~상한) + 구글 공개 문구 대비 참고값.
(d) (11/17) 장소 소개(desc-1) 언어별 커버리지 — ko·en·ja·zh_Hans·zh_Hant 칸마다 따로(다른 언어로 채운 값 없음).
(e) (11/18, 223기) 이름 5개 언어 칸(ko·en·ja·zh_Hans·zh_Hant)·영업시간·사진 채움 — 수집 층 제 칸 + 이름 층(names-2: Wikidata 라벨·OSM name:xx,
    names-1: OSM 한국어·영어) · 영업시간 층(hours-1) · 사진 층(photos-1)을 장소 id 또는 타일 id(wid)로 맞춰 센다.
    언어 칸은 그 언어로 적힌 원천 값만 센다(원어 이름·다른 언어·간체↔번체 변환 값은 세지 않음). 수집 층 zh 칸은 글자로 간체·번체가 갈릴 때만 그 칸.
    영업시간은 tools/oh_check.oh_ok 로 알아듣는 값만, 사진은 photo_url(또는 예전 photo)이 있는 행만.
"""
import argparse, gzip, json, os, shutil, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
TYPES = ('sight', 'food', 'lodging', 'transport', 'other')
TYPE_KO = {'sight': '명소', 'food': '식당·카페', 'lodging': '숙소', 'transport': '교통', 'other': '그 밖'}
NAME_SLOTS = ('ko', 'en', 'ja', 'zh_Hans', 'zh_Hant')
ATTRS = NAME_SLOTS + ('h', 'photo', 'tel', 'url', 'multi')
ATTR_KO = {'ko': '이름 한국어', 'en': '이름 영어', 'ja': '이름 일본어', 'zh_Hans': '이름 중국어 간체', 'zh_Hant': '이름 중국어 번체', 'h': '영업시간', 'photo': '사진',
           'tel': '전화', 'url': '웹', 'multi': '출처 2곳 이상'}
sys.path.insert(0, os.path.join(ROOT, 'tools'))
from oh_check import oh_ok  # noqa: E402
import place_names as PN  # noqa: E402
_KW = (('lodging', ('hotel', 'hostel', 'motel', 'lodging', 'resort', 'guesthouse', 'ryokan', 'bedbreakfast', 'bedandbreakfast', 'vacationrental', 'campground', 'accommodation')),
       ('transport', ('station', 'airport', 'busstop', 'terminal', 'ferry', 'metro', 'transport', 'tramstop', 'platform', 'pier', 'railway', 'busline', 'taxistand')),
       ('sight', ('sight', 'museum', 'landmark', 'monument', 'park', 'temple', 'shrine', 'church', 'cathedral', 'mosque', 'castle', 'palace', 'gallery', 'viewpoint', 'scenic',
                  'beach', 'garden', 'zoo', 'aquarium', 'attraction', 'historic', 'memorial', 'plaza', 'themepark', 'activity', 'event', 'waterfall', 'mountain', 'tower')),
       ('food', ('restaurant', 'food', 'cafe', 'coffee', 'bakery', 'diner', 'bistro', 'izakaya', 'noodle', 'ramen', 'sushi', 'pizza', 'burger', 'dessert', 'teahouse', 'tearoom',
                 'steakhouse', 'brewery', 'icecream', 'bbq', 'eatery')))
_tcache = {}
DESC_SLOTS = ('ko', 'en', 'ja', 'zh_Hans', 'zh_Hant')  # 장소 소개 층(desc-1, scripts/place_desc.py) 언어 칸


def type_of(cat):
    """앱 분류 낱말(소문자 알파벳) → 여행 종류. 낱말 포함으로 가르는 어림 규칙이다(주차장 'parking' 은 명소가 아님 등 예외 몇 개)."""
    c = (cat or '').lower()
    if c in _tcache:
        return _tcache[c]
    t = 'other'
    if c.startswith('parking') or 'gasstation' in c or 'fuel' in c or 'chargingstation' in c:
        t = 'other'
    elif c.endswith(('bar', 'pub')) and not c.endswith('barbar'):
        t = 'food'
    else:
        for name, kws in _KW:
            if any(k in c for k in kws):
                t = name
                break
    _tcache[c] = t
    return t


# (11/18 233기) 커버리지 분모 '일정에 들어갈 수 있는 장소' — 앱 일정 생성기(trip_gen_v2)가 쓰는 칸: 명소·식사(식당·카페)·숙소·교통 거점(공항·역·터미널·여객선).
#   벤치·가로등·버스 정류장·주차장·주유소 같은 '그 밖'·'정류장'은 빼고 센다. 전체 기준 수치도 함께 남긴다(places).
HUB_KW = ('airport', 'station', 'terminal', 'ferry', 'pier', 'harbour', 'harbor', 'railway', 'metro', 'subway')
NOT_HUB = ('stop', 'platform', 'taxistand', 'gas', 'fuel', 'charging', 'police', 'fire', 'radio', 'power', 'weather', 'parking', 'bicycle', 'bike')
_icache = {}


def itin_of(cat):
    """일정에 들어갈 수 있는 장소인가(명소·식당·숙소 전부 + 교통은 거점만)."""
    c = (cat or '').lower()
    if c not in _icache:
        t = type_of(c)
        _icache[c] = t in ('sight', 'food', 'lodging') or (t == 'transport' and any(k in c for k in HUB_KW) and not any(k in c for k in NOT_HUB))
    return _icache[c]


def _blank():
    return {'n': 0, **{a: 0 for a in ATTRS}}


def _rblank():
    return {'n': 0, 'rec': 0, 'rec2': 0, 'added': 0}


def rec_signals(v):
    """rec-1 행 [점수, 교차, conf, sitelinks, 위키, 관광공사, 언급, 순위] → 근거로 쓴 독립 신호 수(scripts/rec_index.n_signals 와 같음)."""
    return (v[1] >= 2) + (v[2] is not None) + bool(v[3]) + bool(v[4]) + bool(v[5]) + (v[6] is not None)


def rec_ids(paths):
    """추천 지수 층(rec-1) → {'any': 점수 있는 장소 id, 'two': 근거 2개 이상, '_files': {파일 수, 행}}. 파일이 없으면 None."""
    a, t, files, seen = set(), set(), {'files': 0, 'rows': 0}, 0
    for f in paths:
        if not os.path.basename(f).startswith('rec-'):
            continue
        try:
            j = _load(f)
        except Exception:
            continue
        if j.get('schema') != 'rec-1' or not isinstance(j.get('rows'), dict):
            continue
        seen += 1; files['files'] += 1
        for rid, v in j['rows'].items():
            if isinstance(v, list) and len(v) >= 8 and isinstance(v[0], int):
                files['rows'] += 1; a.add(rid)
                if rec_signals(v) >= 2:
                    t.add(rid)
    return {'any': a, 'two': t, '_files': files} if seen else None


def add_rec_layers(m, d):
    """rec 폴더의 col_ovtadd_*(새로 더한 일정 장소, collect-1)를 장소로 함께 센다."""
    if not d or not os.path.isdir(d):
        return
    m.adding = True
    for f in sorted(os.listdir(d)):
        if f.startswith('col_ovtadd_') and f.endswith('.json.gz'):
            m.add_layer(_load(os.path.join(d, f)))
    m.adding = False


def _dblank():
    return {'n': 0, 'any': 0, **{k: 0 for k in DESC_SLOTS}}


class Meter:
    def __init__(self, cities=None, hours_ids=(), photo_ids=(), desc=None, names=None, rec=None):
        self.cc, self.typ, self.city, self.src = {}, {t: _blank() for t in TYPES}, {}, {}
        self.cc_typ = {}
        self.cities = cities or {}
        self.h_ids, self.p_ids = set(hours_ids), set(photo_ids)
        self.names = names or {}  # {칸: 그 언어 이름이 있는 장소 id set} — 이름 층(names-2·names-1)
        self.files = 0
        self.desc = desc  # {칸: 장소 id set} 또는 None(장소 소개 층을 읽지 않음)
        self.d_all, self.d_typ, self.d_city = _dblank(), {t: _dblank() for t in TYPES}, {}
        self.itin, self.d_itin = _blank(), _dblank()  # 일정 장소 기준(itin_of)
        self.rec = rec  # (11/18 245기) 추천 지수 층(rec-1) {'any': id set, 'two': 근거 2개 이상 id set, '_files': …} 또는 None
        self.r_itin, self.r_all = _rblank(), _rblank()
        self.adding = False  # col_ovtadd_* (rec_index 가 더한 Overture 일정 장소) 를 읽는 중
        self.city_itin = {}  # (11/18 252기) 도시 × 일정 장소 기준: 속성 칸 + 소개(1개 언어+) desc + 추천 지수 rec·rec2

    def _desc(self, keys, groups):
        hit = [any(k in self.desc[s] for k in keys if k) for s in DESC_SLOTS]
        for g in groups:
            g['n'] += 1; g['any'] += any(hit)
            for s, v in zip(DESC_SLOTS, hit):
                g[s] += v

    def add_layer(self, j):
        """collect-1 장소 층 한 파일."""
        if j.get('schema') != 'collect-1' or j.get('dataset') != 'places':
            return 0
        self.files += 1
        cc, cats, n = j.get('cc') or '??', j.get('cats') or [], len(j['id'])
        tp = [type_of(c) for c in cats]
        ip = [itin_of(c) for c in cats]
        col = lambda k: j.get(k) or [None] * n
        ko, en, ja, zh, h, tel, url, tr, src, wid, ids = (col(k) for k in ('ko', 'en', 'ja', 'zh', 'h', 'tel', 'url', 'tr', 'src', 'wid', 'id'))
        boxes = [(nm, b) for nm, b in self.cities.items()]
        la = lo = 0
        bcc, bct = self.cc.setdefault(cc, _blank()), self.cc_typ.setdefault(cc, {t: 0 for t in TYPES})
        nm_sets = [self.names.get(k, ()) for k in NAME_SLOTS]
        for i in range(n):
            la += j['dla'][i]; lo += j['dlo'][i]
            t = tp[j['c'][i]] if 0 <= j['c'][i] < len(tp) else 'other'
            it = ip[j['c'][i]] if 0 <= j['c'][i] < len(ip) else False
            keys = (ids[i], wid[i]) if wid[i] else (ids[i],)
            zk = PN.zh_generic(PN.name_ok(zh[i])) if zh[i] else None
            own = (bool(PN.name_ok(ko[i])), bool(PN.name_ok(en[i])), bool(PN.name_ok(ja[i])), zk == 'zh_Hans', zk == 'zh_Hant')
            f = tuple(o or any(k in sset for k in keys) for o, sset in zip(own, nm_sets)) + (
                bool(oh_ok(h[i])) or any(k in self.h_ids for k in keys), any(k in self.p_ids for k in keys), bool(tel[i]), bool(url[i]), (tr[i] or 1) >= 2)
            tg = [bcc, self.typ[t]] + ([self.itin] if it else [])
            ci = []
            if boxes:
                x, y = lo / 1e5, la / 1e5
                for nm, b in boxes:
                    if b[0] <= x <= b[2] and b[1] <= y <= b[3]:
                        tg.append(self.city.setdefault(nm, _blank()))
                        if it:
                            ci.append(self.city_itin.setdefault(nm, {**_blank(), 'desc': 0, 'rec': 0, 'rec2': 0}))
            if ci:
                tg += ci  # 속성 칸·장소 수는 아래 tg 고리가 센다
                dv = self.desc is not None and any(k in self.desc[sl] for sl in DESC_SLOTS for k in keys if k)
                rv = self.rec is not None and any(k in self.rec['any'] for k in keys if k)
                r2v = self.rec is not None and any(k in self.rec['two'] for k in keys if k)
                ph = f[ATTRS.index('photo')]
                for g in ci:
                    g['desc'] += dv; g['rec'] += rv; g['rec2'] += r2v
                    if t == 'sight':  # (270기) 관광·명소만 — 사진 분모·분자
                        g['sight_n'] = g.get('sight_n', 0) + 1; g['sight_photo'] = g.get('sight_photo', 0) + ph
            for g in tg:
                g['n'] += 1
                for a, v in zip(ATTRS, f):
                    g[a] += v
            bct[t] += 1
            if self.rec is not None:
                ra, r2 = any(k in self.rec['any'] for k in keys if k), any(k in self.rec['two'] for k in keys if k)
                for g in ([self.r_all, self.r_itin] if it else [self.r_all]):
                    g['n'] += 1; g['rec'] += ra; g['rec2'] += r2; g['added'] += self.adding
            if self.desc is not None:
                dg = [self.d_all, self.d_typ[t]] + ([self.d_itin] if it else [])
                if boxes:
                    x, y = lo / 1e5, la / 1e5
                    dg += [self.d_city.setdefault(nm, _dblank()) for nm, b in boxes if b[0] <= x <= b[2] and b[1] <= y <= b[3]]
                self._desc((ids[i], wid[i]), dg)
            for s in (src[i] or '').split('|'):
                if s:
                    self.src[s] = self.src.get(s, 0) + 1
        return n

    def result(self):
        tot = _blank()
        for b in self.cc.values():
            for k in tot:
                tot[k] += b[k]
        r = {'places': tot, 'itinerary': self.itin, 'by_type': self.typ, 'by_cc': self.cc, 'by_cc_type': self.cc_typ, 'by_city': self.city, 'by_city_itin': self.city_itin, 'by_source': self.src, 'layer_files': self.files,
             'side_files': {k: dict(v) for k, v in SIDE_FILES.items()}}
        if self.rec is not None:
            r['rec'] = {'itinerary': self.r_itin, 'all': self.r_all, 'files': self.rec.get('_files', {})}
        if self.desc is not None:
            r['desc'] = {'all': self.d_all, 'itinerary': self.d_itin, 'by_type': self.d_typ, 'by_city': self.d_city, 'files': self.desc.get('_files', {})}
        return r


def _load(p):
    with open(p, 'rb') as f:
        return json.loads(gzip.decompress(f.read()))


SIDE_FILES = {}  # 마지막 side_ids 가 읽은 층 파일 요약 {종류/출처: {'files', 'rows'}} — 보고서 0절


def side_ids(paths, names=None):
    """영업시간 층(hours-1)·사진 층(photos-1 등)·이름 층(names-2·names-1) → (영업시간 있는 장소 id, 사진 있는 장소 id).
    names(dict)를 주면 {칸: 이름 있는 장소 id set} 를 채운다."""
    h, p = set(), set()
    SIDE_FILES.clear()
    for f in paths:
        try:
            j = _load(f)
        except Exception:
            continue
        rows = j.get('rows')
        if not isinstance(rows, dict):
            continue
        sc = j.get('schema')
        key = f"{sc}/{j.get('src') or j.get('region') or ''}".rstrip('/')
        b = SIDE_FILES.setdefault(key, {'files': 0, 'rows': 0})
        b['files'] += 1; b['rows'] += len(rows)
        if sc == 'hours-1':
            h.update(k for k, v in rows.items() if isinstance(v, dict) and oh_ok(v.get('oh')))
        elif sc in ('photos-1', 'popularity-1', 'tourapi-1'):
            p.update(k for k, v in rows.items() if isinstance(v, dict) and (v.get('photo_url') or v.get('photo') or v.get('photos')))
        elif sc in ('names-2', 'names-1') and names is not None:
            for k, v in rows.items():
                if not isinstance(v, dict):
                    continue
                for slot in NAME_SLOTS:
                    if PN.name_ok(v.get(slot)):  # names-1 은 ko(표시 이름)·en 만 — ks(검색 열쇠)·rd(읽는 법)는 세지 않음
                        names.setdefault(slot, set()).add(k)
    return h, p


def desc_ids(paths):
    """장소 소개 층(desc-1) → {칸: 글이 있는 장소 id set, '_files': {출처: {'rows', 칸…}}}. 층 파일이 하나도 없으면 None."""
    out, files, seen = {k: set() for k in DESC_SLOTS}, {}, 0
    for f in paths:
        if not os.path.basename(f).startswith('desc-'):
            continue
        try:
            j = _load(f)
        except Exception:
            continue
        if j.get('schema') != 'desc-1' or not isinstance(j.get('rows'), dict):
            continue
        seen += 1
        b = files.setdefault(j.get('src') or '?', {'files': 0, 'rows': 0, **{k: 0 for k in DESC_SLOTS}})
        b['files'] += 1
        for rid, v in j['rows'].items():
            if not isinstance(v, dict):
                continue
            b['rows'] += 1
            for k in DESC_SLOTS:
                if v.get(k):
                    out[k].add(rid); b[k] += 1
    if not seen:
        return None
    out['_files'] = files
    return out


def measure_dir(d, cities):
    fs = sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith('.json.gz'))
    nm = {}
    h, p = side_ids([f for f in fs if not os.path.basename(f).startswith(('col_', 'desc-', 'rec-'))], nm)
    m = Meter(cities, h, p, desc_ids(fs), nm, rec_ids(fs))
    for f in fs:
        b = os.path.basename(f)
        if b.startswith('col_'):
            m.adding = b.startswith('col_ovtadd_')
            m.add_layer(_load(f))
    m.adding = False
    return m.result()


def measure_store(store, cities, side_dir=None, batch=40, desc_dir=None, rec_dir=None):
    latest = store.get_json('processed/latest.json', None)
    if not latest:
        return None
    nm = {}
    h, p = side_ids([os.path.join(side_dir, f) for f in sorted(os.listdir(side_dir))], nm) if side_dir and os.path.isdir(side_dir) else (set(), set())
    dd = desc_ids([os.path.join(desc_dir, f) for f in sorted(os.listdir(desc_dir))]) if desc_dir and os.path.isdir(desc_dir) else None
    rr = rec_ids([os.path.join(rec_dir, f) for f in sorted(os.listdir(rec_dir))]) if rec_dir and os.path.isdir(rec_dir) else None
    m = Meter(cities, h, p, dd, nm, rr)
    add_rec_layers(m, rec_dir)
    paths = latest.get('paths') or {f: f"processed/out/{latest['run']}/{f}" for f in latest.get('files', [])}
    keys = sorted(v for k, v in paths.items() if k.startswith('col_') and not k.startswith('col_info_'))
    for i in range(0, len(keys), batch):
        d = tempfile.mkdtemp(dir=os.environ.get('RUNNER_TEMP') or None)
        got = store.pull(keys[i:i + batch], d, strict=False)
        for k in keys[i:i + batch]:
            if k in got:
                m.add_layer(_load(got[k]))
        shutil.rmtree(d, ignore_errors=True)
    r = m.result()
    r['run'], r['pending'] = latest.get('run'), (latest.get('stats') or {}).get('pending')
    return r


def collect_stage(status, total_shards):
    """collect/status.json → 출처별 {조각 수, 전체 조각, 행, 실패 중}."""
    out = {}
    for sid, s in sorted((status.get('sources') or {}).items()):
        out[sid] = {'shards': s.get('shards', 0), 'shards_total': total_shards.get(sid), 'rows': s.get('rows_last', 0), 'failing': s.get('failing', 0)}
    return out


def total_shards():
    reg = json.load(open(os.path.join(ROOT, 'tools', 'world_regions.json'), encoding='utf-8'))
    n = sum(len(v['cc']) for v in reg.values())
    return {'overture_places': n, 'fsq_os_places': n}


def gaps(targets, collected, places):
    """(c) 분모: 공개 출처 합집합. 겹침을 모르므로 범위로 — 하한 = 가장 큰 출처 하나, 상한 = 출처 합. 총수 미확인 출처는 받은 행 수로 대신."""
    sizes = {}
    for t in targets['sources']:
        if t.get('layer') != 'main':
            continue
        n = t.get('count') or (collected.get(t['id']) or {}).get('rows') or 0
        if n:
            sizes[t['id']] = n
    lo, hi = (max(sizes.values()), sum(sizes.values())) if sizes else (0, 0)
    g = targets.get('google_reference') or {}
    pct = lambda a, b: round(100 * a / b, 1) if b else None
    raw = sum((collected.get(k) or {}).get('rows', 0) for k in sizes)
    return {'union_low': lo, 'union_high': hi, 'sizes': sizes, 'raw_rows': raw, 'raw_vs_union_high_pct': pct(raw, hi),
            'published': places, 'published_vs_union_low_pct': pct(places, lo) if places is not None else None,
            'published_vs_union_high_pct': pct(places, hi) if places is not None else None,
            'google_ref': g.get('count'), 'published_vs_google_ref_pct': pct(places, g.get('count')) if places is not None else None}


_PRI = {}


def priority_cities():
    """우선 도시 {도시 키: 순위(1부터)} — sources/priority_cities.json(한국인 해외·국내 상위 도시). 없으면 빈 dict."""
    if not _PRI:
        try:
            j = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'sources', 'priority_cities.json'), encoding='utf-8'))
            _PRI.update({c['key']: c['rank'] for c in j.get('cities', [])})
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return _PRI


def _pct(a, n):
    return f'{100 * a / n:.1f}%' if n else '-'


def to_md(rep, prev=None, top=25):
    L, g = [f"## 커버리지 {rep['date']}"], rep['gap']
    pl = (rep.get('layers') or {}).get('places')
    d = ''
    if prev and pl and (prev.get('layers') or {}).get('places'):
        d = f" (어제보다 {pl['n'] - prev['layers']['places']['n']:+,})"
    L += ['### 1. 목표 대비',
          f"- 공개 출처 합집합(분모): **{g['union_low']:,} ~ {g['union_high']:,}** (하한 = 가장 큰 출처, 상한 = 출처 합 — 겹침은 가공 뒤에야 안다)",
          f"- 받은 원본 행: **{g['raw_rows']:,}** = 상한 분모의 {g['raw_vs_union_high_pct']}%",
          (f"- 게시된 장소: **{g['published']:,}**{d} = 분모의 {g['published_vs_union_high_pct']}% ~ {g['published_vs_union_low_pct']}%" if g['published'] is not None
           else '- 게시된 장소: 미측정(층 파일을 읽지 못함)'),
          (f"- 구글 공개 문구(‘2억 곳 이상’, 2022-12) 대비 참고: {g['published_vs_google_ref_pct']}% — 속성·나라별은 **비교 불가**(공개 수치 없음)" if g['published'] is not None
           else '- 구글 공개 문구 대비: 게시 수 미측정'),
          '### 2. 출처별 수집', '| 출처 | 조각(받음/전체) | 받은 행 | 공개 총수 | 비율 | 실패 중 |', '|---|---|---|---|---|---|']
    tg = {t['id']: t for t in rep['targets']['sources']}
    for sid, s in rep['collect'].items():
        t = tg.get(sid, {})
        L.append(f"| {sid} | {s['shards']}/{s['shards_total'] or '-'} | {s['rows']:,} | {format(t['count'], ',') if t.get('count') else '미확인'} | {_pct(s['rows'], t.get('count'))} | {s['failing']} |")
    lay = rep.get('layers')
    if lay:
        P0, pv = lay['places'], ((prev or {}).get('layers') or {}).get('places') or {}
        dlt = lambda a: (f" (어제 {_pct(pv[a], pv['n'])})" if pv.get('n') and a in pv else '')
        I0 = lay.get('itinerary') or _blank()
        L += ['### 0. 이름 5개 언어·영업시간·사진 채움(그 언어로 적힌 원천 값만 — 원어 이름·번역·간체↔번체 변환 값은 세지 않음)',
              f"- **일정 장소 기준**(명소·식당·숙소·교통 거점 — 일정 생성기가 쓰는 범주, 벤치·정류장·주차장 등 제외): {I0['n']:,}곳 · 전체 기준: {P0['n']:,}곳",
              '| 칸 | 일정 장소 수 | 일정 기준 비율 | 전체 장소 수 | 전체 기준 비율 |', '|---|---|---|---|---|']
        L += [f"| {ATTR_KO[a]} | {I0[a]:,} | {_pct(I0[a], I0['n'])} | {P0[a]:,} | {_pct(P0[a], P0['n'])}{dlt(a)} |" for a in NAME_SLOTS + ('h', 'photo')]
        if lay.get('side_files'):
            L.append('- 읽은 층: ' + ', '.join(f"{k} 파일 {v['files']}·행 {v['rows']:,}" for k, v in sorted(lay['side_files'].items())))
        hdr = '| 장소 | ' + ' | '.join(ATTR_KO[a] for a in ATTRS) + ' |'
        row = lambda name, b: f"| {name} | {b['n']:,} | " + ' | '.join(_pct(b[a], b['n']) for a in ATTRS) + ' |'
        L += ['### 3. 종류별 장소 수·속성 채움', '| 종류 ' + hdr, '|---' * (len(ATTRS) + 2) + '|', row('전체', lay['places'])] + [row(TYPE_KO[t], lay['by_type'][t]) for t in TYPES]
        L += [f'### 4. 나라별(위 {top}개 / 전체 {len(lay["by_cc"])}개 나라)', '| 나라 ' + hdr, '|---' * (len(ATTRS) + 2) + '|']
        L += [row(cc, b) for cc, b in sorted(lay['by_cc'].items(), key=lambda kv: -kv[1]['n'])[:top]]
        L += ['### 5. 도시별', '| 도시 ' + hdr, '|---' * (len(ATTRS) + 2) + '|'] + [row(c, b) for c, b in sorted(lay['by_city'].items(), key=lambda kv: -kv[1]['n'])]
        ci = lay.get('by_city_itin') or {}
        if ci:
            pri = priority_cities()
            ck = NAME_SLOTS + ('h', 'photo', 'url', 'tel', 'desc', 'rec', 'rec2')
            ko = {**ATTR_KO, 'desc': '소개', 'rec': '추천 지수', 'rec2': '지수 근거 2+'}
            order = sorted(ci, key=lambda c: (pri.get(c, 999), -ci[c]['n']))
            L += ['### 5-2. 도시별 — 일정 장소 기준(명소·식당·카페·숙소·교통 거점, 우선 도시 순 — sources/priority_cities.json)',
                  '| 순위 | 도시 | 일정 장소 | ' + ' | '.join(ko[a] for a in ck) + ' |', '|---' * (len(ck) + 3) + '|']
            L[-1] = L[-1] + '---|---|'
            L[-2] = L[-2] + ' 관광·명소 수 | 관광·명소 사진 |'
            L += [f"| {pri.get(c, '-')} | {c} | {ci[c]['n']:,} | " + ' | '.join(_pct(ci[c].get(a, 0), ci[c]['n']) for a in ck)
                  + f" | {ci[c].get('sight_n', 0):,} | {_pct(ci[c].get('sight_photo', 0), ci[c].get('sight_n', 0))} |" for c in order]
        L += ['### 6. 출처별 게시 행', ', '.join(f'{k} {v:,}' for k, v in sorted(lay['by_source'].items(), key=lambda kv: -kv[1])[:15])]
        dsc = lay.get('desc')
        if dsc:
            dh = '| | 장소 | 소개 1개 언어 이상 | ' + ' | '.join(DESC_SLOTS) + ' |'
            drow = lambda name, b: f"| {name} | {b['n']:,} | {_pct(b['any'], b['n'])} | " + ' | '.join(_pct(b[k], b['n']) for k in DESC_SLOTS) + ' |'
            L += ['### 7. 장소 소개(언어별 커버리지 — 수집 층 장소 중 그 언어 소개가 있는 비율, 다른 언어로 채우지 않음)', dh, '|---' * (len(DESC_SLOTS) + 3) + '|',
                  drow('일정 장소', dsc.get('itinerary') or _dblank()), drow('전체', dsc['all'])] + [drow(TYPE_KO[t], dsc['by_type'][t]) for t in TYPES]
            if dsc.get('by_city'):
                L += ['', dh.replace('| |', '| 도시 |', 1), '|---' * (len(DESC_SLOTS) + 3) + '|'] + [drow(c, b) for c, b in sorted(dsc['by_city'].items(), key=lambda kv: -kv[1]['n'])]
            if dsc.get('files'):
                L += ['', '| 소개 층 출처 | 파일 | 행(앱 타일 id 포함) | ' + ' | '.join(DESC_SLOTS) + ' |', '|---' * (len(DESC_SLOTS) + 3) + '|']
                L += [f"| {k} | {v['files']} | {v['rows']:,} | " + ' | '.join(f'{v[s]:,}' for s in DESC_SLOTS) + ' |' for k, v in sorted(dsc['files'].items())]
        rc = lay.get('rec')
        if rc:
            I, A = rc['itinerary'], rc['all']
            pr = (((prev or {}).get('layers') or {}).get('rec') or {}).get('itinerary') or {}
            L += ['### 9. 트립루티 추천 지수 채움률(일정 장소 기준 — 계산식 docs/RECOMMEND_INDEX.md, 다른 서비스 평점 아님)',
                  '| 기준 | 장소 | 지수 있음 | 근거 2개 이상 | 새로 더한 일정 장소(Overture) |', '|---|---|---|---|---|',
                  f"| 일정 장소 | {I['n']:,} | {_pct(I['rec'], I['n'])}" + (f" (어제 {_pct(pr['rec'], pr['n'])})" if pr.get('n') else '') +
                  f" | {_pct(I['rec2'], I['n'])} | {I['added']:,} |",
                  f"| 전체 | {A['n']:,} | {_pct(A['rec'], A['n'])} | {_pct(A['rec2'], A['n'])} | {A['added']:,} |",
                  f"- 읽은 지수 파일 {rc['files'].get('files', 0)} · 행 {rc['files'].get('rows', 0):,}"]
        if lay.get('pending'):
            L.append(f"- 가공이 다음 실행으로 넘긴 일: 원본 파일 {lay['pending'].get('raw_files', 0)}개 · 나라 {len(lay['pending'].get('countries', []))}개")
    for k, v in (rep.get('storage') or {}).items():
        L.append(f'- Storage {k}: {v}')
    return '\n'.join(L) + '\n'


def build(status=None, layers=None, targets=None, date=None, storage=None):
    import datetime
    targets = targets or json.load(open(os.path.join(ROOT, 'sources', 'coverage_targets.json'), encoding='utf-8'))
    col = collect_stage(status or {}, total_shards())
    return {'schema': 'coverage-1', 'date': date or datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d'), 'status_updated': (status or {}).get('updated'),
            'targets': targets, 'collect': col, 'layers': layers, 'gap': gaps(targets, col, layers['places']['n'] if layers else None), 'storage': storage or {}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--status', default=os.path.join(ROOT, 'collect', 'status.json'))
    ap.add_argument('--layers', default=''); ap.add_argument('--store', default=''); ap.add_argument('--side', default='', help='hours·photos 층 파일 폴더(--store 와 함께)')
    ap.add_argument('--desc', default='', help='장소 소개 층(desc-*.json.gz) 폴더(--store 와 함께)')
    ap.add_argument('--rec', default='', help='추천 지수 층(rec-*.json.gz)·새 장소(col_ovtadd_*) 폴더(--store 와 함께)')
    ap.add_argument('--json', default=''); ap.add_argument('--md', default=''); ap.add_argument('--prev', default=''); ap.add_argument('--storage', default='', help='{"이름": "크기"} JSON 파일')
    a = ap.parse_args()
    cities = json.load(open(os.path.join(ROOT, 'sources', 'coverage_cities.json'), encoding='utf-8'))['cities']
    status = json.load(open(a.status, encoding='utf-8')) if a.status and os.path.exists(a.status) else {}
    lay = None
    if a.layers:
        lay = measure_dir(a.layers, cities)
    elif a.store:
        sys.path.insert(0, os.path.join(HERE, 'collect'))
        from common import Store
        lay = measure_store(Store(a.store), cities, a.side or None, desc_dir=a.desc or None, rec_dir=a.rec or None)
    rep = build(status, lay, storage=json.load(open(a.storage, encoding='utf-8')) if a.storage and os.path.exists(a.storage) else None)
    prev = json.load(open(a.prev, encoding='utf-8')) if a.prev and os.path.exists(a.prev) else None
    md = to_md(rep, prev)
    if a.json:
        json.dump(rep, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, sort_keys=True)
    if a.md:
        open(a.md, 'w', encoding='utf-8').write(md)
    print(md)
    return 0


if __name__ == '__main__':
    sys.exit(main())
