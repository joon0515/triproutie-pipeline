#!/usr/bin/env python3
"""층(인기·사진, 편의시설) 파일을 Firebase Storage 에 올리고 서명 manifest 를 만든다.  (2026-11-13, 서버 팀 — 실행은 비서실장이 Cloud Shell 에서)

세계 타일과 같은 규칙을 따른다(11/10 결정): 파일은 places/v1/<tag>/ 아래, manifest 는 정규 JSON 을 Cloud KMS(Ed25519)로 서명,
파일마다 sha256·크기를 manifest 에 적어 앱이 받은 뒤 맞춰 본다. 다운로드 주소·토큰은 만들지 않는다(로그인 사용자만 SDK/REST 로 읽음).

    python3 publish_layers.py --tag 2026-11-13 --dir ./layers --bucket trip-mate-0vum9k.firebasestorage.app \\
        --kms-key projects/<P>/locations/us-east1/keyRings/<R>/cryptoKeys/<K>/cryptoKeyVersions/1 --tools ~/triproutie-pipeline/tools [--dry-run]

    --dry-run  아무것도 올리지 않고 서명도 하지 않는다. manifest 본문과 실행할 명령만 보여 준다(먼저 이것으로 확인).

올라가는 곳
    gs://<bucket>/places/v1/<tag>/layers/<파일>            층 파일(gzip JSON)
    gs://<bucket>/places/v1/<tag>/layers.json              서명된 manifest(뿌리 목록, 64 KiB 미만)
    gs://<bucket>/places/v1/<tag>/layer_parts/<kind>-NNN.json   2단 목록(종류마다, 장마다 따로 서명, 60 KiB 이하) — 뿌리 목록의 parts 에 sha256·크기가 적힌다
    gs://<bucket>/places/v1/layers.json                    최신 판을 가리키는 사본(되돌리기 = 이전 판 manifest 로 덮어쓰기)
받는 층: schema 가 'popularity-1'(pop_build.py·food_score.py) · 'amenities-1'(osm_amenities.py) · 'lockers-1' · 'names-1'(names_osm.py — 이름 보충) · 'tourapi-1'(tourapi_daily.py — 한국관광공사) · 'collect-1'(scripts/collect/process.py — 세계 수집) · 'desc-1'(scripts/place_desc.py·tools/desc_osm.py — 장소 소개, 2단 목록) 인 gzip JSON. 그 밖의 파일은 거부한다.
키·토큰·장소 내용은 출력하지 않는다(파일 이름·크기·개수만).

2단 목록(11/17): 서명 키는 Ed25519(PureEdDSA)라 KMS 가 본문을 그대로 받아 서명한다 — 요약값(digest)만 보내 서명하는 길이 없고, 한 번에 64 KiB 까지만 받는다.
앱(tl_place_layers)도 뿌리 목록을 64 KiB 까지만 읽는다. 수집 층(col_*)이 수백 개가 되면서 목록이 216 KB 가 되어 서명이 실패했다.
→ 수집 층 항목은 layer_parts/collect-NNN.json(나라 단위로 200개씩)으로 빼고, 뿌리 목록에는 그 파일의 sha256·크기만 싣는다(세계 타일 목록 finalize.py 와 같은 구조).
   검증 순서: 뿌리 서명 → 2단 목록 sha256 → 층 파일 sha256. 뿌리 목록의 schema·layers 모양은 그대로라 지금 앱은 고치지 않아도 된다(모르는 키 parts 는 무시).
   지금 앱은 수집 층(kind collect)을 읽지 않는다. 읽게 할 때 parts 를 따라가면 된다.

모든 종류 2단(11/19 253기): 앱이 뿌리에서 읽는 층(amenities·hours·names·photos …)만으로 뿌리가 67,705 bytes 가 되어 다시 서명 실패(process 10회).
→ 모든 종류를 layer_parts/<kind>-NNN.json 으로 빼고(종류마다, 한 장 60 KiB 이하로 다시 조각, 장마다 KMS 서명 — 본문 = sig 를 뺀 정규 JSON, 뿌리와 같은 방식),
   뿌리에는 장마다 {file, kind, n, rows, size, sha256, cc?, bbox?} 만 싣는다. 뿌리 크기 ≈ 장 수 × 250 bytes(층 파일 수만 개까지 상한 아래).
   하위 호환: 지금 앱이 뿌리 layers 에서 읽는 종류(LEGACY_KINDS)는 앱이 쓰는 칸(file·kind·size·sha256·bbox·packs)만 뿌리 layers 에도 남긴다.
   남은 자리 안에서 작은 종류부터 통째로 싣고, 못 실은 종류는 legacy_omitted 에 적는다(실패 아님 — 그 종류는 parts 를 읽는 앱만 받음).
"""
import argparse, base64, datetime, glob, gzip, hashlib, json, os, subprocess, sys

