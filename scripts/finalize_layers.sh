#!/usr/bin/env bash
# 그 판의 층 파일을 모두 내려받아 목록(places-layers-1)을 만들고 KMS 로 서명해 올린다. 파일이 먼저, 목록이 나중.
set -euo pipefail
TAG="$1"; W="$RUNNER_TEMP/layers_final"; mkdir -p "$W"
gcloud storage cp -q "gs://$GCS_BUCKET/places/v1/$TAG/layers/*.json.gz" "$W/" >/dev/null 2>&1
# 따로 만든 층(인기 층 pop_*.json.gz 등)을 저장소의 layers_extra/ 에 넣어 두면 함께 싣는다. 데이터 파일을 저장소에 두지 않으려면
# gs://<bucket>/places/v1/layers_extra/ 에 올려 둔다(있으면 가져온다).
gcloud storage cp -q "gs://$GCS_BUCKET/places/v1/layers_extra/*.json.gz" "$W/" >/dev/null 2>&1 || true
if ls layers_extra/*.json.gz >/dev/null 2>&1; then cp layers_extra/*.json.gz "$W/"; fi
N=$(ls "$W"/*.json.gz | wc -l)
python3 scripts/publish_layers.py --tag "$TAG" --dir "$W" --bucket "$GCS_BUCKET" --kms-key "$KMS_SIGN_KEY" --tools tools | tail -1
rm -rf "$W"
echo "layers finalize: $N files"
