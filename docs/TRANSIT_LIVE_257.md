# 대중교통 실시간 도착 · 요금 (257기, 2026-11-20)

앱 묶음: 로컬 `kit_1114/CC_bench/transit257/`(tl_t257·tl_t257_ui + ItineraryView ops). 이 저장소: 서버 함수 `functions/transit_fn.js`, 팩 `scripts/transit_packs.py` + `.github/workflows/transit_packs.yml`.
기존 것은 그대로 쓴다: 서울 버스·지하철, 홍콩 MTR·KMB, 대만 TDX, 일본 ODPT 운행 정보 = 앱 `tl_leg_live`(TlLegLiveChip, 11/8 D20). 구간 등급 = `docs/TRANSIT_GRADES.md`.

## 1. 도시별 출처와 이용 조건(공식 문서 확인, 2026-11-20)
| 도시 | 실시간 | 키(이름만) | 호출 한도·조건 | 표시 | 실시간 없을 때 |
|---|---|---|---|---|---|
| 서울 | 버스·지하철(기존) | `dataGoKrKey`·`seoulSubwayKey`(FF) | 개발계정 1,000/일(운영계정 증설) | 서울특별시·공공데이터포털(공공누리 1유형) | 요금만 |
| 부산 | 버스(BIMS `stopArrByBstopid`, 앱 직접) | `dataGoKrKey`(FF, 15092750 활용신청) | 공공데이터포털 이용허락 제한 없음 | 부산광역시 버스정보시스템 | 지하철 = 실시간·시간표 API 없음(15000522 폐기) |
| 도쿄 | 도영 지하철 = 역 시간표 + 노선 지연(ODPT 공개 `api-public.odpt.org`, 키 없음) · 도쿄메트로 등 운행 정보(기존 `odptConsumerKey`) | 없음 / `odptConsumerKey` | 도영 데이터 CC BY 4.0(ckan.odpt.org) | © 東京都交通局 / 公共交通オープンデータセンター | 시간표 팩(tokyo) |
| 타이베이·가오슝 | MRT 도착판(기존 TDX) | `tdxClientId`·`tdxClientSecret`(FF) | 토큰 1일 유효, 한도 = 회원 등급별 | 交通部 TDX | 요금 없음(TDX 운임은 키 필요) |
| 홍콩 | MTR·KMB·Citybus(기존, 키 없음) | 없음 | 상업 이용 허락·출처 표시 의무(data.gov.hk 약관) | DATA.GOV.HK 등 | MTR 요금표(공식 CSV) |
| 싱가포르 | 버스(LTA DataMall Bus Arrival v3, 서버) | Secret `LTA_ACCOUNT_KEY` | 하루 1,000만 회 이하, 상업 이용 허락 | Land Transport Authority DataMall | MRT 요금표(data.gov.sg, 2025-12-27 시행) |
| 런던 | 지하철·버스(TfL Unified API, 서버) | Secret `TFL_APP_KEY` | 피드당 분당 500회 이하, 등록 필요 | **Powered by TfL Open Data**(약관 문구) | 버스 £1.85·상한 |
| 뉴욕 | 지하철(MTA GTFS-realtime, 서버) | 없음(지하철) | **앱이 MTA 서버에서 바로 받게 하지 말 것 → 자기 서버에 받아서 내줌**(약관) · 1분 넘게 늦으면 '실시간 아닐 수 있음' 표시 · MTA 인가처럼 보이지 않게 | Data: MTA (not endorsed by MTA) | 요금 $3·7일 12회 상한 |
| 시애틀 | 버스·Link(OneBusAway Puget Sound, 서버) | Secret `OBA_KEY` | Transit Data Terms of Use 동의, 신청 뒤 20영업일 | OneBusAway / Sound Transit | 요금 $3 |
| 파리 | 지하철·RER·버스(IDFM PRIM 다음 통과 단위 요청 + 정류장 찾기 Navitia, 서버) | Secret `IDFM_APIKEY` | 새 토큰 하루 1,000회(초당 5) — 정류장마다 60초 캐시, 데이터 갱신 1분 · ODbL | Île-de-France Mobilités (PRIM), ODbL | 요금 €2.55/€2.05·1일권 |
| 방콕 | 없음 | — | — | — | 시간표 팩(태국 전국 GTFS, CC BY 4.0 — 방콕 노선 포함 여부는 팩 결과로 확인) |

열지 못한 공식 페이지는 앱 묶음 `transit257/SKIPPED_URLS.md` 에 적었다.

## 2. 서버 함수(이름 지정 배포만, Actions 배포 목록에 넣지 않음)
`transitLiveMta`(키 없음) · `transitLiveTfl` · `transitLiveLta` · `transitLiveOba` · `transitLiveIdfm` — 각자 자기 Secret 만 묶는다(Secret 이 없으면 그 함수만 배포 실패, 나머지와 무관).
- 입력 = 역·정류장 근처 좌표(4자리로 자름)·수단·노선. 사용자 위치·주소가 아님. 로그인 사용자만.
- 캐시 = 같은 좌표·수단 30초(IDFM 60초), 정류장 목록 하루(MTA stops.txt·LTA BusStops), MTA 피드 8개 30초. 저장(Firestore) 없음.
- 응답 `{ok, src, stop, arrivals:[{route,dest,min,at,live}], attr, asof}` — `live:false` 는 운영사가 예측이 아닌 시간표 값을 준 것(OBA predicted=false, IDFM Aimed 만, LTA Monitored=0).
- 시험 `functions/test_transit_fn.js` 41/0(외부 호출 0). 실제 MTA 피드로 Grand Central 다음 열차를 받아 봄(11/20).

## 3. 팩(주 1회, places/v1/transit/ — storage.rules 변경 없음)
- `fares.json.gz`: 서울(거리비례 10km·5km마다 100원, 기후동행카드 단기권) · 뉴욕 · 시애틀 · 파리 · 런던(버스·상한) · 홍콩(MTR 역 쌍) · 싱가포르(거리표) · 도쿄 도영(역 쌍 IC) · 부산(링크만). 도시마다 `src[{url, checked}]`.
- `tt/<city>.json.gz`: GTFS 정적(열린 라이선스 cc0·cc-by·etalab 만 — ODbL·SA 는 파생 DB 공개 의무 때문에 섞지 않음) + 도영 역 시간표. calendar.txt 요일 기준(예외일 미반영 → 앱은 '시간표 기준').
- 시험 `tests/test_transit_packs.py` 7/0.
