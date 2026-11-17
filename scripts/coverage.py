#!/usr/bin/env python3
"""커버리지 측정 — '구글 지도 수준' 목표를 숫자로 잰다(docs/COVERAGE.md). 구글 데이터는 받지도 저장하지도 않는다.

  python3 scripts/coverage.py --status collect/status.json                       # 수집 단계(저장소에 있는 요약만으로)
  python3 scripts/coverage.py --layers <층 파일 폴더> [--status …]               # 게시 층 파일(col_*.json.gz, hours·photos 층)을 직접 읽어
  python3 scripts/coverage.py --store gs://<bucket>/collect/v1 [--status …]      # Storage 의 최신 가공 판을 40개씩 받아 읽고 지움
     --json out.json --md out.md --prev 어제.json

재는 것: (a) 출처별 받은 조각·행 ÷ 공개 총수 (b) 종류(명소·식당·숙소·교통)·나라·도시별 장소 수와 속성 채움 비율
(c) 게시 장소 수 ÷ 공개 출처 합집합(하한~상한) + 구글 공개 문구 대비 참고값.
"""
import argparse, gzip, json, os, shutil, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
TYPES = ('sight', 'food', 'lodging', 'transport', 'other')
TYPE_KO = {'sight': '명소', 'food': '식당·카페', 'lodging': '숙소', 'transport': '교통', 'other': '그 밖'}
ATTRS = ('ko', 'en', 'ja', 'zh', 'h', 'photo', 'tel', 'url', 'multi')
ATTR_KO = {'ko': '이름 한국어', 'en': '이름 영어', 'ja': '이름 일본어', 'zh': '이름 중국어', 'h': '영업시간', 'photo': '사진', 'tel': '전화', 'url': '웹', 'multi': '출처 2곳 이상'}
_KW = (('lodging', ('hotel', 'hostel', 'motel', 'lodging', 'resort', 'guesthouse', 'ryokan', 'bedbreakfast', 'bedandbreakfast', 'vacationrental', 'campground', 'accommodation')),
       ('transport', ('station', 'airport', 'busstop', 'terminal', 'ferry', 'metro', 'transport', 'tramstop', 'platform', 'pier', 'railway', 'busline', 'taxistand')),
       ('sight', ('sight', 'museum', 'landmark', 'monument', 'park', 'temple', 'shrine', 'church', 'cathedral', 'mosque', 'castle', 'palace', 'gallery', 'viewpoint', 'scenic',
                  'beach', 'garden', 'zoo', 'aquarium', 'attraction', 'historic', 'memorial', 'plaza', 'themepark', 'activity', 'event', 'waterfall', 'mountain', 'tower')),
       ('food', ('restaurant', 'food', 'cafe', 'coffee', 'bakery', 'diner', 'bistro', 'izakaya', 'noodle', 'ramen', 'sushi', 'pizza', 'burger', 'dessert', 'teahouse', 'tearoom',
                 'steakhouse', 'brewery', 'icecream', 'bbq', 'eatery')))
_tcache = {}


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


def _blank():
    return {'n': 0, **{a: 0 for a in ATTRS}}