OK_SCHEMA = {'popularity-1': 'popularity', 'amenities-1': 'amenities', 'lockers-1': 'lockers', 'names-1': 'names', 'tourapi-1': 'tourapi', 'collect-1': 'collect', 'hours-1': 'hours', 'photos-1': 'photos', 'desc-1': 'desc', 'names-2': 'names5', 'rec-1': 'rec', 'shop-1': 'shop', 'events-1': 'events'}  # hours·photos: 11/15 · desc: 11/17 장소 소개(place_desc.py·desc_osm.py) · names5: 11/18 이름 5개 언어(place_names.py·names5_osm.py)
MAX_BYTES = 8 * 1024 * 1024  # 앱의 파일 크기 상한(타일과 같음)
SIGN_MAX = 64 * 1024         # KMS asymmetric-sign 이 한 번에 받는 본문 크기(Ed25519 는 본문 그대로 서명)
ROOT_MAX = 62 * 1024         # 뿌리 목록 파일(서명 포함) 상한 — 앱 kTlPlMaxRootBytes(64 KiB)·SIGN_MAX 보다 작게
SIG_ROOM = 200               # sig 칸(Ed25519 64바이트 → base64 88자 + 키 이름·따옴표) 여유
PART_MAX = 60 * 1024         # 2단 목록 한 장(서명 포함) 상한 — 2단 목록도 따로 KMS 서명하므로 SIGN_MAX 보다 작게. 넘으면 그 종류 안에서 다시 조각
# (11/19 253기) 뿌리 목록에 남기는 옛 항목: 지금 앱이 뿌리 layers 에서 바로 읽는 종류(tl_place_layers tlPlPick · tl_world_extra hours/photos).
#  모든 종류는 2단 목록(parts)에 다 싣고, 이 종류만 뿌리 layers 에도 앱이 쓰는 칸만 남긴다(하위 호환). 뿌리 상한 안에 다 못 들면 작은 종류부터 넣고
#  못 넣은 종류는 legacy_omitted 에 적는다(그 종류는 parts 를 읽는 앱만 받는다). 그래서 뿌리 크기는 층이 늘어도 상한에 닿지 않는다.
INDEX_AT = 16 * 1024         # 뿌리에 이만큼은 옛 항목(하위 호환) 자리로 남기고, 넘으면 장 목록을 색인(3단)으로
LEGACY_KINDS = ('popularity', 'amenities', 'names', 'lockers', 'labels', 'hours', 'photos')
LEGACY_FIELDS = ('file', 'kind', 'size', 'sha256', 'bbox', 'packs')


