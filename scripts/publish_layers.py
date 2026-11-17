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
    gs://<bucket>/places/v1/<tag>/layer_parts/<kind>-NNN.json   2단 목록(수집 층 col_* 항목) — 뿌리 목록의 parts 에 sha256·크기가 적힌다
    gs://<bucket>/places/v1/layers.json                    최신 판을 가리키는 사본(되돌리기 = 이전 판 manifest 로 덮어쓰기)
받는 층: schema 가 'popularity-1'(pop_build.py·food_score.py) · 'amenities-1'(osm_amenities.py) · 'lockers-1' · 'names-1'(names_osm.py — 이름 보충) · 'tourapi-1'(tourapi_daily.py — 한국관광공사) · 'collect-1'(scripts/collect/process.py — 세계 수집) 인 gzip JSON. 그 밖의 파일은 거부한다.
키·토큰·장소 내용은 출력하지 않는다(파일 이름·크기·개수만).

2단 목록(11/17): 서명 키는 Ed25519(PureEdDSA)라 KMS 가 본문을 그대로 받아 서명한다 — 요약값(digest)만 보내 서명하는 길이 없고, 한 번에 64 KiB 까지만 받는다.
앱(tl_place_layers)도 뿌리 목록을 64 KiB 까지만 읽는다. 수집 층(col_*)이 수백 개가 되면서 목록이 216 KB 가 되어 서명이 실패했다.
→ 수집 층 항목은 layer_parts/collect-NNN.json(나라 단위로 200개씩)으로 빼고, 뿌리 목록에는 그 파일의 sha256·크기만 싣는다(세계 타일 목록 finalize.py 와 같은 구조).
   검증 순서: 뿌리 서명 → 2단 목록 sha256 → 층 파일 sha256. 뿌리 목록의 schema·layers 모양은 그대로라 지금 앱은 고치지 않아도 된다(모르는 키 parts 는 무시).
   지금 앱은 수집 층(kind collect)을 읽지 않는다. 읽게 할 때 parts 를 따라가면 된다.
