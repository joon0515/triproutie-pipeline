"""하루 숫자 표본 → 분포(중앙값·분위수·표본 수) → 생성기가 읽는 일정 모델 모양(itineraryModel)의 덧대기(patch).

담는 것은 숫자뿐이다. 영상·글의 ID·주소·채널·제목·장소 이름·문장은 어디에도 넣지 않는다(tests/test_patterns.py 가 산출물을 검사).
"""
import datetime

MIN_N = 8          # 이 표본 수보다 적으면 모델에 덧대지 않는다(기존 값 유지)
TYPE_MIN_DAYS = 3  # 여행 유형별 묶음은 날 표본이 이만큼 있어야 낸다


def quant(vals, p):
    v = sorted(vals)
    if not v:
        return None
    x = (len(v) - 1) * p
    lo, hi = int(x), min(int(x) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (x - lo)


def hhmm(m):
    m = int(round(min(m, 23 * 60 + 59)))
    return f'{m // 60:02d}:{m % 60:02d}'


def dist(vals, as_time=False):
    """모델과 같은 꼴: 표본 3개 미만이면 중앙값·n 만."""
    if not vals:
        return None
    f = hhmm if as_time else (lambda x: int(round(x)))
    d = {'median': f(quant(vals, .5)), 'n': len(vals)}
    if len(vals) >= 3:
        d = {'p25': f(quant(vals, .25)), 'median': d['median'], 'p75': f(quant(vals, .75)), 'n': len(vals)}
    return d


class Bucket:
    def __init__(self):
        self.docs = 0
        self.v = {k: [] for k in ('day_start', 'day_end', 'stops_per_day', 'gap_min', 'stay_min', 'lunch', 'dinner', 'breakfast', 'first_day_stops', 'last_day_stops')}
        self.cats, self.days = {}, 0

    def add_doc(self, days):
        self.docs += 1
        for d in days:
            self.days += 1
            first, last = d['of'] >= 2 and d['idx'] == 1, d['of'] >= 2 and d['idx'] == d['of']
            if first:
                self.v['first_day_stops'].append(d['stops'])
            elif last:
                self.v['last_day_stops'].append(d['stops'])
            mid = not first and not last  # 모델과 같이 '가운데 날'(하루짜리 포함)만 하루 창·장소 수에 쓴다
            if mid and (d.get('full') or not d['clock']):
                self.v['stops_per_day'].append(d['stops'])
            if d['clock']:
                if mid and d.get('full'):
                    self.v['day_start'].append(d['start']); self.v['day_end'].append(d['end'])
                self.v['gap_min'] += d['gaps']; self.v['stay_min'] += d.get('stays', [])
                for k in ('lunch', 'dinner', 'breakfast'):
                    if k in d:
                        self.v[k].append(d[k])
            for c, n in d['cats'].items():
                self.cats[c] = self.cats.get(c, 0) + n

    def out(self):
        o = {'n_docs': self.docs, 'n_days': self.days}
        for k, vals in self.v.items():
            x = dist(vals, as_time=k in ('day_start', 'day_end', 'lunch', 'dinner', 'breakfast'))
            if x:
                o[k] = x
        tot = sum(self.cats.values())
        if tot:
            o['cat_share'] = {c: round(n / tot, 3) for c, n in sorted(self.cats.items())}
        return o


def aggregate(samples, license_class, attribution, sources):
    """samples = [{'city','cc','type','days'}] → 분포 JSON. 도시·나라·전체 × (all + 여행 유형)."""
    g = {}

    def b(level, key, typ):
        return g.setdefault((level, key, typ), Bucket())
    for s in samples:
        for typ in {'all', s.get('type') or 'any'} - {'any'}:
            b('global', '', typ).add_doc(s['days'])
            if s.get('cc'):
                b('cc', s['cc'], typ).add_doc(s['days'])
            if s.get('city'):
                b('city', s['city'], typ).add_doc(s['days'])
    groups = {'global': {}, 'cc': {}, 'city': {}}
    for (level, key, typ), bk in sorted(g.items()):
        if typ != 'all' and bk.days < TYPE_MIN_DAYS:
            continue
        tgt = groups['global'] if level == 'global' else groups[level].setdefault(key, {})
        tgt[typ] = bk.out()
    return {'schema': 'patterns-1', 'built': datetime.date.today().isoformat(), 'license_class': license_class, 'attribution': attribution,
            'sources': sources, 'note': '자체 분석 통계 — 숫자 분포만. 글·영상의 주소·ID·제목·장소 이름·문장은 담지 않는다.', 'groups': groups}


def _ok(d, min_n):
    return d if d and d.get('n', 0) >= min_n and 'p25' in d else None


def _stops_rule(d):
    """모델 규칙(11/8 결정): relaxed>=3, packed<=min(normal+2, 9)."""
    normal = d['median']
    return {'relaxed': max(3, min(d['p25'], normal)), 'normal': max(3, normal), 'packed': min(max(d['p75'], normal), max(3, normal) + 2, 9), 'n': d['n']}


def to_model_patch(pat, min_n=MIN_N):
    """분포 JSON → 일정 모델과 같은 자리(global·regions·cities.*.generator_rules)의 덧대기. 표본이 min_n 미만인 칸은 아예 넣지 않는다."""
    src = 'patterns' if pat.get('license_class') == 'clean' else 'patterns_' + pat.get('license_class', 'x')
    tag = lambda d: dict(d, src=src)
    out = {'_meta': {'source': '자체 분석 통계(scripts/patterns)', 'built': pat.get('built'), 'license_class': pat.get('license_class'),
                     'attribution': pat.get('attribution'), 'min_n': min_n}, 'global': {}, 'regions': {}, 'cities': {}}
    a = pat['groups']['global'].get('all', {})
    for mk, pk in (('first_stop', 'day_start'), ('last_stop', 'day_end'), ('stops_per_day', 'stops_per_day')):
        if _ok(a.get(pk), min_n):
            out['global'][mk] = tag(a[pk])
    meals = {k: tag(a[k]) for k in ('lunch', 'dinner', 'breakfast') if _ok(a.get(k), min_n)}
    if meals:
        out['global']['meals'] = meals
    for cc, t in pat['groups']['cc'].items():
        a = t.get('all', {})
        r = {k: tag(a[k]) for k in ('day_start', 'day_end', 'stops_per_day', 'lunch', 'dinner', 'first_day_stops', 'last_day_stops') if _ok(a.get(k), min_n)}
        if r:
            out['regions'][cc] = r
    for city, t in pat['groups']['city'].items():
        a, r = t.get('all', {}), {}
        for mk, pk in (('day_start', 'day_start'), ('day_end_last_stop', 'day_end'), ('lunch_window', 'lunch'), ('dinner_window', 'dinner')):
            if _ok(a.get(pk), min_n):
                r[mk] = tag(a[pk])
        if _ok(a.get('stops_per_day'), min_n):
            r['stops_per_day'] = tag(_stops_rule(a['stops_per_day']))
        for mk, pk in (('first_day', 'first_day_stops'), ('last_day', 'last_day_stops')):
            if _ok(a.get(pk), min_n):
                r[mk] = {'stops': tag(a[pk])}
        if r:
            out['cities'][city] = {'generator_rules': r}
    return out


def _is_leaf(d):
    return isinstance(d, dict) and 'n' in d and not any(isinstance(v, dict) for v in d.values())


def apply_patch(model, patch, min_n=MIN_N):
    """모델(dict, 그 자리에서 고침)에 덧대기를 얹는다. 칸마다: 새 표본이 min_n 이상이고, 기존 값이 '지역 값 빌려 쓰기'(src=region_*)이거나
    기존 표본 수보다 적지 않을 때만 바꾼다. 그 밖에는 기존 값 유지. 돌려줌 = 바뀐 칸 경로 목록."""
    changed = []

    def walk(dst, src, path):
        for k, v in src.items():
            if _is_leaf(v):
                old = dst.get(k)
                borrowed = isinstance(old, dict) and str(old.get('src', '')).startswith('region')
                if v['n'] >= min_n and (not isinstance(old, dict) or borrowed or v['n'] >= old.get('n', 0)):
                    dst[k] = v
                    changed.append('/'.join(path + [k]))
            elif isinstance(v, dict):
                if not isinstance(dst.get(k), dict):
                    dst[k] = {}
                walk(dst[k], v, path + [k])
    for top in ('global', 'regions', 'cities'):
        walk(model.setdefault(top, {}), patch.get(top, {}), [top])
    if changed:
        model.setdefault('_meta', {})['patterns'] = patch.get('_meta')
    return changed
