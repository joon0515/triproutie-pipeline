# 파이프라인 작업 규칙 (275기, 2026-11-20)

새 워크플로·스크립트는 아래 모양을 따른다. 옛 것은 고칠 일이 생길 때 옮긴다(한 번에 다 바꾸지 않는다).

## 1. 워크플로
| 할 일 | 쓰는 것 | 메모 |
|---|---|---|
| GCP 로그인 + gcloud + Python | `./.github/actions/gcp-setup` (checkout 다음) | `with: {wif: "${{ vars.GCP_WIF_PROVIDER }}", sa: "${{ vars.GCP_SA_EMAIL }}"}`. Python 이 필요 없으면 `python: none` |
| 명령 하나 돌리는 작업 | `uses: ./.github/workflows/reusable-gcp-job.yml` | 입력 job·run·env·retries·timeout. 본보기 = `fx_daily.yml` publish |
| 실행 기록·재시도 | `scripts/lib/run_job.sh <이름> <시도> <명령 파일>` | JSON 이벤트 3종 + Step Summary 표 한 줄. 시도 1 = 재시도 없음 |
| 실패 알림 | `failure-digest.yml` 목록에 워크플로 `name:` 추가 | 이슈 '자동화 실패 모음' 하나에 한 줄. 재사용 작업은 부르는 쪽 이름으로 모임 |

필수: `timeout-minutes`, `concurrency`, 최소 `permissions`(데이터 올리면 `id-token: write`). 시험 `tests/test_workflows.py`·`tests/test_guards.py` 가 막는다.
예외 목록(직접 로그인 6파일, timeout 없는 옛 작업 6개)은 `tests/test_workflows.py` 맨 위 — 줄이기만 한다.

## 2. 스크립트
- 새 코드는 `from lib import obs`(scripts/ 에서 실행할 때): `obs.event('x.done', n=..)` · `obs.metric('rows', n, job=..)` · `obs.retry(fn)` · `obs.http_get(url, ua, tries=3)`.
- `print` 는 사람이 읽는 요약만. 키·토큰·메일은 로그에 넣지 않는다(obs.event 가 이름에 key/token/secret/password 가 든 필드를 가린다).
- 재시도는 **멱등한 일만**(같은 파일 다시 받기·같은 객체 덮어쓰기). 이슈 만들기·카운터 올리기는 재시도하지 않는다.

## 3. 지표(작업 종류별 최소)
일괄 작업: 마지막 성공 시각(watchdog), 걸린 시간·시도(run_job), 처리 행 수(obs.metric). 바깥 API: 호출 수·상태 코드별 실패(obs.event).

## 4. 남은 중복(다음에 옮길 것)
직접 로그인 6파일 · 스크립트 같은 몸통 5묶음(_gz/_load 3, http_get 2, write 2, rows_of 2, load_pack 2) · 하위 프로세스로 gsutil/gcloud 부르는 스크립트 11개 → obs 와 공용 업로드 함수로.
