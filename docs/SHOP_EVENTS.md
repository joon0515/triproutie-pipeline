# 여행지 쇼핑·행사·팝업 — 자료와 판정 (256기, 2026-11-20)

대표 지시(11/20): 꼭 가야 할 가게와 위치, 거기서 사면 좋은 것·팁, 도시별 행사·축제와 팝업스토어. 코드 = `scripts/shop_index.py`(shop-1) · `scripts/events_layer.py`(events-1) · `scripts/tourapi_daily.py` 0단계(searchFestival2). 앱 묶음 = `kit_1114/CC_bench/shop256`.

## 1. 쓰는 자료(공개·공식만)
| 자료 | 무엇에 | 라이선스·표시 |
|---|---|---|
| Overture Maps places(공식 S3 익명) | 쇼핑 분류·`brand.wikidata`(체인 식별)·confidence | CDLA-Permissive-2.0 · AllThePlaces CC0 · Foursquare Apache-2.0 |
| 우리 수집 층 col_*(OSM·FSQ OS Places·Wikidata) | 매장 자체 QID(상징 매장)·OSM `brand:wikidata`(열 `bwd`, 있으면) | ODbL · Apache-2.0 · CC0 |
| Wikidata 캐시 | sitelinks 수 | CC0 |
| 학습 통계(scripts/patterns) | 나라 안 '쇼핑' 언급 비율 · 브랜드 QID 언급 수 이력 `brand_hist.json` | 대표 허락 학습 전용. 숫자만, 원문·출처는 앱에 보이지 않음 |
| `sources/shop_facts.yaml` | 면세 조건·개점 연도 같은 브랜드 공식 공개 사실 | 공식 쪽 주소 + 확인 날짜 + robots 확인 결과를 사실마다 저장 |
| 한국관광공사 TourAPI searchFestival2 | 한국 행사·축제 | 공공누리(사실 값). 사진은 1·3유형만 |
| 交通部觀光署 活動(EventList.json) | 대만 행사 | **candidate** — 4절 |
| 운영사·시설 공식 사이트 schema.org Event | 공식 공지 행사·팝업 | 기존 `collectors.crawl_site`(robots·쉼 그대로)가 모으는 `ev` — 새 수집기 없음 |

## 2. 판정식(앱 '?' 설명과 같음)
- 점수 = round(100 × (0.30·conf + 0.30·sitelinks + 0.20·체인 규모 + 0.20·쇼핑 언급 비율)). conf = (Overture confidence − 0.5) ÷ 0.5, sitelinks = ln(1+n) ÷ ln(101)(매장 QID, 없으면 브랜드 QID × 0.5), 체인 규모 = min(나라 안 매장 수, 20) ÷ 20. 언급 비율은 그 나라 표본 30일 미만이면 빼고 다시 나눈다.
- 꼭 갈 곳(must) = ① 사실 파일의 1호점 등 ② 매장 자체 위키 문서 sitelinks ≥ 3 ③ 점수 ≥ 50 이고 브랜드 sitelinks ≥ 20 인 체인의 도시 칸(약 5 km) 1등 매장 하나.
- 트렌드 = 브랜드 언급의 최근 30일 새 언급 ÷ 앞 30일 새 언급 − 1(앞 30일 10건 미만이면 내지 않음). 관광청 발표 트렌드는 등록된 출처 0 — 생기면 사실 파일에 url·날짜와 함께 넣는다.
- 근거 코드: iconic · first · chain · known · talk · trend · taxfree. 앱은 코드에서 5개 언어 문장을 만들고 출처·기준일을 붙인다(다른 사이트 문장 복제 없음).

## 3. 층 모양
- `shop-<cc>-NN.json.gz` schema shop-1: rows {id: [이름, 위도, 경도, 세부 분류, 브랜드 QID, 매장 QID, 점수, must, 근거[]]} · brands {QID: {n, c, sl, tr}} · facts {id: 사실·url·checked}.
- `events_kto.json.gz` · `events_tw.json.gz` · `events_official.json.gz` schema events-1: rows {id: {n, s, e, la, lo, a, k festival|event|popup, src, u, cc}}. 끝난 행사는 싣지 않는다.
- publish_layers: kind `shop`·`events`(cc·bbox). `brands_gaz.jsonl.gz`(places/v1/shop/)는 학습기 이름 사전 — 앱에 보내지 않는다.

## 4. 팝업스토어
- 공식: 운영사·시설 공식 사이트 schema.org Event 중 이름에 팝업 낱말이 있는 것(k=popup) + 공공 행사 자료의 같은 낱말.
- 사용자 제보: 앱 제보 시트 → Firestore `popup_reports`(status pending) → 검수 화면(관리자) 승인 → `popups`. 승인 전에는 아무에게도 보이지 않는다. 규칙은 앱 묶음 BLOCKED.

## 5. 건너뛴 주소·확인 못 한 것
| 주소 | 왜 | 대신 |
|---|---|---|
| https://data.gov.tw/dataset/7778 (대만 활동 라이선스 쪽) | 이 환경 열기 도구가 robots 로 거부(2026-11-20) | 검색 결과 제목 '活動 - 觀光資訊資料庫' 확인, 파일은 7777 과 같은 배포 폴더. 사람이 授權方式 를 읽기 전까지 등록부 candidate(워크플로가 받지 않음) |
| 도시 열린데이터(뉴욕·파리 등 행사) | 이번에 약관을 읽지 않음 | 다음 기수: OPEN_DATA.md 방식으로 읽고 등록 |
| 일본 관광청·JNTO 면세점 목록 | 상업 조건 미확인(OPEN_DATA.md) | 브랜드 공식 쪽 사실만 |

## 6. 실행
- `.github/workflows/shop-index.yml` — 매주 수요일 + 수동, 8묶음. 끄기 `SHOP_INDEX_OFF=1`. 대만 행사는 등록부 active 일 때만.
- `tourapi_daily` — 매일 searchFestival2 2~3회(한도 900 안). 끄기 `TOURAPI_FEST_OFF=1`.
- 시험 `tests/test_shop_events.py`(15).
