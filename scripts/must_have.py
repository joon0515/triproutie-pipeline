#!/usr/bin/env python3
"""꼭 있어야 할 명소 점검·보충 — 우선 도시 38곳. (2026-11-20, 270기)

    python3 scripts/must_have.py build --store gs://<b>/collect/v1 --bucket <b> [--top 30] [--dry-run]
    python3 scripts/must_have.py build --store <로컬> --tiles <w-*.json.gz 폴더> --kto <kto_*.json.gz 폴더> --out <폴더>   # 시험

원인(270기 diag-top·추적):
  ① TourAPI(tourapi_daily)는 관광지(12)·문화시설(14)·레포츠(28) 등을 전부 받지만(kto_<시도>.json.gz), 그 행은 추천 지수의 '등재 여부' 신호(rec_index)와
     사진·소개 붙이기에만 쓰였고 일정용 장소 층에 싣는 단계가 없었다. 그래서 Overture·FSQ·OSM 에 한국어 이름으로 없는 명소(성산일출봉 등)는 앱 장소에 없다.
  ② 해외도 같은 꼴 — Wikidata 의 큰 명소가 원천 장소에 없거나 다른 이름이면 빠진다. 점검 기준이 없었다.
이 단계는:
  1. 도시마다 '꼭 있어야 할 명소' = Wikidata 덤프(processed/photos/geo_items.json.gz)에서 도시 상자 안 좌표+P18 항목 중 행정구역·마을·역·강·회사·학교·공항
     (P1082·P81·P403·P885·P452·P2196·P238 이 있는 항목)을 뺀 sitelinks 상위 30.
  2. 있음 = 앱 타일 행 중 그 QID 를 id 로 갖거나, 150m 안에서 이름 닮음 0.8 이상(photo_pri.name_sim — 라벨·별칭 20개 언어)인 행이 있음.
  3. 보충: ⓐ 한국 도시 상자 안 TourAPI 관광지(12)·문화시설(14)·레포츠(28) 중 앱 타일에 없는 것(200m·이름) ⓑ 빠진 Wikidata 명소(ⓐ 와 겹치면 하나만).
     → layers_extra/col_mustadd_<cc>-NN.json.gz (schema collect-1, 행마다 lic·src) + 사진 photos-must-1.json.gz(TourAPI 공공누리 1·3유형 · Commons 자유+저작자).
  4. 보고: 도시별 꼭 있어야 할 명소 수·타일에 있음·보충·보충 뒤 누락, 빠졌던 명소 이름(Wikidata 라벨, CC0) — 이름은 이 공개 명소 목록만 낸다.
같은 입력이면 같은 파일(이번에 만들지 않은 col_mustadd_*·photos-must-* 는 지운다). 키를 쓰지 않는다(이미 받은 덤프·TourAPI 층만 읽음).
"""
import argparse, glob, gzip, json, os, re, shutil, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, 'collect')); sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import photo_pri as PP  # noqa: E402
import place_names as PN  # noqa: E402
from common import Store, today  # noqa: E402

TOP = 30
KR_BOX = [124.5, 33.0, 131.0, 38.7]
PROBES = ['경복궁', '성산일출봉', '창덕궁', '해운대해수욕장', '남산서울타워', '감천문화마을', '한라산', '우도', '불국사', '해동용궁사']  # (270기) 대표 명소가 실제로 들어갔는지 행으로 확인
PRESENT_M = 150.0
KTO_DUP_M = 200.0
KTO_CAT = {'12': 'tourist_attraction', '14': 'museum', '28': 'amusement_park'}
HANGUL = re.compile('[가-힣]')
KANA = re.compile('[぀-ヿ]')
CJK = re.compile('[一-鿿]')


def log(m):
    print('[must-have] ' + m, flush=True)


