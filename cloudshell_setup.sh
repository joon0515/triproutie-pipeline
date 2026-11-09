#!/usr/bin/env bash
# 트립루티 장소 파이프라인 — Google Cloud Shell 에 그대로 붙여넣어 실행(여러 번 실행해도 안전: 이미 있으면 건너뜀).
# 비밀값은 만들거나 출력하지 않는다(키 파일 없음: GitHub Actions → GCP 는 Workload Identity Federation, 서명은 Cloud KMS).
set -euo pipefail
PROJECT="${PROJECT:-trip-mate-0vum9k}"
BUCKET="${BUCKET:-trip-mate-0vum9k.firebasestorage.app}"     # Firebase Storage 기본 버킷(11/9 생성, US-EAST1)
LOC="${LOC:-us-east1}"   # KMS 위치(버킷 US-EAST1 과 같게)
REPO="${REPO:-joon0515/triproutie-pipeline}"
POOL=github-pool; PROVIDER=github-provider; SA_NAME=pipeline-uploader
KEYRING=triproutie; SIGN_KEY=places-manifest-ed25519; RULES_KEY=pipeline-rules-rsa
gcloud config set project "$PROJECT" >/dev/null
PNUM=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
SA="$SA_NAME@$PROJECT.iam.gserviceaccount.com"
echo "[1/7] API 켜기"
gcloud services enable iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com cloudkms.googleapis.com storage.googleapis.com >/dev/null
echo "[2/7] 서비스 계정"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 || gcloud iam service-accounts create "$SA_NAME" --display-name="Trip Routie places pipeline uploader" >/dev/null
echo "[3/7] 버킷 권한(버킷 단위 objectAdmin)"
if ! gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1; then
  echo "  버킷 gs://$BUCKET 이 없습니다. Blaze 전환 → Firebase 콘솔 Storage '시작하기'(위치 $LOC) 뒤 다시 실행하세요."; exit 1
fi
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$SA" --role=roles/storage.objectAdmin >/dev/null
echo "[4/7] Workload Identity 풀·프로바이더(main 브랜치·이 저장소만)"
gcloud iam workload-identity-pools describe "$POOL" --location=global >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools create "$POOL" --location=global --display-name="GitHub Actions" >/dev/null
gcloud iam workload-identity-pools providers describe "$PROVIDER" --location=global --workload-identity-pool="$POOL" >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" --location=global --workload-identity-pool="$POOL" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition="assertion.repository=='$REPO' && assertion.ref=='refs/heads/main'" >/dev/null
echo "[5/7] workloadIdentityUser 바인딩"
gcloud iam service-accounts add-iam-policy-binding "$SA" --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PNUM/locations/global/workloadIdentityPools/$POOL/attribute.repository/$REPO" >/dev/null
echo "[6/7] KMS 키링·키(manifest 서명 Ed25519, 규칙 묶음 복호화 RSA)"
gcloud kms keyrings describe "$KEYRING" --location="$LOC" >/dev/null 2>&1 || gcloud kms keyrings create "$KEYRING" --location="$LOC" >/dev/null
gcloud kms keys describe "$SIGN_KEY" --keyring="$KEYRING" --location="$LOC" >/dev/null 2>&1 || \
  gcloud kms keys create "$SIGN_KEY" --keyring="$KEYRING" --location="$LOC" --purpose=asymmetric-signing --default-algorithm=ec-sign-ed25519 --protection-level=software >/dev/null
gcloud kms keys describe "$RULES_KEY" --keyring="$KEYRING" --location="$LOC" >/dev/null 2>&1 || \
  gcloud kms keys create "$RULES_KEY" --keyring="$KEYRING" --location="$LOC" --purpose=asymmetric-encryption --default-algorithm=rsa-decrypt-oaep-3072-sha256 --protection-level=software >/dev/null
echo "[7/7] KMS 권한(서명·검증 / 복호화)"
gcloud kms keys add-iam-policy-binding "$SIGN_KEY" --keyring="$KEYRING" --location="$LOC" --member="serviceAccount:$SA" --role=roles/cloudkms.signerVerifier >/dev/null
gcloud kms keys add-iam-policy-binding "$RULES_KEY" --keyring="$KEYRING" --location="$LOC" --member="serviceAccount:$SA" --role=roles/cloudkms.cryptoKeyDecrypter >/dev/null
echo
echo "=== GitHub 저장소 Settings → Secrets and variables → Actions → Variables 에 넣을 값(비밀 아님) ==="
echo "GCP_WIF_PROVIDER=projects/$PNUM/locations/global/workloadIdentityPools/$POOL/providers/$PROVIDER"
echo "GCP_SA_EMAIL=$SA"
echo "GCS_BUCKET=$BUCKET"
echo "KMS_SIGN_KEY=projects/$PROJECT/locations/$LOC/keyRings/$KEYRING/cryptoKeys/$SIGN_KEY/cryptoKeyVersions/1"
echo "KMS_RULES_KEY=projects/$PROJECT/locations/$LOC/keyRings/$KEYRING/cryptoKeys/$RULES_KEY/cryptoKeyVersions/1"
echo "=== 아래 두 공개키(비밀 아님)를 개발팀에 전달: 앱 서명 확인용 · 규칙 묶음 암호화용 ==="
gcloud kms keys versions get-public-key 1 --key="$SIGN_KEY" --keyring="$KEYRING" --location="$LOC" --output-file=/dev/stdout
gcloud kms keys versions get-public-key 1 --key="$RULES_KEY" --keyring="$KEYRING" --location="$LOC" --output-file=/dev/stdout
