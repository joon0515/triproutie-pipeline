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
  # 추출본 서버 예절: 한 번에 하나, 실패하면 60초 쉬고 3번까지(11/18: 접속 실패도 다시 — 11/17 지역 5곳이 curl (7) 로 끝남). 식별되는 User-Agent.
  curl -fsSL --retry 3 --retry-delay 60 --retry-connrefused --retry-all-errors -A "triproutie-pipeline (github actions; places-layers)" -o "$W/pbf/$i.osm.pbf" "$U"
done
# 편의시설 층. split 지역(큰 나라)은 위도·경도 4도 칸으로 나눠 파일 하나가 앱 상한(8MB)을 넘지 않게 한다.
if [ -n "$SPLIT" ]; then
  python3 tools/osm_amenities.py --split-deg 4 "$W/out/amenities-$R" "$W"/pbf/*.osm.pbf > /dev/null
else
  python3 tools/osm_amenities.py "$W/out/amenities-$R.json.gz" "$W"/pbf/*.osm.pbf > /dev/null
fi
# 세계 타일(같은 장소 id) 받기 — world 가 적힌 지역만. 이름 보충 층(names=false 가 아니면)과 영업시간 층(hours_osm.py, 11/15)이 같이 쓴다.
NAMES_OK=$(python3 -c "import json,sys;print('' if json.load(open('tools/layers_regions.json'))[sys.argv[1]].get('names', True) is False else '1')" "$R")
if [ -n "$WORLD" ]; then
  WT="${IN_WORLD_TAG:-}"
  if [ -z "$WT" ]; then
    WT=$(gcloud storage cat "gs://$GCS_BUCKET/places/v1/manifest.json" 2>/dev/null | python3 -c "import json,sys;print(json.load(sys.stdin).get('version',''))" || true)
  fi
  if [ -n "$WT" ]; then
    python3 scripts/region_tiles.py "$WORLD" "$WT" "$R" > "$W/tiles.txt" || true
    if [ -s "$W/tiles.txt" ]; then
      # 타일이 많으면(유럽·미국) 인자 길이 한도를 넘지 않게 목록 파일로 받는다
      gcloud storage cp -q -I "$W/tiles/" < "$W/tiles.txt" >/dev/null 2>&1 || true
    fi
  fi
fi
TILES=""
if ls "$W"/tiles/w-*.json.gz >/dev/null 2>&1; then TILES=$(ls "$W"/tiles/w-*.json.gz | paste -sd, -); fi
# (11/18) 그 지역 나라의 수집 층(게시 장소, col_<cc>_*) 주소 — 이름·영업시간 층이 타일 id 와 함께 수집 층 id 로도 싣는다(파일은 40개씩 받아 곧 지움)
COLS_ARG=()
if python3 scripts/region_cols.py "$R" > "$W/cols.txt" 2>/dev/null && [ -s "$W/cols.txt" ]; then COLS_ARG=(--cols "$W/cols.txt"); echo "region $R: col files $(wc -l < "$W/cols.txt")"; fi
# 이름 보충 층: 그 지역 세계 타일에 OSM 의 한글 이름·읽는 법·영문 이름을 붙인다(일본만 — names=false 인 지역은 건너뜀).
if [ "${IN_NAMES:-yes}" = "yes" ] && [ -n "$NAMES_OK" ] && [ -n "$TILES" ]; then
  python3 tools/names_osm.py "$W/out/names-$R.json.gz" "$TILES" "$W"/pbf/*.osm.pbf > /dev/null || true
fi
# 영업시간 층 원본(OSM opening_hours + wikidata 태그 → 타일 id). layers/ 가 아니라 hours_src/ 에 올리고, finalize 가 공식 사이트 값과 합쳐 layers/hours-*.json.gz 로 낸다.
if [ -n "$TILES" ]; then
  mkdir -p "$W/hours"
  if python3 tools/hours_osm.py "$W/hours/hours-$R.json.gz" "$TILES" "$W"/pbf/*.osm.pbf "${COLS_ARG[@]}" | tail -1; then
    gcloud storage cp -q "$W/hours/hours-$R.json.gz" "gs://$GCS_BUCKET/places/v1/$TAG/hours_src/" >/dev/null 2>&1 || echo "region $R: hours upload failed"
    # (11/17) 판과 상관없는 최신 사본. 수집 게시(c<날짜시각> 판)가 최신 판이 된 뒤의 merge 는 그 판에 hours_src 가 없어 여기서 가져온다.
    gcloud storage cp -q "$W/hours/hours-$R.json.gz" "gs://$GCS_BUCKET/places/v1/hours_src/" >/dev/null 2>&1 || echo "region $R: hours latest copy failed"
  fi
fi
# (11/17) 장소 소개 층 OSM 판(description:<lang> 태그 → 타일 id, ODbL 따로 파일). 수집 게시(process)가 뿌리 목록 밖(2단 목록)의 층은 넘겨 싣지 않으므로
# layers_extra/ 에도 사본을 둔다 — process 의 publish 가 layers_extra 를 함께 싣는다.
if [ -n "$TILES" ]; then
  if python3 tools/desc_osm.py "$W/out/desc-osm-$R.json.gz" "$TILES" "$W"/pbf/*.osm.pbf | tail -1 && [ -f "$W/out/desc-osm-$R.json.gz" ]; then
    gcloud storage cp -q "$W/out/desc-osm-$R.json.gz" "gs://$GCS_BUCKET/places/v1/layers_extra/" >/dev/null 2>&1 || echo "region $R: desc-osm extra copy failed"
  else
    echo "region $R: desc-osm skipped"
  fi
fi
# (11/18, 223기) 이름 5개 언어 층 OSM 판(name:ko·en·ja·zh-Hans·zh-Hant 태그 → 타일 id, ODbL 따로 파일 names-osm-<지역>-NN). 소개 층처럼 2단 목록(kind names5)이라
# 수집 게시(process)가 넘겨 싣도록 layers_extra/ 에 둔다. 지난번 조각 수가 달랐을 수 있어 이 지역 파일을 먼저 지운다.
if [ -n "$TILES" ]; then
  mkdir -p "$W/names5"
  if python3 tools/names5_osm.py "$W/names5" "$R" "$TILES" "$W"/pbf/*.osm.pbf "${COLS_ARG[@]}" | tail -1 && ls "$W"/names5/names-osm-*.json.gz >/dev/null 2>&1; then
    gcloud storage rm -q "gs://$GCS_BUCKET/places/v1/layers_extra/names-osm-$R-[0-9][0-9].json.gz" >/dev/null 2>&1 || true
    gcloud storage cp -q "$W"/names5/names-osm-*.json.gz "gs://$GCS_BUCKET/places/v1/layers_extra/" >/dev/null 2>&1 || echo "region $R: names-osm extra copy failed"
  else
    echo "region $R: names-osm skipped"
  fi
fi
N=$(ls "$W"/out/*.json.gz 2>/dev/null | wc -l)
[ "$N" -gt 0 ] || { echo "region $R: 0 files"; exit 1; }
gcloud storage cp -q "$W"/out/*.json.gz "$DEST/" >/dev/null 2>&1
rm -rf "$W"      # 러너 디스크·흔적 정리(캐시·아티팩트 없음)
echo "region $R: $N files uploaded"