class Index:
    """앱 타일 행 이름·좌표 격자(0.002도) + QID 행."""

    def __init__(self, tiles, bbox):
        self.grid, self.qids, self.cc = {}, set(), {}
        for d in tiles:
            ids, la_, lo_ = d.get('id') or [], d.get('_lat') or [], d.get('_lon') or []
            cols = [d[k] for k in ('nm', 'ko', 'en', 'ja', 'zh') if isinstance(d.get(k), list)]
            for i in range(min(len(ids), len(la_), len(lo_))):
                la, lo = la_[i], lo_[i]
                if not (bbox[0] - 0.02 <= lo <= bbox[2] + 0.02 and bbox[1] - 0.02 <= la <= bbox[3] + 0.02):
                    continue
                m = PP.QID_IN_ID.search(ids[i]) if isinstance(ids[i], str) else None
                if m:
                    self.qids.add(m.group(1))
                ns = {PP.geo_norm(c[i]) for c in cols if i < len(c)} - {''}
                if ns:
                    self.grid.setdefault((int(la * 500), int(lo * 500)), []).append((la, lo, ns))
                if d.get('cc'):
                    self.cc[d['cc']] = self.cc.get(d['cc'], 0) + 1

    def add(self, la, lo, names):
        ns = {PP.geo_norm(n) for n in names} - {''}
        if ns:
            self.grid.setdefault((int(la * 500), int(lo * 500)), []).append((la, lo, ns))

    def has(self, la, lo, names, max_m, sim=PP.GEO_SIM):
        mine = {PP.geo_norm(n) for n in names} - {''}
        if not mine:
            return False
        gi, gj = int(la * 500), int(lo * 500)
        r = max(1, int(max_m / 200) + 1)
        for di in range(-r, r + 1):
            for dj in range(-r, r + 1):
                for tla, tlo, ns in self.grid.get((gi + di, gj + dj), ()):
                    if PP._dist_m(la, lo, tla, tlo) <= max_m and any(PP.name_sim(a, b) >= sim for a in mine for b in ns):
                        return True
        return False

    def main_cc(self):
        return max(self.cc, key=self.cc.get) if self.cc else None


def must_list(items, top=TOP):
    """geo_items 한 도시 → 꼭 있어야 할 명소(장소 아님 표시 없음, sitelinks 상위 top)."""
    ok = [g for g in items or [] if isinstance(g, list) and len(g) >= 7 and not g[6]]
    return sorted(ok, key=lambda g: (-g[3], g[0]))[:top]


def lang_names(names):
    """다국어 이름 목록(언어 표시 없음) → ko·ja·zh·en 칸 어림(글자 종류로)."""
    out = {}
    for n in names:
        if HANGUL.search(n):
            out.setdefault('ko', n)
        elif KANA.search(n):
            out.setdefault('ja', n)
        elif CJK.search(n):
            out.setdefault('zh', n)
        elif n.isascii():
            out.setdefault('en', n)
    return out


def load_kto(paths):
    rows = []
    for fn in paths:
        try:
            j = json.loads(gzip.decompress(open(fn, 'rb').read()))
        except (OSError, ValueError):
            continue
        for rid, e in (j.get('rows') or {}).items():
            if isinstance(e, dict) and str(e.get('t')) in KTO_CAT and isinstance(e.get('lat'), (int, float)) and isinstance(e.get('lon'), (int, float)) and e.get('n'):
                rows.append({'cid': rid[4:] if rid.startswith('kto:') else rid, 'n': e['n'], 't': str(e['t']), 'lat': e['lat'], 'lon': e['lon'], 'photo': e.get('photo')})
    return rows


def city_run(c, tiles, geo_items, kto, ccache, top=TOP):
    """도시 하나 → (보충 행 [dict], 사진 {행 id: 사진 행}, 보고)."""
    bb = c['bbox']
    ix = Index(tiles, bb)
    adds, photos = [], {}
    rep = {'kto_in_box': 0, 'kto_added': 0}
    must = must_list(geo_items, top)
    in_tiles = [g for g in must if g[0] in ix.qids or ix.has(g[1], g[2], g[5], PRESENT_M)]  # 보충 전 앱 타일에 있음
    for k in kto:
        if not (bb[0] <= k['lon'] <= bb[2] and bb[1] <= k['lat'] <= bb[3]):
            continue
        rep['kto_in_box'] += 1
        nm = PP.KR_BR.sub(' ', k['n']).strip() or k['n']
        if ix.has(k['lat'], k['lon'], [k['n'], nm], KTO_DUP_M):
            continue
        rid = 'kto:' + k['cid']
        adds.append({'id': rid, 'nm': nm, 'ko': nm, 'lat': k['lat'], 'lon': k['lon'], 'c': KTO_CAT[k['t']], 'src': 'tourapi', 'lic': 'KOGL', 'tr': 1})
        ix.add(k['lat'], k['lon'], [k['n'], nm])
        rep['kto_added'] += 1
        v = PP.kto_photo_row(k.get('photo'))
        if v:
            photos[rid] = dict(v, kto_cid=k['cid'])
    missing_before = [g for g in must if g not in in_tiles]
    missing = [g for g in missing_before if not ix.has(g[1], g[2], g[5], PRESENT_M)]  # TourAPI 보충으로 채워진 것은 뺌
    added_wd = 0
    for g in missing:
        q, la, lo, sl, p, names = g[:6]
        ln = lang_names(names)
        rid = 'wikidata:' + q
        adds.append({'id': rid, 'nm': ln.get('en') or names[0], **{k: v for k, v in ln.items() if k in ('ko', 'en', 'ja', 'zh')},
                     'lat': la, 'lon': lo, 'c': 'tourist_attraction', 'src': 'wikidata', 'lic': 'CC0', 'wd': q, 'tr': 1})
        ix.add(la, lo, names)
        added_wd += 1
        cc_ = ccache.get(p)
        v = PN.photo_row(p, cc_[1]) if cc_ else None
        if v:
            photos[rid] = dict(v, q=q)
    after = sum(1 for g in must if g[0] in ix.qids or ix.has(g[1], g[2], g[5], PRESENT_M))
    rep.update(must=len(must), present=len(in_tiles), missing_before=len(missing_before), wd_added=added_wd, missing_after=len(must) - after,
               missing_names=[(lang_names(g[5]).get('en') or g[5][0])[:40] for g in missing_before][:30], cc=ix.main_cc())
    return adds, photos, rep


