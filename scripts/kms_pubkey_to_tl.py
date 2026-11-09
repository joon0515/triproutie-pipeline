#!/usr/bin/env python3
"""Cloud KMS Ed25519 공개키(PEM, cloudshell_setup.sh 가 출력) → 앱 tl_sig 형식(32바이트 hex) — kTlRdPubKeys / 장소 팩 manifest 확인용.
  python3 kms_pubkey_to_tl.py pub.pem   (또는 표준입력)"""
import sys
from cryptography.hazmat.primitives import serialization as ser
pem = open(sys.argv[1], 'rb').read() if len(sys.argv) > 1 else sys.stdin.buffer.read()
k = ser.load_pem_public_key(pem)
print(k.public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw).hex())
