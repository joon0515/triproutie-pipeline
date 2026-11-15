#!/usr/bin/env bash
# 그 판의 층 파일을 모두 내려받아 목록(places-layers-1)을 만들고 KMS 로 서명해 올린다. 파일이 먼저, 목록이 나중.
set -euo pipefail
TAG="$1"; W="$RUNNER_TEMP/layers_final"; mkdir -p "$W"
gcloud storage cp -q "gs://$GCS_BUCKET/places/v1/$TAG/layers/*.json.gz" "$W/" >/dev/null 2>&1
# 따로 만든 층(인기 층 pop_*.json.gz 등)을 저장소의 layers_extra/ 에 넣어 두면 함께 싣는다. 데이터 파일을 저장소에 두지 않으려면
# gs://<bucket>/places/v1/layers_extra/ 에 올려 둔다(있으면 가져온다).
gcloud storage cp -q "gs://$GCS_BUCKET/places/v1/layers_extra/*.json.gz" "$W/" >/dev/null 2>&1 || true
if ls layers_extra/*.json.gz >/dev/null 2>&1; then cp layers_extra/*.json.gz "$W/"; fi
# (11/15) 수집 순환(collect)이 만든 층 — 사진 층 photos-*.json.gz(photo_layer.py) 등. gs://<bucket>/collect/v1/layers/
gcloud storage cp -q "gs://$GCS_BUCKET/collect/v1/layers/*.json.gz" "$W/" >/dev/null 2>&1 || true
# (11/15) 영업시간 층: 이 판의 OSM 원본(hours_src/) + 세계 수집 최신 가공본(공식 사이트 JSON-LD·Wikivoyage) → 같은 oh 칸으로 합쳐 hours-*.json.gz
H="$RUNNER_TEMP/hours_merge"; mkdir -p "$H/osm" "$H/col"
gcloud storage cp -q "gs://$GCS_BUCKET/places/v1/$TAG/hours_src/*.json.gz" "$H/osm/" >/dev/null 2>&1 || true
RUN=$(gcloud storage cat "gs://$GCS_BUCKET/collect/v1/processed/latest.json" 2>/dev/null | python3 -c "import json,sys;print(json.load(sys.stdin).get('run',''))" || true)
if [ -n "$RUN" ]; then gcloud storage cp -q "gs://$GCS_BUCKET/collect/v1/processed/out/$RUN/col_*.json.gz" "$H/col/" >/dev/null 2>&1 || true; fi
rm -f "$W"/hours-*.json.gz   # 옛 합본은 버리고 원본에서 다시 합친다(두 번 합치지 않게)
python3 tools/hours_merge.py "$H/osm" "$H/col" "$W" | tail -1 || echo "hours merge skipped"
rm -rf "$H"
N=$(ls "$W"/*.json.gz | wc -l)
python3 scripts/publish_layers.py --tag "$TAG" --dir "$W" --bucket "$GCS_BUCKET" --kms-key "$KMS_SIGN_KEY" --tools tools | tail -1
rm -rf "$W"
echo "layers finalize: $N files"
