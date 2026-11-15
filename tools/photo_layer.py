#!/usr/bin/env python3
"""사진 층(photos-1) — 세계 타일 행 id 에 사진 1장을 붙인다. (2026-11-15)

    python3 photo_layer.py <out.json.gz> <지역 이름> <타일 폴더> [--hours <hours-*.json.gz 폴더>] [--kto <kto_*.json.gz 폴더>] [--top N]

출처(앞이 먼저):
  ① 한국(타일 cc=KR): TourAPI firstimage — tourapi_daily.py 가 공공누리 제1유형·제3유형만 남긴 kto_*.json.gz 의 photo 를 이름(공백·기호 뗀 뒤 같음)+좌표 150m 로 맞춤.
     3유형은 no_modify=true(자르기·보정 금지), 출처 '한국관광공사'.
  ② Wikidata P18 → Commons(pop_build.py 의 요청 예절·캐시 그대로: 한 번에 한 요청, 1.2초 간격, 429 면 Retry-After). Q-id 씨앗 =
     타일 id 의 'kowiki:Q…' · 한국어 위키 제목(pop_build.candidates — 좌표 3km 검증) · 영업시간 층의 q(OSM wikidata 태그, 80m+이름으로 맞춘 것).
     라이선스는 CC0 · CC BY · CC BY-SA(판 번호 무관)만. 퍼블릭 도메인 표기·그 밖은 싣지 않는다.
행: {"photo_url","photo_thumb","photo_credit"("사진: 저작자 · 라이선스"),"photo_license","photo_license_url"?,"photo_author"?,"photo_page"?,"photo_src":"tourapi|commons","no_modify"?}
"""
import glob, gzip, json, os, re, sys, time, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pop_build as PB  # noqa: E402

LIC_OK = re.compile(r'^(cc0( 1\.0)?|cc[ -]by([ -]\d\.\d)?( [a-z-]+)?|cc[ -]by-sa([ -]\d\.\d)?( [a-z-]+)?)$', re.I)
QID = re.compile(r'(?:^|:)(Q\d+)$')
STRIP = re.compile(r'[\s　・·\-‐－—.,()（）「」『』&＆\'’]+')


def nrm(s):
    return STRIP.sub('', (s or '').lower())


def tiles_in(folder):
    return sorted(glob.glob(os.path.join(folder, 'w-*.json.gz')))


def kto_photos(kto_dir):
    """kto_*.json.gz → 격자 {칸: [(lat, lon, 이름, photo)]} (사진 있는 행만)."""
    grid = {}
    for fn in sorted(glob.glob(os.path.join(kto_dir or '', 'kto_*.json.gz'))):
        j = json.loads(gzip.decompress(open(fn, 'rb').read()))
        for r in (j.get('rows') or {}).values():
            p = r.get('photo') if isinstance(r, dict) else None
            if not p or 'lat' not in r or p.get('license') not in ('KOGL-1', 'KOGL-3'):
                continue
            grid.setdefault((int(r['lat'] * 200), int(r['lon'] * 200)), []).append((r['lat'], r['lon'], nrm(r.get('n')), p))
    return grid


def from_kto(d, grid, i):
    la, lo = d['_lat'][i], d['_lon'][i]
    mine = {nrm(d[k][i]) for k in ('nm', 'ko') if isinstance(d.get(k), list) and isinstance(d[k][i], str)} - {''}
    if not mine:
        return None
    cy, cx = int(la * 200), int(lo * 200)
    best = None
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            for r in grid.get((cy + dy, cx + dx), ()):
                m = PB.km(la, lo, r[0], r[1]) * 1000
                if m <= 150 and r[2] in mine and (best is None or m < best[0]):
                    best = (m, r[3])
    if not best:
        return None
    p = best[1]
    lic = {'KOGL-1': '공공누리 제1유형(출처표시)', 'KOGL-3': '공공누리 제3유형(출처표시·변경금지)'}[p['license']]
    v = {'photo_url': p['url'], 'photo_thumb': p.get('thumb') or p['url'], 'photo_credit': f"사진: {p.get('credit') or '한국관광공사'} · {lic}",
         'photo_license': p['license'], 'photo_author': p.get('credit') or '한국관광공사', 'photo_src': 'tourapi'}
    if p.get('no_modify') or p['license'] == 'KOGL-3':
        v['no_modify'] = True
    return v


def p18_by_qid(qids):
    """Q-id → P18 파일 이름(wbgetentities ids 50개씩, pop_build 캐시·예절)."""
    out = {}
    uniq = sorted(set(qids))
    for k in range(0, len(uniq), 50):
        url = 'https://www.wikidata.org/w/api.php?' + urllib.parse.urlencode({'action': 'wbgetentities', 'ids': '|'.join(uniq[k:k + 50]), 'props': 'claims', 'format': 'json'})
        for q, e in ((PB.get_json(url) or {}).get('entities') or {}).items():
            try:
                f = e['claims']['P18'][0]['mainsnak']['datavalue']['value']
            except Exception:
                continue
            if isinstance(f, str):
                out[q] = f
    return out


