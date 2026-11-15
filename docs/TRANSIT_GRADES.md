# 이동 구간 등급 제안 + GTFS 보관 명세 (2026-11-15, 데이터 보강팀)

감사(X_audit) 결과: 일정 203구간 모두 `estimated=true`. OSM 노선 그래프를 탄 47구간(23%)도 시간은 모델값이라 '추정'으로 묶였다.
'실데이터냐 아니냐' 두 칸으로는 도쿄·오사카처럼 노선 근거가 있는 도시와 직선 추정뿐인 도시가 구별되지 않는다 → 세 등급으로 나눈다.
생성기 연결은 앱 팀 몫. 여기는 **칸 이름과 뜻만** 정한다.

## 1. 등급 (구간 `leg.grade`)
| 등급 | 값 | 조건 | 화면 문구(제안) |
|---|---|---|---|
| 실측 | `measured` | 시간표(GTFS `stop_times`)에서 두 정류장 사이 실제 운행 시간 + 배차 간격 평균 대기, 또는 공식 확인 구간(앱 DB legs 의 공식 5개) | "시간표 기준" |
| 노선 근거 | `route` | 실제 노선망 위의 경로(OSM 노선 그래프 `osm_route`, 또는 GTFS `shapes`·정류장 순서)는 있으나 시간은 평균 속도 모델 | "평소 기준" |
| 거리 추정 | `distance` | 직선거리 × 1.3 (도보·차량 속도 모델) | "대략" |

- 지금 `estimated` 칸은 그대로 두고(호환) `estimated = (grade != 'measured')` 로 맞춘다.
- 출시 기준 제안(감사 §4-4): "추정 10% 미만" 대신 **`route`+`measured` 50% 이상 · 나머지는 화면에 '대략' 표기**.

## 2. 구간 칸 (생성기 → 일정 JSON `legs[]`)
| 칸 | 형 | 뜻 |
|---|---|---|
| `grade` | `measured`\|`route`\|`distance` | 위 등급 |
| `mode` | `walk`\|`transit`\|`car`\|`taxi` | 이동 수단 |
| `min` | int | 걸리는 분(대기 포함) |
| `wait_min` | int? | 평균 대기(배차 간격 ÷ 2), `measured` 일 때만 |
| `src` | string | `gtfs:<피드 id>` · `osm_route` · `model` · `legs_db` |
| `feed_lic` | string? | GTFS 피드 라이선스(표기용) — `gtfs:` 일 때 필수 |
| `feed_attr` | string? | 표기 문구(예 "© 東京都交通局 CC BY 4.0") |
| `from_stop` / `to_stop` | string? | GTFS stop_id(`<피드>:<stop_id>`) |
| `route_name` | string? | 노선 이름(예 "都営バス 都01") |
| `asof` | date? | 피드 받은 날 — 오래되면(90일+) `route` 로 내린다 |

## 3. GTFS 보관(파이프라인이 매주 채움)
- 수집기: `scripts/collect/collectors.py: gtfs` (등록부 `mobility_gtfs`, 조각 8개, 피드마다 7일에 한 번).
- 보관 대상: 등록부 `mobility_gtfs.targets` 의 7개 도시(서울·부산·교토·도쿄·오사카·파리·방콕) bbox 와 피드 bbox 가 겹치는 피드. 이 피드는 먼저 받고 원본 zip 을 남긴다.
- 경로: `gs://<bucket>/collect/v1/gtfs/<라이선스 갈래>/<피드 id>/<YYYY-MM-DD>.zip` — 갈래 = `cc0` · `cc-by` · `cc-by-sa` · `odbl` · `etalab` · `other-open`.
  ODbL 피드로 만든 파생 자료는 ODbL 로 공개해야 하므로 갈래를 섞지 않는다.
- 목록: `gs://<bucket>/collect/v1/gtfs/index/<조각>.json` = `{피드 id: {city, key, license, lic_class, provider, cc, bbox, bytes, sha256, fetched, stops, stations, routes, types}}`.
- 받는 피드는 Mobility Database 카탈로그에서 **키가 필요 없고 열린 라이선스로 자동 판정된 것만**(기존 규칙). 등록부 `extra_licenses` 에 대표가 허락한 라이선스만 더한다.

## 4. 2026-11-15 카탈로그 확인 결과(files.mobilitydatabase.org/feeds_v2.csv 를 직접 받아 걸러 봄)
| 도시 | 받는 피드(열린 라이선스·키 없음) | 빠진 것 |
|---|---|---|
| 도쿄 | 都営バス(mdb-3175·3176, CC BY 4.0) + 구·시 커뮤니티 버스 20여 개(ODPT·JBDA, CC BY/CC0) | 지하철·JR(ODPT 키 필요) |
| 파리 | SNCF(tdg-67595, ODbL)·Zenbus·BlaBlaCar Bus(ODbL) | **IDFM 지하철·RER·버스 전체**(tdg-80921 등, 'Licence Mobilités' — 자동 판정 밖, 허락 필요) |
| 방콕 | 태국 교통정책계획청 전국 GTFS(mdb-1831, CC BY 4.0, bbox 가 태국 전체) — 방콕 노선 포함 여부는 첫 수집 뒤 `index` 의 routes 로 확인 | BTS·MRT 단독 피드 없음 |
| 서울·부산 | 없음(카탈로그에 한국 피드 0) | 국가대중교통정보센터·data.go.kr 자료는 GTFS 형식·라이선스 확인 전 |
| 교토·오사카 | 없음(카탈로그 기준) | 교토시버스·지하철은 ODPT(키·약관 동의 필요), 오사카메트로 공개 GTFS 확인 못 함 |

## 5. 앱 팀이 할 일(이 PR 범위 밖)
1. `index/*.json` 에서 도시별 피드를 골라 `stops`·`stop_times` 로 정류장 쌍 → 운행 분·배차 간격 표를 만들고(로컬 전처리), 구간이 그 표에 닿으면 `measured`.
2. 지금 `osm_route` 를 탄 구간은 `route`, 나머지는 `distance`. 채점기(qa/realism) 집계를 세 등급으로.