def run(store, bucket=None, tiles_dir=None, kto_dir=None, out=None, top=TOP, dry_run=False):
    cities = PP.load_cities()
    geo = (store.get_json('processed/photos/geo_items.json.gz', None) or {}).get('cities') or {}
    ccache = store.get_json('processed/desc/commons.json.gz', {}) or {}
    for u, v in (store.get_json('processed/photos/commons.json.gz', {}) or {}).items():
        if u not in ccache or ccache[u][0] < v[0]:
            ccache[u] = v
    wd = tempfile.mkdtemp(prefix='must_', dir=os.environ.get('RUNNER_TEMP') or None)
    by_cc, photos, reps = {}, {}, {}
    try:
        ver, tiles_for = PP.local_tiles_for(tiles_dir) if tiles_dir else PP.gcs_tiles_for(bucket, wd)
        if not kto_dir:
            import place_desc as PD
            kto_dir = os.path.join(wd, 'kto'); os.makedirs(kto_dir)
            PD.gs('cp', '-q', f'gs://{bucket}/places/v1/layers_extra/kto_*.json.gz', kto_dir + '/')
        kto = load_kto(sorted(glob.glob(os.path.join(kto_dir, 'kto_*.json.gz'))))
        log(f'TourAPI 관광지·문화시설·레포츠 {len(kto):,}')
        seen = set()
        for c in cities:
            adds, ph, rep = city_run(c, tiles_for(c['bbox']), geo.get(c['key']), kto, ccache, top)
            reps[c['key']] = rep
            cc = rep['cc'] or 'XX'
            for r in adds:
                if r['id'] not in seen:  # 상자가 겹치는 도시(서울·인천 등)에서 한 번만
                    seen.add(r['id']); by_cc.setdefault(cc, []).append(r)
            photos.update(ph)
        # (270기) 우선 도시 상자 밖 한국 전역의 TourAPI 관광지·문화시설·레포츠도 일정용 장소로(같은 규칙: 앱 타일에 200m·이름으로 없을 때만)
        rest = [k for k in kto if not any(c['bbox'][0] <= k['lon'] <= c['bbox'][2] and c['bbox'][1] <= k['lat'] <= c['bbox'][3] for c in cities)]
        if rest:
            adds, ph, rep = city_run({'key': 'kr_rest', 'bbox': KR_BOX}, tiles_for(KR_BOX), None, rest, ccache, top)
            reps['kr_rest'] = rep
            for r in adds:
                if r['id'] not in seen:
                    seen.add(r['id']); by_cc.setdefault('KR', []).append(r)
            photos.update(ph)
    finally:
        shutil.rmtree(wd, ignore_errors=True)
    out = out or tempfile.mkdtemp(prefix='must_out_', dir=os.environ.get('RUNNER_TEMP') or None)
    os.makedirs(out, exist_ok=True)
    for f in glob.glob(os.path.join(out, 'col_mustadd_*.json.gz')) + glob.glob(os.path.join(out, 'photos-must-*.json.gz')):
        os.remove(f)
    from process import to_layer
    made = []
    for cc, rows in sorted(by_cc.items()):
        rows.sort(key=lambda r: r['id'])
        p = os.path.join(out, f'col_mustadd_{cc}-00.json.gz')
        doc = to_layer(cc, rows)
        doc['licenses'] = {'tourapi': 'KOGL(한국관광공사 TourAPI 4.0, data.go.kr 15101578)', 'wikidata': 'CC0'}
        open(p, 'wb').write(gzip.compress(json.dumps(doc, ensure_ascii=False, separators=(',', ':')).encode(), 6, mtime=0))
        made.append(p)
    if photos:
        p = os.path.join(out, 'photos-must-1.json.gz')
        doc = {'schema': 'photos-1', 'region': 'must', 'license': 'mixed — 행마다 photo_license(공공누리 1·3유형 · CC0 · CC BY · CC BY-SA)',
               'attribution': '사진: 한국관광공사 TourAPI · 위키미디어 공용', 'built': today(), 'rows': dict(sorted(photos.items()))}
        open(p, 'wb').write(gzip.compress(json.dumps(doc, ensure_ascii=False, separators=(',', ':')).encode(), 6, mtime=0))
        made.append(p)
    if bucket and not dry_run:
        import place_desc as PD
        dest = f'gs://{bucket}/places/v1/layers_extra/'
        if made and PD.gs('cp', '-q', '-I', dest, stdin='\n'.join(made).encode()).returncode != 0:
            raise SystemExit('보충 층 올리기 실패')
        names = {os.path.basename(p) for p in made}
        r = PD.gs('ls', dest + 'col_mustadd_*.json.gz', dest + 'photos-must-*.json.gz')
        stale = [u for u in (r.stdout.decode().split() if r.stdout else []) if u.endswith('.json.gz') and u.rsplit('/', 1)[1] not in names]
        if stale:
            PD.gs('rm', '-q', '-I', stdin='\n'.join(stale).encode())
    probe = {}
    for nm in PROBES:
        k = PP.geo_norm(nm)
        hit = [r for rows in by_cc.values() for r in rows if k and any(k == PP.geo_norm(PP.KR_BR.sub(' ', r.get(x) or '')) or k == PP.geo_norm(r.get(x) or '') for x in ('nm', 'ko'))]
        probe[nm] = [f"{r['id']}·{r['c']}·사진{'있음' if r['id'] in photos else '없음'}" for r in hit[:2]] or ['보충 없음(타일에 있거나 TourAPI·Wikidata 에 없음)']
    pub = None
    if bucket:
        import place_desc as PD
        import publish_layers as PLY
        lj = PD.gs_json(f'gs://{bucket}/places/v1/layers.json') or {}

        def cat(path):
            r = PD.gs('cat', f'gs://{bucket}/{path}')
            return r.stdout if r.returncode == 0 else None
        try:  # 2단 목록(collect 등은 part 목록 안)까지 따라간다 — process.publish 와 같은 방식
            files = [e.get('file') for e in PLY.entries_of(lj, cat)]
        except Exception:  # noqa: BLE001
            files = [e.get('file') for e in lj.get('layers') or [] if isinstance(e, dict)]
        pub = {'version': lj.get('version'), 'col_mustadd': sum(1 for f in files if isinstance(f, str) and 'col_mustadd_' in f),
               'photos_must': sum(1 for f in files if isinstance(f, str) and 'photos-must' in f)}
    store.put_json('processed/photos/must_state.json', {'built': today(), 'cities': {k: {x: y for x, y in v.items() if x != 'missing_names'} for k, v in reps.items()}})
    L = [f'## 꼭 있어야 할 명소 점검·보충 {today()}',
         f"- 보충 행 {sum(len(v) for v in by_cc.values()):,}(나라 {len(by_cc)}) · 사진 {len(photos):,}",
         f"- 대표 명소 보충 행 확인: {probe}",
         f"- 앱 게시 목록(places/v1/layers.json)에 실린 보충 층: {pub}",
         '| 도시 | 꼭 있어야 할 명소 | 타일에 있음 | 빠짐(보충 전) | Wikidata 로 보충 | 보충 뒤 빠짐 | TourAPI 상자 안 | TourAPI 보충 | 빠졌던 명소(Wikidata 라벨) |', '|---|---|---|---|---|---|---|---|---|']
    for c in cities + [{'key': 'kr_rest'}]:
        r = reps.get(c['key']) or {}
        L.append(f"| {c['key']} | {r.get('must', 0)} | {r.get('present', 0)} | {r.get('missing_before', 0)} | {r.get('wd_added', 0)} | {r.get('missing_after', 0)} | {r.get('kto_in_box', 0):,} | "
                 f"{r.get('kto_added', 0):,} | {', '.join(r.get('missing_names') or [])} |")
    p = os.environ.get('GITHUB_STEP_SUMMARY')
    if p:
        open(p, 'a', encoding='utf-8').write('\n'.join(L) + '\n')
    print('\n'.join(L))
    return reps, by_cc, photos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['build'])
    ap.add_argument('--store', required=True); ap.add_argument('--bucket', default=None); ap.add_argument('--tiles', default=None)
    ap.add_argument('--kto', default=None); ap.add_argument('--out', default=None); ap.add_argument('--top', type=int, default=TOP)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    run(Store(a.store), a.bucket, a.tiles, a.kto, a.out, a.top, a.dry_run)


if __name__ == '__main__':
    main()
