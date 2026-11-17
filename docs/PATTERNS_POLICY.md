# 일정 패턴 수집 — 출처별 약관 판정 (198기, 2026-11-17)

> **법률 자문이 아님.** 공식 페이지를 읽고 정리한 것이다. 최종 판단은 대표·법무(변호사) 확인이 필요하다.
> 목적: 블로그·유튜브 같은 공개 일정에서 **숫자 분포만**(하루 시작·끝, 장소 수, 장소 간 간격, 식사 시각) 뽑아 생성기의 일정 모델(`itineraryModel`)에 덧댄다. 원문·주소·ID 는 저장하지 않는다.

## 판정 요약

| 출처 | 판정 | 코드 상태 | 핵심 근거 |
|---|---|---|---|
| YouTube Data API v3 | **사용 안 함** (서면 허락 전) | 받는 코드 없음(`sources.yaml` blocked) | 개발자 정책 III.E.4.h 파생 데이터·지표 금지, III.E.2.a 집계 금지 |
| 네이버 검색 API | **사용 안 함** (미확인) | 받는 코드 없음 | 약관 페이지를 이 환경에서 열지 못함(미확인). 결과는 요약문뿐이라 본문은 금지 도메인 |
| 카카오(Daum) 검색 API | **사용 안 함** | 받는 코드 없음 | 운영정책 제5조 30호·20호. 결과는 요약문뿐 |
| Wikivoyage 덤프(일정 문서) | **조건부** | 켜짐(`by-sa` 묶음으로 따로) | CC BY-SA 4.0. 통계에 동일조건이 미치는지 **불명확** → 기본 모델에는 덧대지 않음 |
| robots 허용 블로그·여행 사이트 | **조건부** (사이트마다) | 틀만 있음, 등록된 사이트 0 | 사람이 약관을 읽고 날짜를 적은 사이트만. robots·noai·TDM 유보 자동 확인 |

## 1. YouTube API Services

확인한 페이지(2026-11-17):
- Developer Policies — https://developers.google.com/youtube/terms/developer-policies (페이지 표기 "Last updated 2026-11-06 UTC")
- Terms of Service — https://developers.google.com/youtube/terms/api-services-terms-of-service (같은 날짜 표기)
- Developer Policies Guide — https://developers.google.com/youtube/terms/developer-policies-guide ("Last updated 2026-09-14 UTC")
- 할당량 — https://developers.google.com/youtube/v3/determine_quota_cost ("Last updated 2026-11-12 UTC")

**(a) 보관 기간·갱신 의무**
- III.E.4.d: 로그인 없이 받는 자료(Non-Authorized Data, 검색 결과·영상 설명은 여기에 해당)는 임시로만, "not longer than 30 calendar days".
- III.E.4.b: 통계도 Non-Authorized 이면 30일 넘게 저장 금지.
- III.E.4.e·f: 저장한 자료는 최신 자료와 맞게 유지, 화면에는 가장 최신 자료.
- ToS §24.3: 계약 끝나면 API 데이터 전부 삭제.

**(b) 파생 지표·집계**
- III.E.4.h: "must not … (ii) access or use API Data to create new or derived data or metrics."
- III.E.2.a: "Do not aggregate API Data except …" (같은 콘텐츠 소유자의 채널끼리만, 그 소유자만 보는 경우).
- III.L: 추가 지표·통계 저장은 **감사받은 분석 개발자가 따로 허락을 받은 경우만**(2026-06-01부터 할당량 늘리기 요청 절차).
- 정책 가이드: "don't create derived metrics and/or store data beyond what is permitted", "Merge or combine YouTube API data with any other data" 금지.
- ToS §16.1: API 데이터와 그 파생물은 YouTube 의 재산.

**(c) 허용 용도**
- ToS §3.1: API 클라이언트를 만들고 운영하는 데만. §7: 개인정보처리방침 게시 의무. III.A.1: YouTube 약관 링크 표시. III.F.2.a: 보여 줄 때 출처가 YouTube 임을 분명히.
- III.E.6·III.I.14: 긁기 금지, API 말고 다른 기술로 API 데이터를 받는 것 금지(= 자막 내려받기 도구·웹페이지 읽기 금지).
- ML/AI 학습에 대한 조항은 이 페이지들에 없음(미확인).

**판정: 사용 안 함.** 우리가 하려는 일(설명란 시각표 → 도시별 중앙값·분위수)은 III.E.4.h 의 "derived data or metrics" 와 III.E.2.a 의 "aggregate" 에 그대로 걸린다. 30일 보관 규칙도 '숫자만 남긴다'로 피해 가지 못한다(파생물 자체가 금지). III.L 허락을 받으면 다시 본다.
- 할당량 참고(허락을 받게 될 때): 하루 기본 10,000 단위, `search.list` 는 별도로 **하루 100회**, `videos.list` 1단위(최대 50개). → 하루 최대 영상 약 5,000개 검색 가능, 설명란에 시각표가 있는 비율을 5~10%로 보면 하루 250~500편이 상한(추정, 측정 아님).
- Secret 이름은 `YOUTUBE_API_KEY` 로 자리만 적어 두었다(지금 쓰는 코드 없음).

## 2. 네이버 검색 API