def describe(fn):
    """파일 하나 → manifest 항목. 모양이 틀리면 ValueError."""
    b = open(fn, 'rb').read()
    if len(b) > MAX_BYTES:
        raise ValueError(f'{os.path.basename(fn)}: {len(b)} bytes > {MAX_BYTES}')
    j = json.loads(gzip.decompress(b))
    kind = OK_SCHEMA.get(j.get('schema'))
    if not kind:
        raise ValueError(f'{os.path.basename(fn)}: 모르는 schema')
    rows = j.get('rows') if kind != 'lockers' else j.get('lockers')
    if kind == 'collect' and isinstance(j.get('id'), list):  # 수집 층(scripts/collect/process.py) — 타일과 같은 열 모양
        rows = j['id']
    n = len(rows) if isinstance(rows, (list, dict)) else 0
    if n == 0:
        raise ValueError(f'{os.path.basename(fn)}: 행이 없음')
    e = {'file': os.path.basename(fn), 'kind': kind, 'n': n, 'size': len(b), 'sha256': hashlib.sha256(b).hexdigest()}
    if kind in ('amenities', 'lockers'):
        pts = rows
        e['bbox'] = [round(min(r['lon'] for r in pts), 4), round(min(r['lat'] for r in pts), 4), round(max(r['lon'] for r in pts), 4), round(max(r['lat'] for r in pts), 4)]
        e['license'] = 'ODbL-1.0'
    if kind == 'names':
        e['packs'] = (j.get('packs') if isinstance(j.get('packs'), list) and len(j['packs']) <= 40 else None) or ([j['pack']] if j.get('pack') and not j.get('packs') else None)  # 타일 수백 개짜리 지역 층은 packs 를 싣지 않고 bbox 로 고르게 한다
        bb = j.get('bbox')
        if isinstance(bb, list) and len(bb) == 4 and all(isinstance(x, (int, float)) for x in bb):
            e['bbox'] = bb
        e['license'] = 'ODbL-1.0'
    if kind == 'tourapi':  # 한국관광공사 TourAPI 4.0(tourapi_daily.py) — 시도 1개 = 파일 1개, rows 는 {'kto:<cid>': {...}}
        pts = [r for r in rows.values() if isinstance(r, dict) and 'lat' in r]
        if pts:
            e['bbox'] = [round(min(r['lon'] for r in pts), 4), round(min(r['lat'] for r in pts), 4), round(max(r['lon'] for r in pts), 4), round(max(r['lat'] for r in pts), 4)]
        e['license'] = 'KOGL'
        e['area'] = j.get('area')
        e['photos'] = sum(1 for r in rows.values() if isinstance(r, dict) and 'photo' in r)
    if kind == 'collect':
        e['dataset'] = j.get('dataset')
        if j.get('cc'):
            e['cc'] = j['cc']
        if isinstance(j.get('dla'), list) and j['dla']:
            la = lo = 0; xs = []; ys = []
            for a, b in zip(j['dla'], j['dlo']):
                la += a; lo += b; ys.append(la / 1e5); xs.append(lo / 1e5)
            e['bbox'] = [round(min(xs), 4), round(min(ys), 4), round(max(xs), 4), round(max(ys), 4)]
        e['license'] = 'mixed (sources/registry.yaml)'
    if kind in ('hours', 'photos'):  # 영업시간 층(hours_osm.py + hours_merge.py) · 사진 층(photo_layer.py) — rows 는 {장소 id: {...}}
        bb = j.get('bbox')
        if isinstance(bb, list) and len(bb) == 4 and all(isinstance(x, (int, float)) for x in bb):
            e['bbox'] = bb
        e['license'] = j.get('license') or 'mixed'
        if kind == 'hours':
            e['with_oh'] = sum(1 for r in rows.values() if isinstance(r, dict) and r.get('oh'))
        else:
            e['region'] = j.get('region')
    if kind == 'names5':  # 이름 5개 언어(names-2) — rows 는 {장소 id: {ko,en,ja,zh_Hans,zh_Hant: 이름|null, q?|osm?}}, 출처 하나 = 파일 하나
        e['license'] = j.get('license') or 'mixed'
        e['src'] = j.get('src')
        e['langs'] = {k: v for k, v in (j.get('langs') or {}).items() if isinstance(v, int)} or None
        bb = j.get('bbox')
        if isinstance(bb, list) and len(bb) == 4 and all(isinstance(x, (int, float)) for x in bb):
            e['bbox'] = bb
        if j.get('region'):
            e['region'] = j['region']
    if kind == 'desc':  # 장소 소개 — rows 는 {장소 id: {ko,en,ja,zh_Hans,zh_Hant: 글|null, …}}, 출처 하나 = 파일 하나(라이선스를 섞지 않음)
        e['license'] = j.get('license') or 'mixed'
        e['src'] = j.get('src')
        e['langs'] = {k: v for k, v in (j.get('langs') or {}).items() if isinstance(v, int)} or None
        bb = j.get('bbox')
        if isinstance(bb, list) and len(bb) == 4 and all(isinstance(x, (int, float)) for x in bb):
            e['bbox'] = bb
        if j.get('region'):
            e['region'] = j['region']
    if kind == 'rec':  # (11/18 245기) 트립루티 추천 지수 — rows 는 {장소 id: [점수, 신호…]}, 계산식 docs/RECOMMEND_INDEX.md
        e['src'], e['version'], e['cc'] = j.get('src'), j.get('version'), j.get('cc')
    if kind in ('shop', 'events'):  # (11/20 256기) 쇼핑 층(shop_index.py) · 행사 층(events_layer.py·tourapi_daily.py) — 앱은 나라·상자로 고른다
        e['src'], e['version'] = j.get('src'), j.get('version')
        if j.get('cc'):
            e['cc'] = j['cc']
        pts = [(r[1], r[2]) for r in rows.values() if isinstance(r, list) and len(r) > 2] if kind == 'shop' else \
              [(r['la'], r['lo']) for r in rows.values() if isinstance(r, dict) and 'la' in r]
        if pts:
            e['bbox'] = [round(min(p[1] for p in pts), 4), round(min(p[0] for p in pts), 4), round(max(p[1] for p in pts), 4), round(max(p[0] for p in pts), 4)]
    if kind == 'popularity':
        e['packs'] = sorted((j.get('report') or {}).keys()) or None
        e['photos'] = sum(1 for r in rows.values() if isinstance(r, dict) and 'photo' in r)
    return {k: v for k, v in e.items() if v is not None}


