#!/usr/bin/env python3
"""핵심 규칙 묶음(분류·필터·랭킹·큐레이션·음역 사전 — 공개 저장소에 평문으로 두지 않는 것)을 암호화 → private_enc/rules.enc + rules.key.enc
개발 서버에서만 실행. 비밀키 없음: AES-256-GCM 데이터 키를 새로 만들고, 그 키를 KMS RSA 공개키(PEM, 비밀 아님)로 감쌈.
Actions 는 KMS asymmetric-decrypt(서비스 계정 권한)로만 풀 수 있다.
  python3 encrypt_rules.py <rules_pub.pem>"""
import io, os, sys, tarfile, secrets, json
from cryptography.hazmat.primitives import serialization as ser, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import os; G = os.environ.get('TL_GLOBAL_PACKS', '../global_packs'); D14 = os.environ.get('TL_D14', '../d14')
FILES = [(f'{G}/tools/{f}', f'tools/{f}') for f in ('build_pack.py', 'taxo.py', 'fsq_map.py', 'curated_ko.py', 'vi_ko.py', 'ms_ko.py', 'cities.py', 'cities300.py')] + \
        [(f'{G}/raw/wiki/{f}', f'raw/wiki/{f}') for f in ('ko_geo.json', 'ko_by_qid.json', 'ko_by_title.json')] + \
        [(f'{G}/rules_src/rules.json', 'tools/rules.json')] + \
        [(f'{D14}/{f}', f'd14/{f}') for f in ('kana_ko.py', 'jp_ko.py', 'zh_ko.py', 'kr_conv.py')] + \
        [(f'{D14}/raw/{f}', f'd14/raw/{f}') for f in ('jp_ko_dict.json', 'zh_pinyin_map.json', 'zh_fold.json', 'unihan_mandarin.json')]
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode='w:gz') as t:
    for src, arc in FILES:
        if os.path.exists(src):
            t.add(src, arcname=arc)
pt = buf.getvalue()
dek = AESGCM.generate_key(256); nonce = secrets.token_bytes(12)
ct = nonce + AESGCM(dek).encrypt(nonce, pt, b'triproutie-rules-v1')
pub = ser.load_pem_public_key(open(sys.argv[1], 'rb').read())
wrapped = pub.encrypt(dek, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'private_enc'), exist_ok=True)
o = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'private_enc')
open(f'{o}/rules.enc', 'wb').write(ct); open(f'{o}/rules.key.enc', 'wb').write(wrapped)
print('ok', len(pt), 'bytes plaintext,', len(FILES), 'files')
