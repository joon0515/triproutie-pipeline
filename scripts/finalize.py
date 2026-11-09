#!/usr/bin/env python3
"""모든 지역 부분 manifest → places-world-v1 manifest(정규 JSON, Cloud KMS Ed25519 서명) → Storage 업로드. 장소 데이터·키 출력 없음."""
import base64, datetime, glob, json, os, subprocess, sys
sys.path.insert(0, 'tools')
from canonical_fix import canon_value, canonical
TAG = sys.argv[1]; D = os.environ['RUNNER_TEMP']; DEST = f"gs://{os.environ['GCS_BUCKET']}/places/v1/{TAG}"
subprocess.run(['gcloud', 'storage', 'cp', '-q', f'{DEST}/parts/*.json', f'{D}/parts/'], check=True, capture_output=True)
tiles = [t for f in sorted(glob.glob(f'{D}/parts/*.json')) for t in json.load(open(f))['tiles']]
man = canon_value({'schema': 'places-world-1', 'version': TAG, 'created_at': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
                   'path': f'places/v1/{TAG}/tiles/', 'tiles': tiles, 'disabled': False, 'revoked': [],
                   'expires': (datetime.datetime.utcnow() + datetime.timedelta(days=90)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                   'attribution': {'ko': '장소 정보: © Overture Maps Foundation (CDLA-Permissive-2.0) · Foursquare OS Places (Apache-2.0) · AllThePlaces (CC0)'}})
open(f'{D}/body.json', 'w', encoding='utf-8').write(canonical(man))
k = os.environ['KMS_SIGN_KEY']
subprocess.run(['gcloud', 'kms', 'asymmetric-sign', *(lambda q: [f'--project={q[1]}', f'--location={q[3]}', f'--keyring={q[5]}', f'--key={q[7]}', f'--version={q[9]}'])(k.split('/')),
                f'--input-file={D}/body.json', f'--signature-file={D}/sig.bin'], check=True, capture_output=True)
man['sig'] = base64.b64encode(open(f'{D}/sig.bin', 'rb').read()).decode()
json.dump(man, open(f'{D}/manifest.json', 'w', encoding='utf-8'), ensure_ascii=False)
for dst in (f'{DEST}/manifest.json', f"gs://{os.environ['GCS_BUCKET']}/places/v1/manifest.json"):
    subprocess.run(['gcloud', 'storage', 'cp', '-q', f'{D}/manifest.json', dst], check=True, capture_output=True)
print('manifest ok:', len(tiles), 'files')
