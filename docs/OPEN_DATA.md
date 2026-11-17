# 각국 공공데이터 목록 — 라이선스를 직접 읽은 것만 (2026-11-17, 196기)

등록 규칙: 공식 라이선스 페이지를 직접 읽고(주소·날짜), robots 를 확인한 뒤에만 `sources/registry.yaml` 에 `status: active` 로 올린다.
이 표는 조사 결과다 — **registry.yaml 은 이 PR 에서 건드리지 않았다**(초안 PR #23 과 겹치지 않게). '읽음' 인 것만 다음 기수가 수집기를 붙인다.

## 이미 수집 중(등록부 active)
| 나라 | 출처 | 라이선스 | 표기 |
|---|---|---|---|
| 한국 | 한국관광공사 TourAPI 4.0 | 공공누리 1·3유형 | 한국관광공사 + 유형 |
| 대만 | 交通部觀光署 觀光資訊資料庫 | 政府資料開放授權條款 1.0 | 기관 이름 + 라이선스 |
| 캐나다 | 여행 권고 | OGL-Canada 2.0 | 정해진 문구 |
| 영국 | FCDO 여행 권고 | OGL v3.0 | 정해진 문구 |
| 여러 나라 | CKAN·data.europa.eu 자동 발견 데이터셋 | 데이터셋마다(기계 판정) | LICENSE_AUDIT 12번 — 사람이 읽은 근거 없음. 장소 층에 싣기 전 데이터셋별 확인 필요 |

## 새로 확인(2026-11-17) — 붙일 수 있음
| 나라 | 출처 | 라이선스(읽은 곳) | 조건 | 다음 일 |
|---|---|---|---|---|
| 세계 | GeoNames 덤프(지명·도시) | CC BY — https://www.geonames.org/export/ ("cc-by licence … give credit to GeoNames") | GeoNames 표기 + 링크 | 도시·나라 이름 사전(ODBL.md 권고와 같음). 덤프 주소 robots 확인 후 등록 |
| 싱가포르 | data.gov.sg | Singapore Open Data Licence v1.0 — https://data.gov.sg/open-data-licence (상업·비상업 사용, 배포·수정 허용) | 출처 고지 + 라이선스 최신판 링크 | 관광 데이터셋 고르기(호텔·명소·호커센터) → 데이터셋 주소별 robots |
| 프랑스 | DATAtourisme | Licence Ouverte 2.0 — https://www.datatourisme.fr/licence/ | 작성자(`HasBeenCreatedBy`)와 마지막 갱신일 표기 | **가입 후 흐름(flux) 만들기가 필요 → 대표**(계정 생성은 팀 금지) |

## 확인 못 함(받지 않는다)
| 나라 | 출처 | 왜 |
|---|---|---|
| 미국 | 국립공원청 NPS API | 문서 페이지에서 약관·한도를 읽지 못함(2017년 판 껍데기만 열림). API 키 필요 여부 미확인 |
| 일본 | 観光庁·JNTO·国土数値情報 | 공식 오픈데이터 위치·상업 조건 미확인(데이터셋마다 조건이 다르다고 알려짐) |
| 홍콩 | data.gov.hk | 약관 페이지 미확인. 홍콩천문대 자료는 상업 사용 금지(LICENSE_AUDIT 46번) — 기관별로 다름 |
| 세계 | OSM taginfo(영업시간 태그 수) | robots.txt 가 API 경로를 막음 → 열지 않음 |
| 세계 | Transitland · Nager.Date · 미국 국무부 RSS | 등록부 candidate 그대로(약관·robots 미확인) |

## 쓰지 않는 것(변하지 않음)
구글·네이버·카카오·다음·타벨로그·트립어드바이저·인스타그램·틱톡·유튜브에서 긁기, 비공식 API, 프록시 돌리기·UA 위장, 약관이 상업 사용을 막는 자료.
