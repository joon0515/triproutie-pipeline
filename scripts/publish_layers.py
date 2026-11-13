#!/usr/bin/env python3
"""층(인기·사진, 편의시설) 파일을 Firebase Storage 에 올리고 서명 manifest 를 만든다.  (2026-11-13, 서버 팀 — 실행은 비서실장이 Cloud Shell 에서)

세계 타일과 같은 규칙을 따른다(11/10 결정): 파일은 places/v1/<tag>/ 아래, manifest 는 정규 JSON 을 Cloud KMS(Ed25519)로 서명,
파일마다 sha256·크기를 manifest 에 적어 앱이 받은 뒤 맞춰 본다. 다운로드 주소·토큰은 만들지 않는다(로그인 사용자만 SDK/REST 로 읽음).

    python3 publish_layers.py --tag 2026-11-13 --dir ./layers --bucket trip-mate-0vum9k.firebasestorage.app \\
        --kms-key projects/<P>/locations/us-east1/keyRings/<R>/cryptoKeys/<K>/cryptoKeyVersions/1 --tools ~/triproutie-pipeline/tools [--dry-run]

    --dry-run  아무것도 올리지 않고 서명도 하지 않는다. manifest 본문과 실행할 명령만 보여 준다(먼저 이것으로 확인).

올라가는 곳
    gs://<bucket>/places/v1/<tag>/layers/<파일>            층 파일(gzip JSON)
    gs://<bucket>/places/v1/<tag>/layers.json              서명된 manifest
    gs://<bucket>/places/v1/layers.json                    최신 판을 가리키는 사본(되돌리기 = 이전 판 manifest 로 덮어쓰기)
받는 층: schema 가 'popularity-1'(pop_build.py·food_score.py) · 'amenities-1'(osm_amenities.py) · 'lockers-1' · 'names-1'(names_osm.py — 이름 보충) 인 gzip JSON. 그 밖의 파일은 거부한다.
키·토큰·장소 내용은 출력하지 않는다(파일 이름·크기·개수만).
"""
import argparse, base64, datetime, glob, gzip, hashlib, json, os, subprocess, sys

OK_SCHEMA = {'popularity-1': 'popularity', 'amenities-1': 'amenities', 'lockers-1': 'lockers', 'names-1': 'names'}
MAX_BYTES = 8 * 1024 * 1024  # 앱의 파일 크기 상한(타일과 같음)


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
    if kind == 'popularity':
        e['packs'] = sorted((j.get('report') or {}).keys()) or None
        e['photos'] = sum(1 for r in rows.values() if isinstance(r, dict) and 'photo' in r)
    return {k: v for k, v in e.items() if v is not None}


def build_manifest(tag, files, now=None):
    now = now or datetime.datetime.utcnow()
    return {'schema': 'places-layers-1', 'version': tag, 'created_at': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'path': f'places/v1/{tag}/layers/',
            'layers': [describe(f) for f in files], 'disabled': False, 'revoked': [],
            'expires': (now + datetime.timedelta(days=90)).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'attribution': {'ko': '인기 점수: 위키백과 조회수·Wikidata (CC0) · 사진: 위키미디어 공용(사진마다 저작자·라이선스 표기) · 편의시설·이름 보충: © OpenStreetMap contributors (ODbL 1.0)'}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag', required=True)
    ap.add_argument('--dir', required=True)
    ap.add_argument('--bucket', required=True)
    ap.add_argument('--kms-key', default='')
    ap.add_argument('--tools', default='tools', help='canonical_fix.py 가 있는 폴더(파이프라인 저장소의 tools/)')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    if not all(c.isalnum() or c in '._-' for c in a.tag) or not (1 <= len(a.tag) <= 40):
        sys.exit('tag 은 영문·숫자·._- 40자 이내')
    sys.path.insert(0, a.tools)
    from canonical_fix import canon_value, canonical  # 타일 manifest 와 같은 정규 JSON(서명 검증이 이것과 맞아야 한다)
    files = sorted(glob.glob(os.path.join(a.dir, '*.json.gz')))
    if not files:
        sys.exit('층 파일이 없음')
    man = canon_value(build_manifest(a.tag, files))
    body = canonical(man)
    dest = f'gs://{a.bucket}/places/v1/{a.tag}'
    print(f'층 {len(files)}개, 합계 {sum(e["size"] for e in man["layers"]):,} bytes, manifest 본문 {len(body.encode()):,} bytes')
    for e in man['layers']:
        print(f'  {e["kind"]:<11} {e["file"]:<44} n={e["n"]:<7} {e["size"]:>9,} bytes')
    cmds = [['gcloud', 'storage', 'cp', '-q', *files, f'{dest}/layers/']]
    if a.dry_run:
        print('[dry-run] 서명·업로드 안 함. 실행할 명령:')
        print('  ' + ' '.join(cmds[0][:4]) + f' <{len(files)} files> {dest}/layers/')
        print(f'  gcloud kms asymmetric-sign … --input-file=body.json --signature-file=sig.bin   (키: {"지정됨" if a.kms_key else "미지정"})')
        print(f'  gcloud storage cp -q manifest.json {dest}/layers.json')
        print(f'  gcloud storage cp -q manifest.json gs://{a.bucket}/places/v1/layers.json')
        return 0
    if not a.kms_key:
        sys.exit('--kms-key 필요(서명 없이 올리지 않는다)')
    tmp = os.environ.get('TMPDIR', '/tmp')
    open(f'{tmp}/layers_body.json', 'w', encoding='utf-8').write(body)
    q = a.kms_key.split('/')
    subprocess.run(['gcloud', 'kms', 'asymmetric-sign', f'--project={q[1]}', f'--location={q[3]}', f'--keyring={q[5]}', f'--key={q[7]}', f'--version={q[9]}',
                    f'--input-file={tmp}/layers_body.json', f'--signature-file={tmp}/layers_sig.bin'], check=True, capture_output=True)
    man['sig'] = base64.b64encode(open(f'{tmp}/layers_sig.bin', 'rb').read()).decode()
    json.dump(man, open(f'{tmp}/layers_manifest.json', 'w', encoding='utf-8'), ensure_ascii=False)
    subprocess.run(cmds[0], check=True, capture_output=True)  # 파일 먼저, manifest 는 나중(앱이 없는 파일을 가리키는 manifest 를 보지 않게)
    for dst in (f'{dest}/layers.json', f'gs://{a.bucket}/places/v1/layers.json'):
        subprocess.run(['gcloud', 'storage', 'cp', '-q', f'{tmp}/layers_manifest.json', dst], check=True, capture_output=True)
    print('layers manifest ok:', len(files), 'files')
    return 0


if __name__ == '__main__':
    sys.exit(main())
