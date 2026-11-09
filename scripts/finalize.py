#!/usr/bin/env python3
"""모든 지역 부분 manifest → places-world-v1 manifest(정규 JSON, Cloud KMS Ed25519 서명) → Storage 업로드. 장소 데이터·키 출력 없음."""
import base64, datetime, glob, hashlib, json, os, subprocess, sys
sys.path.insert(0, 'tools')
from canonical_fix import canon_value, canonical
TAG = sys.argv[1]; D = os.environ['RUNNER_TEMP']; DEST = f"gs://{os.environ['GCS_BUCKET']}/places/v1/{TAG}"
os.makedirs(f'{D}/parts', exist_ok=True)
r = subprocess.run(['gcloud', 'storage', 'cp', '-q', f'{DEST}/parts/*.json', f'{D}/parts/'], capture_output=True, text=True)
if r.returncode:
    sys.exit('parts download failed: ' + r.stderr.strip()[-300:])
# 2단 구조: 지역 manifest(타일별 sha256·크기)는 지역마다 파일로 올리고, 루트 manifest(64 KiB 미만)에는 지역 manifest 의
# 경로·sha256·크기만 넣어 KMS 로 서명. 앱 검증 순서: 루트 서명 → 지역 manifest sha256 → 타일 sha256.
EXP = (datetime.datetime.utcnow() + datetime.timedelta(days=90)).strftime('%Y-%m-%dT%H:%M:%SZ')
regions = []; ntiles = 0
os.makedirs(f'{D}/regions', exist_ok=True)
for f in sorted(glob.glob(f'{D}/parts/*.json')):
    pj = json.load(open(f)); R = pj['region']
    rm = canonical(canon_value({'schema': 'places-world-region-1', 'version': TAG, 'region': R, 'path': f'places/v1/{TAG}/tiles/', 'tiles': pj['tiles']})).encode('utf-8')
    open(f'{D}/regions/{R}.json', 'wb').write(rm)
    r = subprocess.run(['gcloud', 'storage', 'cp', '-q', f'{D}/regions/{R}.json', f'{DEST}/regions/{R}.json'], capture_output=True, text=True)
    if r.returncode:
        sys.exit('region upload failed: ' + r.stderr.strip()[-300:])
    regions.append({'region': R, 'path': f'places/v1/{TAG}/regions/{R}.json', 'sha256': hashlib.sha256(rm).hexdigest(), 'size': len(rm), 'n': len(pj['tiles'])})
    ntiles += len(pj['tiles'])
man = canon_value({'schema': 'places-world-2', 'version': TAG, 'created_at': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
                   'path': f'places/v1/{TAG}/tiles/', 'regions': regions, 'disabled': False, 'revoked': [], 'expires': EXP,
                   'attribution': {'ko': '장소 정보: © Overture Maps Foundation (CDLA-Permissive-2.0) · Foursquare OS Places (Apache-2.0) · AllThePlaces (CC0)'}})
open(f'{D}/body.json', 'w', encoding='utf-8').write(canonical(man))
k = os.environ['KMS_SIGN_KEY']
r = subprocess.run(['gcloud', 'kms', 'asymmetric-sign', *(lambda q: [f'--project={q[1]}', f'--location={q[3]}', f'--keyring={q[5]}', f'--key={q[7]}', f'--version={q[9]}'])(k.split('/')),
                f'--input-file={D}/body.json', f'--signature-file={D}/sig.bin'], capture_output=True, text=True)
if r.returncode:
    sys.exit('kms sign failed: body ' + str(os.path.getsize(f'{D}/body.json')) + ' bytes; ' + r.stderr.strip()[-400:])
man['sig'] = base64.b64encode(open(f'{D}/sig.bin', 'rb').read()).decode()
json.dump(man, open(f'{D}/manifest.json', 'w', encoding='utf-8'), ensure_ascii=False)
for dst in (f'{DEST}/manifest.json', f"gs://{os.environ['GCS_BUCKET']}/places/v1/manifest.json"):
    subprocess.run(['gcloud', 'storage', 'cp', '-q', f'{D}/manifest.json', dst], check=True, capture_output=True)
print('manifest ok:', len(regions), 'regions,', ntiles, 'files, root', os.path.getsize(f'{D}/body.json'), 'bytes')