def build_manifest(tag, files, now=None, extra=()):
    now = now or datetime.datetime.now(datetime.UTC)
    return {'schema': 'places-layers-1', 'version': tag, 'created_at': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'path': f'places/v1/{tag}/layers/',
            'layers': sorted([describe(f) for f in files] + list(extra), key=lambda e: e['file']), 'disabled': False, 'revoked': [],
            'expires': (now + datetime.timedelta(days=90)).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'attribution': {'ko': '인기 점수: 위키백과 조회수·Wikidata (CC0) · 사진: 위키미디어 공용(사진마다 저작자·라이선스 표기) · 편의시설·이름 보충: © OpenStreetMap contributors (ODbL 1.0) · 한국 관광지 정보·사진: 한국관광공사 TourAPI 4.0 (공공누리 제1·3유형, 출처표시) · 장소 이름(5개 언어): Wikidata (CC0) · © OpenStreetMap contributors (ODbL 1.0) · 장소 소개: Wikidata (CC0) · 한국관광공사 TourAPI 4.0 (공공누리 제1유형) · 交通部觀光署 觀光資訊資料庫 (政府資料開放授權條款-第1版) · © OpenStreetMap contributors (ODbL 1.0) — 파일마다 license·credit · 세계 수집 층(col_*): Overture Maps Foundation(CDLA-Permissive-2.0 등 레코드별) · Foursquare Labs(Apache-2.0) · Wikivoyage(CC BY-SA 4.0) · Wikidata(CC0) · 각국 공공데이터(출처별 라이선스, 행마다 lic)'}}