def commons_row(rec, f):
    lic = rec.get('license') or ''
    if not LIC_OK.match(lic.strip()):
        return None
    return {k: v for k, v in {'photo_url': rec['thumb'], 'photo_thumb': rec['thumb'], 'photo_credit': rec['credit'], 'photo_license': lic,
                              'photo_license_url': rec.get('license_url') or None, 'photo_author': rec.get('artist') or None,
                              'photo_page': rec.get('page') or None, 'photo_src': 'commons', 'photo_file': f}.items() if v}


def hours_q(hours_dir):
    out = {}
    for fn in sorted(glob.glob(os.path.join(hours_dir or '', 'hours-*.json.gz'))):
        for rid, v in (json.loads(gzip.decompress(open(fn, 'rb').read())).get('rows') or {}).items():
            if isinstance(v, dict) and v.get('q'):
                out[rid] = v['q']
    return out


def build(region, tiles, hours_dir=None, kto_dir=None, top=20000):
    rows, rep, pts = {}, {'tiles': len(tiles), 'rows': 0, 'tourapi': 0, 'commons': 0, 'seeds': 0}, []
    grid = kto_photos(kto_dir) if kto_dir else {}
    hq = hours_q(hours_dir)
    seeds = {}  # 행 id → (Q-id, 우선순위)
    for fn in tiles:
        d = PB.load_pack(fn)
        rep['rows'] += d['n']
        cand = {i: (t, lim) for i, t, lim, _ in PB.candidates(d)}
        wd = PB.wikidata([t for t, _ in cand.values()]) if cand else {}
        for i in range(d['n']):
            rid = d['id'][i]
            if grid and d.get('cc') == 'KR':
                v = from_kto(d, grid, i)
                if v:
                    rows[rid] = v; rep['tourapi'] += 1; pts.append((d['_lat'][i], d['_lon'][i]))
                    continue
            m = QID.search(rid)
            q = m.group(1) if m else None
            if not q and i in cand:
                w = wd.get(cand[i][0])
                if w and w.get('lat') is not None and PB.km(d['_lat'][i], d['_lon'][i], w['lat'], w['lon']) <= cand[i][1]:
                    q = w['q']
            q = q or hq.get(rid)
            if q:
                seeds[rid] = (q, d['s'][i] if isinstance(d.get('s'), list) else 0, d['_lat'][i], d['_lon'][i])
    # 요청 수를 묶어 두려고 점수 높은 순 top 개만(한 지역 20000 = wbgetentities 400회 + imageinfo 400회 안쪽)
    pick = sorted(seeds.items(), key=lambda kv: -kv[1][1])[:top]
    rep['seeds'] = len(pick)
    p18 = p18_by_qid([v[0] for _, v in pick])
    ph = PB.photos(sorted(set(p18.values())))
    for rid, (q, _, la, lo) in pick:
        f = p18.get(q)
        rec = ph.get(f) if f else None
        v = commons_row(rec, f) if rec else None
        if v and rid not in rows:
            rows[rid] = v; rep['commons'] += 1; pts.append((la, lo))
    doc = {'schema': 'photos-1', 'region': region, 'license': 'mixed — 행마다 photo_license(CC0·CC BY·CC BY-SA·공공누리 1·3유형)',
           'attribution': '사진: 위키미디어 공용(사진마다 저작자·라이선스) · 한국관광공사 TourAPI 4.0(공공누리 제1·3유형)',
           'built': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'report': {**rep, 'with_photo': len(rows), 'net': dict(PB.STATS)}, 'rows': rows}
    if pts:
        doc['bbox'] = [round(min(p[1] for p in pts), 4), round(min(p[0] for p in pts), 4), round(max(p[1] for p in pts), 4), round(max(p[0] for p in pts), 4)]
    return doc


def main(argv):
    def opt(name, default=None):
        if name in argv:
            i = argv.index(name); v = argv[i + 1]; del argv[i:i + 2]; return v
        return default
    hours_dir, kto_dir, top = opt('--hours'), opt('--kto'), int(opt('--top', 20000))
    if len(argv) < 4:
        print(__doc__)
        return 2
    doc = build(argv[2], tiles_in(argv[3]), hours_dir, kto_dir, top)
    if not doc['rows']:
        print('no photos', doc['report'])
        return 1
    with gzip.open(argv[1], 'wt', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, separators=(',', ':'))
    print(doc['report'], os.path.getsize(argv[1]), 'bytes')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
