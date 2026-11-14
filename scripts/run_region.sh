#!/usr/bin/env bash
# 지역 1개: 스캔(Overture+FSQ) → 타일 → 빌드 → Firebase Storage 비공개 경로 업로드 → 지역 부분 manifest. 로그에는 개수만.
set -euo pipefail
R="$1"; TAG="$2"; DEST="gs://$GCS_BUCKET/places/v1/$TAG"
export QUIET=1 PACK_TARGET_MB=2.5 PACK_SPLIT_MB=3 DUCK_MEM=10GB
python3 tools/world_region.py "$R" > /dev/null
SLUGS=$(python3 -c "import json;print(' '.join(json.load(open('raw/world/tiles_$R.json'))))")
python3 tools/build_pack.py $SLUGS > /dev/null
# 11/14: 지역 상자가 겹치는 곳(부산·쓰시마 등)은 두 지역이 같은 셀 id 의 타일을 만든다 → 같은 이름으로 올리면 나중 것이 덮어써
# 먼저 올린 지역의 manifest 해시와 어긋난다(verify 실패). 파일 이름에 지역을 넣어 겹치지 않게 한다.
for f in packs/w-*.json.gz; do mv "$f" "${f%.json.gz}.$R.json.gz"; done
python3 scripts/part_manifest.py "$R" > "$RUNNER_TEMP/part_$R.json"
N=$(ls packs/w-*.json.gz | wc -l)
gcloud storage cp -q packs/w-*.json.gz "$DEST/tiles/" >/dev/null 2>&1
gcloud storage cp -q "$RUNNER_TEMP/part_$R.json" "$DEST/parts/$R.json" >/dev/null 2>&1
rm -rf raw/world packs/w-*      # 러너 디스크·흔적 정리(캐시·아티팩트 없음)
echo "region $R: $N files uploaded"
