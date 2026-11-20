#!/bin/bash
# run_job.sh <이름> <시도 횟수> <명령 파일> — 공용 실행기(275기). 모든 작업이 같은 모양으로 남긴다:
#   · JSON 이벤트 한 줄씩(stderr): {"event":"job.started|job.attempt_failed|job.finished","job":..,"attempt":..,"exit":..,"duration_s":..}
#   · Step Summary 표 한 줄: | 작업 | 결과 | 시도 | 걸린 시간 |
#   · 재시도: 실패하면 30초 × 시도 번호만큼 쉬고 다시(시도 횟수 1 이면 재시도 없음 = 옛 동작).
set -u
NAME=$1; TRIES=${2:-1}; CMD=$3; T0=$(date +%s); ev(){ printf '{"ts":"%s","event":"%s","job":"%s","attempt":%s,"exit":%s,"duration_s":%s}\n' "$(date -u +%FT%TZ)" "$1" "$NAME" "$2" "$3" "$(( $(date +%s) - T0 ))" >&2; }
ev job.started 0 0
a=0; rc=1
while [ $a -lt "$TRIES" ]; do a=$((a+1)); bash -eo pipefail "$CMD"; rc=$?; [ $rc = 0 ] && break; ev job.attempt_failed $a $rc; [ $a -lt "$TRIES" ] && sleep $((30*a)); done
ev job.finished $a $rc
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  grep -q '^| 작업 |' "$GITHUB_STEP_SUMMARY" 2>/dev/null || printf '| 작업 | 결과 | 시도 | 걸린 시간 |\n|---|---|---|---|\n' >> "$GITHUB_STEP_SUMMARY"
  printf '| %s | %s | %s | %ss |\n' "$NAME" "$([ $rc = 0 ] && echo 성공 || echo "실패($rc)")" "$a" "$(( $(date +%s) - T0 ))" >> "$GITHUB_STEP_SUMMARY"
fi
exit $rc