- https://developers.naver.com/products/terms/ , https://developers.naver.com/docs/common/openapiguide/ — 2026-11-17 이 환경에서 **열지 못함(미확인)**.
- 알려진 사실(코드로 확인한 것): 블로그 검색 API 결과는 제목·요약문·주소뿐이다. 일정 숫자를 뽑으려면 본문(blog.naver.com)을 읽어야 하는데 naver 는 금지 도메인이고, 11/8 팀 결정(robots 가 AI 수집을 막는 후기 플랫폼 제외)과도 겹친다.
- **판정: 사용 안 함.** 키 발급도 권하지 않는다(약관 확인 전, 그리고 확인해도 요약문만으로는 숫자를 못 뽑음).

## 3. 카카오(Daum) 검색 API

- 운영정책 https://developers.kakao.com/terms/latest/ko/site-policies (시행 2026-04-20, 2026-11-17 확인)
  - 제5조 20호: 사용자 환경 개선 외 목적으로 데이터를 캐시하거나 최신 상태로 유지하지 않는 행위 금지.
  - 제5조 30호: 사전 승낙 없이 "복사, 복제, 변경, 번역, 출판…" 금지.
  - 데이터베이스화·분석을 직접 다루는 조항은 없음.
- Daum 검색 공통 문서 https://developers.kakao.com/docs/latest/ko/daum-search/common — 저장·가공 조건 언급 없음, 쿼터만.
- **판정: 사용 안 함.** 결과는 요약문뿐이고 본문은 금지 도메인(daum·kakao·naver 블로그). '변경' 금지가 통계 가공에 미치는지 불명확 → 사전 승낙 없이 쓰지 않는다.

## 4. robots 허용 블로그·여행 사이트 판정 방법

코드(`scripts/patterns/sources.py` `usable`·`site`)가 강제하는 것:
1. 금지 도메인(instagram·tiktok·naver·kakao·daum·tabelog·google·tripadvisor·youtube)은 `common.forbidden` 이 버린다.
2. robots.txt 를 요청마다 실시간 확인(`common.Http`, UA `TripRoutie-collector`), Crawl-delay·사이트당 최소 간격·하루 한도·403/429 이면 쉼.
3. `/.well-known/tdmrep.json`(TDM 유보 선언)이 있으면 그 사이트 전체를 읽지 않는다.
4. 쪽마다 `<meta name="robots" content="noai|noml|none">`·`tdm-reservation=1` 이면 그 쪽을 읽지 않는다.
5. 씨앗 사이트 안, `path_re` 에 맞는 일정 쪽만, `max_pages` 까지.
사람이 할 것(코드가 대신 못 함): 사이트 약관을 읽고 '자동 수집 금지'·'상업 이용 금지'가 없는지 확인 → `sources.yaml` 에 `terms_url`·`terms_checked`(날짜)·`seeds` 를 적고 `status: active`. 그 전에는 받지 않는다(시험이 지킴). 지금 등록된 사이트는 **0곳**.

## 5. Wikivoyage 등 공개 일정 자료

- 라이선스: CC BY-SA 4.0 (등록부 `sources/registry.yaml` wikivoyage 항목, 118기 2026-11-15 확인. 이번에 Wikimedia 약관 페이지는 열지 못함 — 재확인 미확인).
- robots: dumps.wikimedia.org/robots.txt 없음(404 = 허용, 2026-11-17 확인). 덤프만 받고 위키 페이지·API 는 부르지 않는다.
- 동일조건(ShareAlike)이 통계에 미치는가: **불명확.** CC FAQ(https://creativecommons.org/faq/ , 2026-11-17)는 "CC licenses do not contractually impose restrictions on uses of a work where there is no underlying copyright" 라고 하지만, 우리가 뽑는 '하루 장소 수' 같은 숫자가 저작물의 개작인지, 데이터베이스권이 붙는지(같은 FAQ: sui generis 권리도 같은 조건)는 나라마다 다를 수 있다.
- 그래서: Wikivoyage 표본은 `license_class: by-sa` 로 **따로 계산·따로 저장**(`patterns.by-sa.json`, `itinerary_model_patch.by-sa.json`)하고, 기본 덧대기(`itinerary_model_patch.json`)에는 섞지 않는다. 출처 표기 "Wikivoyage contributors (CC BY-SA 4.0)" 를 산출물에 남긴다. 앱에 넣을지는 대표·법무 판단.
- 실제 양(2026-11-17, 영어 덤프 132MB 전체 조사): 날 제목(Day 1…)이 2개 이상인 문서 **16편**, 그중 숫자가 뽑힌 것 5편·22일, 일본어·중국어 덤프 0편. 우리 41개 도시에 해당하는 표본은 사실상 없음.
- **판정: 조건부**(따로 두기·표기). 양이 너무 적어 모델 값을 바꿀 수준은 아니다.

## 6. 저장하는 것 / 하지 않는 것

- 저장: Storage `patterns/v1/` 에 분포 JSON·덧대기 JSON·하루 숫자 표본(도시 열쇠·나라·유형·숫자)·상태(덤프 해시).
- 저장하지 않음: 글·자막·설명 전문, 제목, 장소 이름, 주소·URL·영상 ID·채널. 장소 이름은 실행 중 메모리에서 우리 장소 자료와 맞춰 **종류**(sight·food·cafe…)로만 바꾸고 버린다. `tests/test_patterns.py` 가 산출물에 원문 조각이 없는지 검사한다.
- 표본 8개 미만 칸은 덧대지 않는다(기존 값 유지). 기존 값의 표본이 더 많으면 기존 값 유지, 지역 값을 빌려 쓰던 칸(`src: region_*`)만 바꾼다.