class Meter:
    def __init__(self, cities=None, hours_ids=(), photo_ids=()):
        self.cc, self.typ, self.city, self.src = {}, {t: _blank() for t in TYPES}, {}, {}
        self.cc_typ = {}
        self.cities = cities or {}
        self.h_ids, self.p_ids = set(hours_ids), set(photo_ids)
        self.files = 0

    def add_layer(self, j):
        """collect-1 장소 층 한 파일."""
        if j.get('schema') != 'collect-1' or j.get('dataset') != 'places':
            return 0
        self.files += 1
        cc, cats, n = j.get('cc') or '??', j.get('cats') or [], len(j['id'])
        tp = [type_of(c) for c in cats]
        col = lambda k: j.get(k) or [None] * n
        ko, en, ja, zh, h, tel, url, tr, src, wid, ids = (col(k) for k in ('ko', 'en', 'ja', 'zh', 'h', 'tel', 'url', 'tr', 'src', 'wid', 'id'))
        boxes = [(nm, b) for nm, b in self.cities.items()]
        la = lo = 0
        bcc, bct = self.cc.setdefault(cc, _blank()), self.cc_typ.setdefault(cc, {t: 0 for t in TYPES})
        for i in range(n):
            la += j['dla'][i]; lo += j['dlo'][i]
            t = tp[j['c'][i]] if 0 <= j['c'][i] < len(tp) else 'other'
            f = (bool(ko[i]), bool(en[i]), bool(ja[i]), bool(zh[i]), bool(h[i]) or ids[i] in self.h_ids or (wid[i] in self.h_ids if wid[i] else False),
                 ids[i] in self.p_ids or (wid[i] in self.p_ids if wid[i] else False), bool(tel[i]), bool(url[i]), (tr[i] or 1) >= 2)
            tg = [bcc, self.typ[t]]
            if boxes:
                x, y = lo / 1e5, la / 1e5
                for nm, b in boxes:
                    if b[0] <= x <= b[2] and b[1] <= y <= b[3]:
                        tg.append(self.city.setdefault(nm, _blank()))
            for g in tg:
                g['n'] += 1
                for a, v in zip(ATTRS, f):
                    g[a] += v
            bct[t] += 1
            for s in (src[i] or '').split('|'):
                if s:
                    self.src[s] = self.src.get(s, 0) + 1
        return n

    def result(self):
        tot = _blank()
        for b in self.cc.values():
            for k in tot:
                tot[k] += b[k]
        return {'places': tot, 'by_type': self.typ, 'by_cc': self.cc, 'by_cc_type': self.cc_typ, 'by_city': self.city, 'by_source': self.src, 'layer_files': self.files}


def _load(p):
    with open(p, 'rb') as f:
        return json.loads(gzip.decompress(f.read()))


def side_ids(paths):
    """영업시간 층(hours-1)·사진 층(photos-1) → (영업시간 있는 장소 id, 사진 있는 장소 id)."""
    h, p = set(), set()
    for f in paths:
        try:
            j = _load(f)
        except Exception:
            continue
        rows = j.get('rows')
        if not isinstance(rows, dict):
            continue
        if j.get('schema') == 'hours-1':
            h.update(k for k, v in rows.items() if isinstance(v, dict) and v.get('oh'))
        elif j.get('schema') in ('photos-1', 'popularity-1', 'tourapi-1'):
            p.update(k for k, v in rows.items() if isinstance(v, dict) and (v.get('photo') or v.get('photos')))
    return h, p


def measure_dir(d, cities):
    fs = sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith('.json.gz'))
    h, p = side_ids([f for f in fs if not os.path.basename(f).startswith('col_')])
    m = Meter(cities, h, p)
    for f in fs:
        if os.path.basename(f).startswith('col_'):
            m.add_layer(_load(f))
    return m.result()


def measure_store(store, cities, side_dir=None, batch=40):
    latest = store.get_json('processed/latest.json', None)
    if not latest:
        return None
    h, p = side_ids([os.path.join(side_dir, f) for f in os.listdir(side_dir)]) if side_dir and os.path.isdir(side_dir) else (set(), set())
    m = Meter(cities, h, p)
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
        hdr = '| 장소 | ' + ' | '.join(ATTR_KO[a] for a in ATTRS) + ' |'
        row = lambda name, b: f"| {name} | {b['n']:,} | " + ' | '.join(_pct(b[a], b['n']) for a in ATTRS) + ' |'
        L += ['### 3. 종류별 장소 수·속성 채움', '| 종류 ' + hdr, '|---' * (len(ATTRS) + 2) + '|', row('전체', lay['places'])] + [row(TYPE_KO[t], lay['by_type'][t]) for t in TYPES]
        L += [f'### 4. 나라별(위 {top}개 / 전체 {len(lay["by_cc"])}개 나라)', '| 나라 ' + hdr, '|---' * (len(ATTRS) + 2) + '|']
        L += [row(cc, b) for cc, b in sorted(lay['by_cc'].items(), key=lambda kv: -kv[1]['n'])[:top]]
        L += ['### 5. 도시별', '| 도시 ' + hdr, '|---' * (len(ATTRS) + 2) + '|'] + [row(c, b) for c, b in sorted(lay['by_city'].items(), key=lambda kv: -kv[1]['n'])]
        L += ['### 6. 출처별 게시 행', ', '.join(f'{k} {v:,}' for k, v in sorted(lay['by_source'].items(), key=lambda kv: -kv[1])[:15])]
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
        lay = measure_store(Store(a.store), cities, a.side or None)
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
