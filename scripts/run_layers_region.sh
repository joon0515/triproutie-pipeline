#!/usr/bin/env bash
# 지역 1개: OSM 추출본 받기 → 편의시설 층 → (선택) 이름 보충 층 → Firebase Storage 비공개 경로 업로드. 로그에는 개수만.
set -euo pipefail
R="$1"; TAG="$2"; DEST="gs://$GCS_BUCKET/places/v1/$TAG/layers"
W="$RUNNER_TEMP/layers_$R"; mkdir -p "$W/pbf" "$W/out" "$W/tiles"
URLS=$(python3 -c "import json,sys;print(json.load(open('tools/layers_regions.json'))[sys.argv[1]]['url'])" "$R")
WORLD=$(python3 -c "import json,sys;print(json.load(open('tools/layers_regions.json'))[sys.argv[1]].get('world',''))" "$R")
SPLIT=$(python3 -c "import json,sys;print('1' if json.load(open('tools/layers_regions.json'))[sys.argv[1]].get('split') else '')" "$R")
i=0
for U in $URLS; do
  i=$((i+1))
  # 추출본 서버 예절: 한 번에 하나, 실패하면 60초 쉬고 3번까지. 식별되는 User-Agent.
  curl -fsSL --retry 3 --retry-delay 60 -A "triproutie-pipeline (github actions; places-layers)" -o "$W/pbf/$i.osm.pbf" "$U"
done
# 편의시설 층. split 지역(큰 나라)은 위도·경도 4도 칸으로 나눠 파일 하나가 앱 상한(8MB)을 넘지 않게 한다.
if [ -n "$SPLIT" ]; then
  python3 tools/osm_amenities.py --split-deg 4 "$W/out/amenities-$R" "$W"/pbf/*.osm.pbf > /dev/null
else
  python3 tools/osm_amenities.py "$W/out/amenities-$R.json.gz" "$W"/pbf/*.osm.pbf > /dev/null
fi
# 이름 보충 층: 그 지역 세계 타일(같은 장소 id)에 OSM 의 한글 이름·읽는 법·영문 이름을 붙인다.
if [ "${IN_NAMES:-yes}" = "yes" ] && [ -n "$WORLD" ]; then
  WT="${IN_WORLD_TAG:-}"
  if [ -z "$WT" ]; then
    WT=$(gcloud storage cat "gs://$GCS_BUCKET/places/v1/manifest.json" 2>/dev/null | python3 -c "import json,sys;print(json.load(sys.stdin).get('version',''))" || true)
  fi
  if [ -n "$WT" ]; then
    python3 scripts/region_tiles.py "$WORLD" "$WT" "$R" > "$W/tiles.txt" || true
    if [ -s "$W/tiles.txt" ]; then
      gcloud storage cp -q $(cat "$W/tiles.txt") "$W/tiles/" >/dev/null 2>&1 || true
      if ls "$W"/tiles/w-*.json.gz >/dev/null 2>&1; then
        python3 tools/names_osm.py "$W/out/names-$R.json.gz" "$(ls "$W"/tiles/w-*.json.gz | paste -sd, -)" "$W"/pbf/*.osm.pbf > /dev/null || true
      fi
    fi
  fi
fi
N=$(ls "$W"/out/*.json.gz 2>/dev/null | wc -l)
[ "$N" -gt 0 ] || { echo "region $R: 0 files"; exit 1; }
gcloud storage cp -q "$W"/out/*.json.gz "$DEST/" >/dev/null 2>&1
rm -rf "$W"      # 러너 디스크·흔적 정리(캐시·아티팩트 없음)
echo "region $R: $N files uploaded"
