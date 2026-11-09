#!/usr/bin/env bash
# Actions 안에서만: KMS 로 데이터 키를 풀어 규칙 묶음을 tools/ 아래에 펼침. 키·평문은 로그에 남기지 않고 바로 지움.
set -euo pipefail
umask 077
IFS=/ read -r _ KP _ KL _ KR _ KK _ KV <<< "$KMS_RULES_KEY"
gcloud kms asymmetric-decrypt --project="$KP" --location="$KL" --keyring="$KR" --key="$KK" --version="$KV" \
  --ciphertext-file=private_enc/rules.key.enc --plaintext-file="$RUNNER_TEMP/dek.bin" >/dev/null
python3 - "$RUNNER_TEMP/dek.bin" <<'PY'
import io, sys, tarfile
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
dek = open(sys.argv[1], 'rb').read(); ct = open('private_enc/rules.enc', 'rb').read()
pt = AESGCM(dek).decrypt(ct[:12], ct[12:], b'triproutie-rules-v1')
tarfile.open(fileobj=io.BytesIO(pt), mode='r:gz').extractall('.', filter='data')
PY
shred -u "$RUNNER_TEMP/dek.bin" 2>/dev/null || rm -f "$RUNNER_TEMP/dek.bin"
echo "rules ok"