"""
import argparse, base64, datetime, glob, gzip, hashlib, json, os, subprocess, sys

OK_SCHEMA = {'popularity-1': 'popularity', 'amenities-1': 'amenities', 'lockers-1': 'lockers', 'names-1': 'names', 'tourapi-1': 'tourapi', 'collect-1': 'collect', 'hours-1': 'hours', 'photos-1': 'photos'}  # hours·photos: 11/15
MAX_BYTES = 8 * 1024 * 1024  # 앱의 파일 크기 상한(타일과 같음)
SIGN_MAX = 64 * 1024         # KMS asymmetric-sign 이 한 번에 받는 본문 크기(Ed25519 는 본문 그대로 서명)
ROOT_MAX = 62 * 1024         # 뿌리 목록 파일(서명 포함) 상한 — 앱 kTlPlMaxRootBytes(64 KiB)·SIGN_MAX 보다 작게
PART_KINDS = ('collect',)    # 2단 목록으로 빼는 종류(지금 앱이 뿌리 목록에서 읽지 않는 것만)
PART_ENTRIES = 200           # 2단 목록 하나에 싣는 층 항목 수(나라가 갈리지 않게 나라 단위로 채운다)


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
    if kind == 'popularity':
        e['packs'] = sorted((j.get('report') or {}).keys()) or None
        e['photos'] = sum(1 for r in rows.values() if isinstance(r, dict) and 'photo' in r)
    return {k: v for k, v in e.items() if v is not None}


def build_manifest(tag, files, now=None, extra=()):
    now = now or datetime.datetime.utcnow()
    return {'schema': 'places-layers-1', 'version': tag, 'created_at': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'path': f'places/v1/{tag}/layers/',
            'layers': sorted([describe(f) for f in files] + list(extra), key=lambda e: e['file']), 'disabled': False, 'revoked': [],
            'expires': (now + datetime.timedelta(days=90)).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'attribution': {'ko': '인기 점수: 위키백과 조회수·Wikidata (CC0) · 사진: 위키미디어 공용(사진마다 저작자·라이선스 표기) · 편의시설·이름 보충: © OpenStreetMap contributors (ODbL 1.0) · 한국 관광지 정보·사진: 한국관광공사 TourAPI 4.0 (공공누리 제1·3유형, 출처표시) · 세계 수집 층(col_*): Overture Maps Foundation(CDLA-Permissive-2.0 등 레코드별) · Foursquare Labs(Apache-2.0) · Wikivoyage(CC BY-SA 4.0) · Wikidata(CC0) · 각국 공공데이터(출처별 라이선스, 행마다 lic)'}}


def split_manifest(man, canonical):
    """한 장짜리 목록 → (뿌리 목록, {2단 목록 파일 이름: 바이트}). PART_KINDS 항목만 뺀다. 뺄 것이 없으면 목록은 예전 모양 그대로."""
    keep = [e for e in man['layers'] if e['kind'] not in PART_KINDS]
    root = {**man, 'layers': keep}
    parts, blobs = [], {}
    for kind in PART_KINDS:
        by_cc = {}
        for e in sorted((e for e in man['layers'] if e['kind'] == kind), key=lambda e: (e.get('cc') or '', e['file'])):
            by_cc.setdefault(e.get('cc') or '', []).append(e)
        chunks, cur = [], []
        for cc in sorted(by_cc):
            g = by_cc[cc]
            if cur and len(cur) + len(g) > PART_ENTRIES:
                chunks.append(cur); cur = []
            while len(g) > PART_ENTRIES:  # 한 나라가 200개를 넘으면 그 나라만 여러 장으로
                chunks.append(g[:PART_ENTRIES]); g = g[PART_ENTRIES:]
            cur = cur + g
        if cur:
            chunks.append(cur)
        for i, ch in enumerate(chunks):
            name = f'{kind}-{i:03d}.json'
            b = canonical({'schema': 'places-layers-part-1', 'version': man['version'], 'kind': kind, 'path': man['path'], 'layers': ch}).encode('utf-8')
            blobs[name] = b
            p = {'file': name, 'kind': kind, 'n': len(ch), 'rows': sum(e['n'] for e in ch), 'size': len(b), 'sha256': hashlib.sha256(b).hexdigest()}
            ccs = sorted({e['cc'] for e in ch if e.get('cc')})
            if ccs:
                p['cc'] = ccs
            bbs = [e['bbox'] for e in ch if isinstance(e.get('bbox'), list) and len(e['bbox']) == 4]
            if bbs:
                p['bbox'] = [min(b[0] for b in bbs), min(b[1] for b in bbs), max(b[2] for b in bbs), max(b[3] for b in bbs)]
            parts.append(p)
    if parts:
        root['parts_path'] = f"places/v1/{man['version']}/layer_parts/"
        root['parts'] = parts
    return root, blobs


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
    man, blobs = split_manifest(flat, canonical)
    man = canon_value(man)
    body = canonical(man)
    dest = f'gs://{a.bucket}/places/v1/{a.tag}'
    print(f'층 {len(files) + len(ex["entries"])}개(Storage 안 복사 {len(ex["entries"])}개), 합계 {sum(e["size"] for e in flat["layers"]):,} bytes, manifest 본문 {len(body.encode()):,} bytes'
          + (f' (2단 목록 {len(blobs)}개에 {sum(p["n"] for p in man["parts"])}개 항목, 한 장이면 {len(canonical(flat).encode()):,} bytes)' if blobs else ''))
    for e in flat['layers'][:300]:  # 수천 개면 앞 300개만
        print(f'  {e["kind"]:<11} {e["file"]:<44} n={e["n"]:<7} {e["size"]:>9,} bytes')
    if len(body.encode()) + 200 > ROOT_MAX:  # 서명·올리기 전에 멈춘다(+200 = sig 칸). 앱이 읽는 종류가 이만큼 늘면 그 종류도 2단으로 빼야 한다(앱 변경 필요)
        by = {}
        for e in man['layers']:
            by[e['kind']] = by.get(e['kind'], 0) + len(canonical(e).encode()) + 1
        sys.exit(f'뿌리 목록 본문 {len(body.encode()):,} bytes > 상한 {ROOT_MAX - 200:,} (KMS 서명·앱 읽기 한도 64 KiB). 종류별 bytes: {by}')
    cmds = [['gcloud', 'storage', 'cp', '-q', *files, f'{dest}/layers/']] if files else []
    if a.dry_run:
        print('[dry-run] 서명·업로드 안 함. 실행할 명령:')
        print(f'  gcloud storage cp -q <{len(files)} files> {dest}/layers/')
        if ex['sources']:
            print(f'  gcloud storage cp -q -I {dest}/layers/   <Storage 안 복사 {len(ex["sources"])}개>')
        print(f'  gcloud kms asymmetric-sign … --input-file=body.json --signature-file=sig.bin   (키: {"지정됨" if a.kms_key else "미지정"})')
        if blobs:
            print(f'  gcloud storage cp -q <{len(blobs)} part lists> {dest}/layer_parts/')
        print(f'  gcloud storage cp -q manifest.json {dest}/layers.json')
        print(f'  gcloud storage cp -q manifest.json gs://{a.bucket}/places/v1/layers.json')
        return 0
    if not a.kms_key:
        sys.exit('--kms-key 필요(서명 없이 올리지 않는다)')
    tmp = os.environ.get('TMPDIR', '/tmp')
    open(f'{tmp}/layers_body.json', 'w', encoding='utf-8').write(body)
    q = a.kms_key.split('/')
    r = subprocess.run(['gcloud', 'kms', 'asymmetric-sign', f'--project={q[1]}', f'--location={q[3]}', f'--keyring={q[5]}', f'--key={q[7]}', f'--version={q[9]}',
                    f'--input-file={tmp}/layers_body.json', f'--signature-file={tmp}/layers_sig.bin'], capture_output=True, text=True)
    if r.returncode:
        sys.exit('kms sign failed: body ' + str(len(body.encode())) + ' bytes; ' + r.stderr.strip()[-400:])
    man['sig'] = base64.b64encode(open(f'{tmp}/layers_sig.bin', 'rb').read()).decode()
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
