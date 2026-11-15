#!/usr/bin/env bash
# TourAPI 하루 수집: GCS 누적본 받기 → tourapi_daily.py(한도 안) → 누적본·층·agg 올리기 → 상태 파일만 저장소에 커밋.
# 장소 내용은 GitHub(로그·artifact·cache)에 남기지 않는다. 키는 env TOURAPI_KEY(secret DATA_GO_KR_KEY)로만 받는다.
set -uo pipefail
W="$RUNNER_TEMP/tourapi"; S="tourapi/state.json"; G="gs://$GCS_BUCKET"
mkdir -p "$W/store" "$W/layers"
cp "$S" "$W/state_before.json" 2>/dev/null || echo '{}' > "$W/state_before.json"
gcloud storage cp -q "$G/tourapi/v1/store/area_*.json" "$W/store/" >/dev/null 2>&1 || echo "store: 처음 실행(누적본 없음)"
python3 scripts/tourapi_daily.py --state "$S" --store "$W/store" --layers "$W/layers" --agg "$W/agg_tourapi.json"
RC=$?
UP=0
if ls "$W"/store/area_*.json >/dev/null 2>&1; then
  gcloud storage cp -q "$W"/store/area_*.json "$G/tourapi/v1/store/" >/dev/null 2>&1 || UP=1
  [ $UP = 0 ] && { gcloud storage cp -q "$W"/layers/kto_*.json.gz "$G/places/v1/layers_extra/" >/dev/null 2>&1 || UP=1; }
  [ $UP = 0 ] && { gcloud storage cp -q "$W/agg_tourapi.json" "$G/tourapi/v1/agg_tourapi.json" >/dev/null 2>&1 || UP=1; }
fi
if [ $UP != 0 ]; then
  # 올리기 실패: 받은 내용이 GCS 에 없으니 위치(task·page·retry)는 되돌리고, 오늘 쓴 호출 수만 남긴다(한도 초과 방지).
  python3 - "$W/state_before.json" "$S" <<'PY'
import json, sys
old = json.load(open(sys.argv[1])); new = json.load(open(sys.argv[2]))
old.update(date=new['date'], calls=new['calls'], last=dict(new.get('last', {}), stop='UPLOAD_FAIL'))
json.dump(old, open(sys.argv[2], 'w'), ensure_ascii=False, indent=1, sort_keys=True)
PY
  echo "upload failed — 위치 되돌림"; RC=3
fi
rm -rf "$W"
git config user.name "github-actions[bot]"; git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add "$S"
if ! git diff --cached --quiet; then
  git commit -q -m "tourapi: 수집 상태 $(TZ=Asia/Seoul date +%F)"
  for i in 1 2 3; do git push -q origin HEAD:main && break; sleep 5; git pull -q --rebase origin main; done
fi
exit $RC
