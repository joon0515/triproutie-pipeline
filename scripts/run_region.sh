#!/usr/bin/env bash
# 지역 1개: 스캔(Overture+FSQ) → 타일 → 빌드 → Firebase Storage 비공개 경로 업로드 → 지역 부분 manifest. 로그에는 개수만.
set -euo pipefail
R="$1"; TAG="$2"; DEST="gs://$GCS_BUCKET/places/v1/$TAG"
export QUIET=1 PACK_TARGET_MB=2.5 PACK_SPLIT_MB=3 DUCK_MEM=10GB
python3 tools/world_region.py "$R" > /dev/null
SLUGS=$(python3 -c "import json;print(' '.join(json.load(open('raw/world/tiles_$R.json'))))")
python3 tools/build_pack.py $SLUGS > /dev/null
python3 scripts/part_manifest.py "$R" > "$RUNNER_TEMP/part_$R.json"
N=$(ls packs/w-*.json.gz | wc -l)
gcloud storage cp -q packs/w-*.json.gz "$DEST/tiles/" >/dev/null 2>&1
gcloud storage cp -q "$RUNNER_TEMP/part_$R.json" "$DEST/parts/$R.json" >/dev/null 2>&1
rm -rf raw/world packs/w-*      # 러너 디스크·흔적 정리(캐시·아티팩트 없음)
echo "region $R: $N files uploaded"
