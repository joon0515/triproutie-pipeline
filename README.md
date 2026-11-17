# triproutie-pipeline (공개 코드 — 데이터·규칙 없음)
- `.github/workflows/places-world.yml` — 수동 실행만. 지역 매트릭스 → 스캔(Overture 공개 S3 + Foursquare OS Places Iceberg) → H3 타일 → 빌드 → Firebase Storage 비공개 업로드 → Cloud KMS 서명 manifest.
- `tools/world_region.py`·`world_regions.json`·`canonical_fix.py` — 공개 가능한 스캔·타일·정규 JSON 코드.
- `private_enc/` — 분류·필터·랭킹·큐레이션·음역 사전 묶음의 **암호문**(AES-256-GCM, 데이터 키는 KMS RSA 로 감쌈; 서비스 계정만 풀 수 있음).
- 인증: Workload Identity Federation(키 파일 없음). 비밀(이름만): Actions secret `FSQ_TOKEN`·`DATA_GO_KR_KEY`, 함수용 Secret Manager `DATA_GO_KR_KEY`·`JUSO_KEY`·`JUSO_COORD_KEY`(functions/ defineSecret). 설정: `SETUP_KO.md`, `cloudshell_setup.sh`.
- 데이터 출처 표시: Overture Maps Foundation(CDLA-Permissive-2.0), Foursquare OS Places(Apache-2.0, NOTICE), AllThePlaces(CC0).
- `.github/workflows/tourapi_daily.yml` — 매일 00:20 KST(+수동). 한국관광공사 TourAPI 4.0(KorService2) 전국 16개 시도(법정동 lDongRegnCd)·269개 시군구 × 관광지·문화시설·레포츠·숙박·쇼핑·음식점을 하루 900회 안에서 이어받기. 1단계 목록(약 130회, 첫날) → 2단계 소개(detailIntro2, 관광지·음식점 먼저, 앱에 있는 곳→소도시→서울·부산) → 3단계 요금(12·28)(`scripts/tourapi_daily.py`, 모의 시험 `tests/test_tourapi_daily.py`).
  - 비밀: `DATA_GO_KR_KEY`(저장소 Actions secret, 없으면 GCP Secret Manager 같은 이름을 시도). 한도 바꾸기: 변수 `TOURAPI_DAILY_LIMIT`.
  - 저장소에는 `tourapi/state.json`(단계·작업 위치·오늘 호출 수)과 우선순위 목록 `tourapi/priority_cids.json`(앱 데이터에 있는 contentid)만 커밋. 내용은 Storage 비공개: `tourapi/v1/store/area_*.json`(누적), `tourapi/v1/agg_tourapi.json`(앱 데이터 모양), `places/v1/layers_extra/kto_<시도>.json.gz`(schema `tourapi-1` — 다음 places-layers 실행 때 서명 목록에 실림).
  - 사진은 공공누리 제1·3유형만(3유형 `no_modify`, 자르기 금지), 출처 '한국관광공사 TourAPI 4.0' 을 파일·목록 attribution 에 표기.
- 처리량·한도·일정: `docs/THROUGHPUT.md` — 가공은 partition → countries(나라 8 조각 동시에) → merge → publish.

## 워크플로 한눈에 (251기 감사, 2026-11-18)
GitHub 예약(schedule)은 이 저장소에서 자주 빠진다. 그래서 collect `chain` 과 `watchdog`(sources/watchdog.json `max_age_h`)이 다시 건다.
| 파일 | 언제 | 하는 일 |
|---|---|---|
| collect.yml | 매시 23분 예약 + 끝나면 스스로 다음 회차(chain) | 출처×나라 조각 수집 → Storage collect/v1/raw, chain 이 process·watchdog·tourapi·places-layers 보충 |
| process.yml | collect 뒤 + 3시간 예약 + 수동 | partition → countries → merge → place-desc → publish(품질 관문·KMS 서명) |
| places-layers.yml | 월 2일 full·all, 월요일 merge + 수동 | OSM 편의시설·이름·영업시간 층 |
| places-world.yml | 수동만 | 세계 타일(Overture·FSQ) |
| tourapi_daily.yml | 매일 KST 00:20 | 한국관광공사 TourAPI |
| fx_daily.yml | 매일 KST 06:10 | 참고 환율 |
| coverage-report.yml | 매일 KST 05:47 | 커버리지 보고(이슈 '커버리지 일일 보고') |
| patterns.yml | 매일 + 수동 | 일정 패턴 숫자 분포(docs/PATTERNS_POLICY.md) |
| rec-index.yml | 주 1회(화) | 추천 지수(docs/RECOMMEND_INDEX.md) |
| wikidata-dump.yml | 주 1회(금) | Wikidata 덤프 조인 → place-desc 캐시 |
| watch-sources.yml | 주 1회(수) | 출처 약관·링크 감시(이슈) |
| climate-normals.yml | 수동만 | 도시별 월 평년값 |
| deploy_functions.yml | functions/ push + 수동 | Cloud Functions 배포(이름 지정) |
| guards.yml | push·PR + 주 1회 | 단위 시험 전부 + 라이선스·출처 표기 |
| watchdog.yml | 매시 37분 | 오래 성공 없는 워크플로 다시 걸기·keepalive |
| failure-digest.yml | 다른 워크플로 실패 때 | 이슈 '자동화 실패 모음'에 한 줄 |