def _part_chunks(ents, canonical, budget):
    """한 종류의 항목 → 장 목록. 장마다 정규 JSON 이 budget 바이트 이하. 나라(cc)가 갈리지 않게 나라 단위로 채우고, 한 나라가 넘치면 그 나라만 여러 장."""
    def cc_of(e):
        return e['cc'] if isinstance(e.get('cc'), str) else ''  # 색인(3단)에서 넘기는 장 항목은 cc 가 목록 — 나누지 않고 차례대로
    by_cc = {}
    for e in sorted(ents, key=lambda e: (cc_of(e), e['file'])):
        by_cc.setdefault(cc_of(e), []).append(e)
    chunks, cur, used = [], [], 0
    for cc in sorted(by_cc):
        g = [(e, len(canonical(e).encode('utf-8')) + 1) for e in by_cc[cc]]
        gb = sum(n for _, n in g)
        if cur and used + gb > budget:
            chunks.append(cur); cur, used = [], 0
        for e, n in g:
            if n > budget:
                raise ValueError(f"{e['file']}: 항목 하나가 {n} bytes — 2단 목록 한 장에 못 실음")
            if cur and used + n > budget:
                chunks.append(cur); cur, used = [], 0
            cur.append(e); used += n
    if cur:
        chunks.append(cur)
    return chunks


def split_manifest(man, canonical, sign=None):
    """한 장짜리 목록 → (뿌리 목록, {2단 목록 파일 이름: 바이트}).
    모든 종류를 layer_parts/<kind>-NNN.json 으로 빼고(장마다 PART_MAX 이하, sign 이 있으면 장마다 따로 서명), 뿌리에는 장의 sha256·크기만 싣는다.
    뿌리 layers 에는 지금 앱이 읽는 종류(LEGACY_KINDS)의 앱용 칸만 남긴다(하위 호환, 상한 안에서). 층이 없으면 목록은 예전 모양 그대로.
    sign(body: bytes) → base64 서명 문자열. 없으면(dry-run·시험) 서명 칸 없이 본문만."""
    if not man['layers']:
        return dict(man), {}
    head = {k: v for k, v in man.items() if k != 'layers'}
    parts, blobs = [], {}
    for kind in sorted({e['kind'] for e in man['layers']}):
        ents = [e for e in man['layers'] if e['kind'] == kind]
        shell = {'schema': 'places-layers-part-1', 'version': man['version'], 'kind': kind, 'path': man['path'], 'layers': []}
        budget = PART_MAX - SIG_ROOM - len(canonical(shell).encode('utf-8'))
        for i, ch in enumerate(_part_chunks(ents, canonical, budget)):
            name = f'{kind}-{i:03d}.json'
            body = {**shell, 'layers': ch}
            bb = canonical(body).encode('utf-8')
            b = canonical({**body, 'sig': sign(bb)}).encode('utf-8') if sign else bb
            if len(b) > PART_MAX:
                raise ValueError(f'{name}: {len(b)} bytes > {PART_MAX}')
            blobs[name] = b
            p = {'file': name, 'kind': kind, 'n': len(ch), 'rows': sum(e['n'] for e in ch), 'size': len(b), 'sha256': hashlib.sha256(b).hexdigest()}
            ccs = sorted({e['cc'] for e in ch if e.get('cc')})
            if ccs:
                p['cc'] = ccs
            bbs = [e['bbox'] for e in ch if isinstance(e.get('bbox'), list) and len(e['bbox']) == 4]
            if bbs:
                p['bbox'] = [min(x[0] for x in bbs), min(x[1] for x in bbs), max(x[2] for x in bbs), max(x[3] for x in bbs)]
            parts.append(p)
    root = {**head, 'layers': [], 'parts_path': f"places/v1/{man['version']}/layer_parts/", 'parts': parts}
    # 3단(색인): 장이 수백 장이 되어 뿌리의 parts 만으로 상한에 가까우면, 장이 가장 많은 종류부터 그 종류의 장 목록을
    #  <kind>-index-NNN.json(schema places-layers-index-1, 장마다 서명, PART_MAX 이하)으로 빼고 뿌리에는 색인 장의 sha256·크기만 둔다.
    #  색인 한 장 ≈ 장 280개 ≈ 층 항목 6만 개 — 뿌리 크기는 (종류 수 × 색인 장 수)에만 비례해 상한에 닿지 않는다.
    indexed = set()
    while len(canonical(root).encode('utf-8')) + SIG_ROOM > ROOT_MAX - INDEX_AT:
        cnt = {}
        for p in root['parts']:
            if p['kind'] not in indexed:
                cnt[p['kind']] = cnt.get(p['kind'], 0) + 1
        if not cnt:
            break  # 더 줄일 수 없음 → main 이 서명 전에 멈춘다
        kind = max(sorted(cnt), key=lambda k: cnt[k]); indexed.add(kind)
        mine = [p for p in root['parts'] if p['kind'] == kind]
        shell = {'schema': 'places-layers-index-1', 'version': man['version'], 'kind': kind, 'path': root['parts_path'], 'parts': []}
        budget = PART_MAX - SIG_ROOM - len(canonical(shell).encode('utf-8'))
        idx = []
        for i, ch in enumerate(_part_chunks(mine, canonical, budget)):
            name = f'{kind}-index-{i:03d}.json'
            body = {**shell, 'parts': ch}
            bb = canonical(body).encode('utf-8')
            b = canonical({**body, 'sig': sign(bb)}).encode('utf-8') if sign else bb
            blobs[name] = b
            x = {'file': name, 'kind': kind, 'index': True, 'n': len(ch), 'rows': sum(p['rows'] for p in ch), 'size': len(b), 'sha256': hashlib.sha256(b).hexdigest()}
            bbs = [p['bbox'] for p in ch if 'bbox' in p]
            if bbs:
                x['bbox'] = [min(q[0] for q in bbs), min(q[1] for q in bbs), max(q[2] for q in bbs), max(q[3] for q in bbs)]
            idx.append(x)
        root['parts'] = [p for p in root['parts'] if p['kind'] != kind] + idx
    # 옛 항목: 남은 자리 안에서 작은 종류부터 통째로(한 종류의 일부만 싣지 않는다 — 앱이 '이 종류는 다 있음'으로 믿으므로)
    legacy = {}
    for e in man['layers']:
        if e['kind'] in LEGACY_KINDS:
            legacy.setdefault(e['kind'], []).append({k: e[k] for k in LEGACY_FIELDS if k in e})
    cost = {k: sum(len(canonical(x).encode('utf-8')) + 1 for x in v) for k, v in legacy.items()}
    left = ROOT_MAX - SIG_ROOM - len(canonical({**root, 'legacy_omitted': sorted(legacy)}).encode('utf-8'))
    keep, omit = [], []
    for k in sorted(legacy, key=lambda k: (cost[k], k)):
        if cost[k] <= left:
            keep.append(k); left -= cost[k]
        else:
            omit.append(k)
    root['layers'] = sorted((x for k in keep for x in legacy[k]), key=lambda e: e['file'])
    if omit:
        root['legacy_omitted'] = sorted(omit)
    return root, blobs

