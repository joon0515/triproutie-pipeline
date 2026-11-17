# 상시 가동(365일 24시간) — 끊기는 곳과 막은 방법 (2026-11-18, 233기)

대표 지시(11/18 00:50): GitHub 자동 수집을 쉬지 않고 돌려 데이터가 계속 쌓이게 한다. '구글맵 수준 데이터'·'자동화할 수 있는 것은 전부 GitHub로'는 그대로.

## 1. 끊김 점검과 수정
| # | 끊기는 곳 | 근거 | 수정 |
|---|---|---|---|
| 1 | collect chain: `process` 걸기가 실패하면 뒤 단계가 모두 건너뛰어 다음 수집이 안 걸림 | 단계 기본값 = 앞 단계 실패 시 중단 | `continue-on-error` |
| 2 | chain: `run_started_at` 조회 실패 → `bash -e` 로 그 단계가 죽음 → 다음 수집 안 걸림 | 명령 치환 실패도 스크립트를 끝냄 | 실패하면 10분 쉬고 진행 |
| 3 | chain: 다음 수집 걸기에 재시도 없음(API 일시 오류 한 번이면 사슬 끊김) | | 5번 재시도, 그래도 실패면 watchdog 이 3시간 안에 건다 |
| 4 | chain: 다음 수집을 건 뒤의 보충 단계(TourAPI·places-layers) 실패가 실행 전체를 실패로 만듦 → 실패 알림·연속 실패 수가 부풀려짐 | | `continue-on-error` |
| 5 | 예약(schedule) 지연·누락 — fx·coverage·tourapi·patterns·watch-sources 는 예약만 믿음 | GitHub 문서 "The schedule event can be delayed … some queued jobs may be dropped" | watchdog 이 마지막 성공 시각을 보고 다시 건다(`sources/watchdog.json`) |
| 6 | watchdog 자신의 예약도 빠질 수 있음 | 같음 | collect chain 이 2시간 넘게 watchdog 실행이 없으면 한 번 건다(서로 감시) |
| 7 | **60일 무활동 규칙** — 이 저장소는 공개(public)라 해당 | GitHub 문서 "In a public repository, scheduled workflows are automatically disabled when no repository activity has occurred in 60 days." | watchdog 이 `disabled_inactivity` 를 보면 REST `PUT …/enable` 로 되살리고, 하루 한 번(UTC 03시) 켜진 워크플로 전부에 enable 을 다시 불러 시계를 되돌린다. 사람이 끈 것(`disabled_manually`)은 건드리지 않는다. collect 의 시간별 상태 커밋도 활동이 된다 |
| 8 | 동시 실행: concurrency 묶음에는 실행 1개 + 대기 1개만 — 새로 걸면 **기존 대기 실행이 취소**된다 | GitHub concurrency 동작 | watchdog 은 실행 중·대기 중이면 절대 걸지 않는다. 마지막 실행 뒤 50분(연속 실패 중이면 6시간) 안에는 다시 걸지 않는다 |

실행 시간 상한(점검만, 바꿀 것 없음): GitHub 호스트 러너 작업 6시간·실행 35일. 가장 긴 작업은 places-world 350분·process countries 300분 — 모두 안쪽.
`GITHUB_TOKEN` API 한도 1,000회/시간/저장소 — watchdog 한 번 약 15~30회.

## 2. watchdog (`.github/workflows/watchdog.yml`, `scripts/watchdog.py`)
- 매시 37분(정각 부하 피함) + 수동. REST 만 쓴다. 권한은 `actions: write`·`issues: write`(클라우드 권한·비밀값 없음).
- 기준(마지막 성공으로부터): collect 3시간 · process 8시간 · tourapi·fx·coverage 27시간 · patterns 30시간 · places-layers(merge)·watch-sources·guards 8일.
- 3회 연속 실패(취소·건너뜀은 세지 않음) → 이슈 **'자동화 실패 모음'**에 한 번(같은 실패 묶음은 숨은 표시로 중복 막음).
- GitHub Actions 캐시(한도 10 GiB, 경고 8 GiB)·아티팩트(경고 400 MiB) 용량 → 이슈 **'저장 용량·비용 경고'**.
- 판단만 보기: 수동 실행 `dry_run=yes`.

## 3. 증분·갱신 (`sources/refresh_policy.json`)
이미 모은 것은 건너뛰고(조각·사이트·파일마다 마지막 성공 날짜), 아래 날짜가 지난 것만 다시 받는다 — 자주 바뀌는 것은 짧게, 이름은 길게.

| 칸 | 주기 | 어디 |
|---|---|---|
| 영업시간(공식 사이트 schema.org) | 14일 | `collectors.crawl`(예전 30일) |
| 소개·Wikidata 라벨 | 30일 | `place_desc.REFRESH_DAYS` |
| 공개 데이터셋 | 30일 · GTFS 7일 | `collectors.open_dataset`·`gtfs` |
| 사진(Commons 정보) | 90일 | `place_names.C_REFRESH` |
| 이름 | 상한 180일(실제로는 Wikidata 30일·OSM 월 1회로 더 자주) | — |

지역 순환(`sources/region_priority.json`): 같은 날짜끼리는 **사용자가 많이 찾는 나라부터** — Storage `collect/v1/demand.json`(나라별 검색 수, 있을 때) → 앱 일정 모델 도시의 나라(한·일·대만·홍콩·마카오·중국) → 커버리지 도시 30곳의 나라 → 전 세계.

## 4. 상한
- **저장 용량·비용**(`scripts/storage_guard.py`, coverage-report 가 하루 한 번 `gcloud storage du` 로 읽기만): 저장소 변수 `STORAGE_CAP_GIB`(기본 500)·`COST_CAP_USD_MONTH`(기본 15)·`STORAGE_USD_PER_GIB`(기본 0.026).
  80% → 이슈 '저장 용량·비용 경고'. 100% → `collect/v1/guards/usage.json` 의 `pause_collect` → collect 계획이 새 원본 수집을 멈춘다(가공·게시는 계속). 버킷·IAM 설정은 바꾸지 않는다.
- **출처별 속도 제한**: 등록부(`sources/registry.yaml`)의 `daily_limit`·`interval_s`(사이트당 최소 1초, robots `Crawl-delay` 가 더 길면 그것) — `common.Http` 가 지킨다. robots.txt 는 요청 때마다 실시간 확인.
  TourAPI 하루 900회 + 요청 간격 1초(이번에 추가, `TOURAPI_INTERVAL_S`). OSM(Geofabrik) 월 1회·동시 8개. Commons 15초·하루 1,500회. 시험: `tests/test_always_on.py`.
- 금지 도메인·우회 수집·프록시 회전·UA 위장·비공식 API 없음(`common.forbidden`, UA 에 저장소 주소).

## 5. 매일 커버리지 표
coverage-report 가 `docs/COVERAGE.md` 8절(언어별 이름·영업시간·사진·소개 채움률 14일 표 + 1·7·30일 대비 증감)을 바꿔 커밋한다(개수·비율만). `scripts/coverage_trend.py`.