def entries_of(root, read):
    """게시된 뿌리 목록 → 층 항목 전부(뿌리 layers 옛 항목 + 2단 목록 + 3단 색인). read(Storage 경로) → bytes|None.
    장마다 크기·sha256 을 뿌리(또는 색인)와 맞춰 보고, 틀리거나 못 읽으면 ValueError(빠진 채로 다음 판을 만들지 않게). 같은 파일은 한 번만."""
    out, seen = [], set()

    def take(e):
        if e.get('file') and e['file'] not in seen:
            seen.add(e['file']); out.append(e)

    def walk(p):
        b = read(root['parts_path'] + p['file'])
        if b is None or len(b) != p['size'] or hashlib.sha256(b).hexdigest() != p['sha256']:
            raise ValueError(f"2단 목록 {p['file']}: 못 읽음 또는 sha256 틀림")
        j = json.loads(b)
        for q in j.get('parts', []) if p.get('index') else []:
            walk(q)
        for e in j.get('layers', []) if not p.get('index') else []:
            take(e)
    for e in root.get('layers', []):
        take(e)
    for p in root.get('parts', []):
        walk(p)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag', required=True)
    ap.add_argument('--dir', required=True)
    ap.add_argument('--bucket', required=True)
    ap.add_argument('--kms-key', default='')
    ap.add_argument('--tools', default='tools', help='canonical_fix.py 가 있는 폴더(파이프라인 저장소의 tools/)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--extra', default='', help='{entries: [describe() 결과], sources: {파일: gs://… 원본}} — 러너에 받지 않고 Storage 안에서 복사해 싣는 층(process.py 수집 층)')
    a = ap.parse_args()
    if not all(c.isalnum() or c in '._-' for c in a.tag) or not (1 <= len(a.tag) <= 40):
        sys.exit('tag 은 영문·숫자·._- 40자 이내')
    sys.path.insert(0, a.tools)
    from canonical_fix import canon_value, canonical  # 타일 manifest 와 같은 정규 JSON(서명 검증이 이것과 맞아야 한다)
    files = sorted(glob.glob(os.path.join(a.dir, '*.json.gz')))
    ex = json.load(open(a.extra, encoding='utf-8')) if a.extra else {'entries': [], 'sources': {}}
    clash = {os.path.basename(f) for f in files} & set(ex['sources'])
    if clash:
        sys.exit(f'같은 이름의 층이 두 곳에서 옴: {sorted(clash)[:5]}')
    if not files and not ex['entries']:
        sys.exit('층 파일이 없음')
    flat = canon_value(build_manifest(a.tag, files, extra=ex['entries']))
    man, blobs = split_manifest(flat, canonical)  # 서명 전 모양으로 크기·내용 확인(dry-run 도 이것)
    man = canon_value(man)
    body = canonical(man)
    dest = f'gs://{a.bucket}/places/v1/{a.tag}'
    print(f'층 {len(files) + len(ex["entries"])}개(Storage 안 복사 {len(ex["entries"])}개), 합계 {sum(e["size"] for e in flat["layers"]):,} bytes, 뿌리 목록 본문 {len(body.encode()):,} bytes'
          + (f' (2단 목록 {len(blobs)}장에 {sum(p["n"] for p in man["parts"])}개 항목, 가장 큰 장 {max(len(b) for b in blobs.values()):,} bytes, 한 장이면 {len(canonical(flat).encode()):,} bytes)' if blobs else ''))
    if man.get('legacy_omitted'):  # 실패는 아님 — 그 종류는 parts 를 읽는 앱만 받는다
        print(f'경고: 뿌리 layers(지금 앱용 옛 항목)에 못 실은 종류 legacy_omitted={man["legacy_omitted"]} — 2단 목록에는 있음. 앱이 parts 를 읽게 바꿔야 그 종류가 보인다', file=sys.stderr)
    for e in flat['layers'][:300]:  # 수천 개면 앞 300개만
        print(f'  {e["kind"]:<11} {e["file"]:<44} n={e["n"]:<7} {e["size"]:>9,} bytes')

    def too_big(m, b):  # 서명·올리기 전에 멈춘다. 2단 구조라 여기 닿으면 2단 목록 장 수가 수백 장(층 수만 개)이라는 뜻
        if len(b.encode()) + SIG_ROOM <= ROOT_MAX:
            return
        by = {}
        for e in m['layers'] + m.get('parts', []):
            by[e['kind']] = by.get(e['kind'], 0) + len(canonical(e).encode()) + 1
        sys.exit(f'뿌리 목록 본문 {len(b.encode()):,} bytes > 상한 {ROOT_MAX - SIG_ROOM:,} (KMS 서명·앱 읽기 한도 64 KiB). 종류별 bytes: {by}')
    too_big(man, body)
    cmds = [['gcloud', 'storage', 'cp', '-q', *files, f'{dest}/layers/']] if files else []
    if a.dry_run:
        print('[dry-run] 서명·업로드 안 함. 실행할 명령:')
        print(f'  gcloud storage cp -q <{len(files)} files> {dest}/layers/')
        if ex['sources']:
            print(f'  gcloud storage cp -q -I {dest}/layers/   <Storage 안 복사 {len(ex["sources"])}개>')
        print(f'  gcloud kms asymmetric-sign … × {len(blobs)} (2단 목록 장마다) + 1 (뿌리)   (키: {"지정됨" if a.kms_key else "미지정"})')
        if blobs:
            print(f'  gcloud storage cp -q <{len(blobs)} part lists> {dest}/layer_parts/')
        print(f'  gcloud storage cp -q manifest.json {dest}/layers.json')
        print(f'  gcloud storage cp -q manifest.json gs://{a.bucket}/places/v1/layers.json')
        return 0
    if not a.kms_key:
        sys.exit('--kms-key 필요(서명 없이 올리지 않는다)')
    tmp = os.environ.get('TMPDIR', '/tmp')
    q = a.kms_key.split('/')

    def kms_sign(b, what):
        if len(b) > SIGN_MAX:
            sys.exit(f'{what}: 서명 본문 {len(b):,} bytes > {SIGN_MAX:,}')
        open(f'{tmp}/layers_body.json', 'wb').write(b)
        r = subprocess.run(['gcloud', 'kms', 'asymmetric-sign', f'--project={q[1]}', f'--location={q[3]}', f'--keyring={q[5]}', f'--key={q[7]}', f'--version={q[9]}',
                            f'--input-file={tmp}/layers_body.json', f'--signature-file={tmp}/layers_sig.bin'], capture_output=True, text=True)
        if r.returncode:
            sys.exit(f'kms sign failed ({what}): body {len(b)} bytes; ' + r.stderr.strip()[-400:])
        return base64.b64encode(open(f'{tmp}/layers_sig.bin', 'rb').read()).decode()

    if blobs:  # 2단 목록을 장마다 서명 → 그 바이트의 sha256·크기로 뿌리 목록을 다시 만든다
        man, blobs = split_manifest(flat, canonical, sign=lambda b: kms_sign(b, '2단 목록'))
        man = canon_value(man)
        body = canonical(man)
        too_big(man, body)
    man['sig'] = kms_sign(body.encode('utf-8'), '뿌리 목록')
    json.dump(man, open(f'{tmp}/layers_manifest.json', 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))  # 빈칸 없이(파일 크기 = 본문 + sig)
    if os.path.getsize(f'{tmp}/layers_manifest.json') > ROOT_MAX:
        sys.exit('뿌리 목록 파일이 상한을 넘음: ' + str(os.path.getsize(f'{tmp}/layers_manifest.json')) + ' bytes')
    if cmds:
        subprocess.run(cmds[0], check=True, capture_output=True)  # 파일 먼저, manifest 는 나중(앱이 없는 파일을 가리키는 manifest 를 보지 않게)
    if ex['sources']:  # 수집 층: Storage → Storage 복사(서버 쪽, 러너 디스크를 거치지 않음). 원본 이름 = 층 파일 이름
        bad = [f for f, src in ex['sources'].items() if os.path.basename(src) != f]
        if bad:
            sys.exit(f'원본 이름과 층 이름이 다름: {bad[:5]}')
        subprocess.run(['gcloud', 'storage', 'cp', '-q', '-I', f'{dest}/layers/'], input='\n'.join(ex['sources'].values()).encode(), check=True, capture_output=True)
    if blobs:  # 2단 목록은 층 파일 다음, 뿌리 목록보다 먼저
        pd = os.path.join(tmp, 'layers_parts_' + a.tag); os.makedirs(pd, exist_ok=True)
        for n, b in blobs.items():
            open(os.path.join(pd, n), 'wb').write(b)
        subprocess.run(['gcloud', 'storage', 'cp', '-q', *[os.path.join(pd, n) for n in sorted(blobs)], f'{dest}/layer_parts/'], check=True, capture_output=True)
    for dst in (f'{dest}/layers.json', f'gs://{a.bucket}/places/v1/layers.json'):
        subprocess.run(['gcloud', 'storage', 'cp', '-q', f'{tmp}/layers_manifest.json', dst], check=True, capture_output=True)
    print('layers manifest ok:', len(flat['layers']), 'files,', len(blobs), 'part lists, root', len(body.encode()), 'bytes')
    return 0


if __name__ == '__main__':
    sys.exit(main())
