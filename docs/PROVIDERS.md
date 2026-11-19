# AI 공급자 목록 — 이름·설명 채움(scripts/ai_fill.py) (260기, 2026-11-20)

원칙: 공식 엔드포인트만. 비공식 재판매처·차단 회피·여러 계정 돌려쓰기 없음. 키는 저장소 secret 이름으로만 다루며 로그·파일에 값을 쓰지 않는다. 앱 사용자 데이터는 보내지 않고 공개 장소 데이터(이름·분류·도시·나라·출처 칸)만 보낸다.
한도는 공식 문서에서 읽은 값만 '확인'으로 적고, 나머지는 '미확인'(코드 기본값은 보수적으로 낮춘 값, 환경 변수 `AI_<이름>_RPM`·`_RPD`·`_MODEL` 로 바꿈). 확인일 2026-11-20.

| 공급자 | secret | 무료 한도(공식) | 상업적 이용 조건 | 확인 |
|---|---|---|---|---|
| Gemini API 무료 등급 | `GEMINI_API_KEY` | 한도는 RPM·TPM·RPD 3축, **프로젝트 단위**, RPD 는 태평양 시간 자정 초기화. 숫자는 문서에 없고 AI Studio 에서만 보인다 → 코드 기본값 RPM 10·RPD 250(보수적, 미확인). 무료 모델: gemini-3.5-flash·gemini-3.1-flash-lite·gemini-2.5-flash 등(가격표) | 약관: 무료 서비스는 입력·출력이 제품 개선에 쓰이고 사람이 읽을 수 있음(개인·민감 정보 제출 금지 → 우리는 공개 장소 데이터만). 결과물 소유권은 Google 이 주장하지 않음. 경쟁 모델 개발에 쓰기 금지. '상업 이용 허용' 문구는 없고 '전문·업무 목적 개발자용'. 유료 등급은 제품 개선에 쓰지 않음 | 한도 숫자 미확인, 조건 확인 |
| Cerebras(선택) | `CEREBRAS_API_KEY` | Free Trial 표: gpt-oss-120b·qwen-3.8-27b 모두 RPM 5, 분당 입력 토큰 30K(전체 90K), 시간당 100만·하루 100만 토큰. 일 요청 수는 문서에 없음 | 미확인(약관 원문 확인 못 함) | 한도 확인, 상업 조건 미확인 |
| Cloudflare Workers AI(선택) | `CF_ACCOUNT_ID`, `CF_AI_TOKEN` | 하루 10,000 Neurons 무료, 매일 00:00 UTC 초기화. 초과분은 Workers Paid 에서 1,000 Neurons 당 0.011달러(우리는 넘기지 않음) | 가격 문서에 상업 이용 문구 없음 → 미확인 | 한도 확인, 조건 미확인 |
| Groq(선택) | `GROQ_API_KEY` | 미확인(공식 한도 문서를 이 환경에서 열지 못함). 코드 기본값 RPM 10·RPD 200 | 미확인 | 미확인 |
| NVIDIA NIM(선택) | `NVIDIA_API_KEY` | 미확인(FAQ 가 본문을 주지 않음). 코드 기본값 RPM 5·RPD 100 | 미확인 — 개발·시제품용일 수 있어 대표 확인 전에는 켜지 않기를 권함 | 미확인 |

- **GitHub Models 는 뺐다.** 대표 결정(무료 사용은 시험·시제품용). 공식 문서에도 2026-07-30 부로 완전 종료·추론 API 사용 불가로 적혀 있다.
- 모델 이름 기본값(Gemini gemini-3.1-flash-lite · Cerebras gpt-oss-120b 는 공식 문서에서 확인, 나머지 Groq·Cloudflare·NVIDIA 는 미확인 — 계정에서 쓸 수 있는 모델로 `AI_<이름>_MODEL` 지정).
- 라우터: 켜진 공급자만(secret 이 모두 있어야 켜짐) → 앞에서부터 순서대로(gemini → cerebras → groq → cloudflare → nvidia). 분당·일당 한도를 코드가 먼저 지키고, 429 는 Retry-After 만큼(15분 넘으면 오늘은 끔) 쉬게 한 뒤 다음 공급자로. 401·403 이면 그 공급자는 오늘 끔. 오류 본문은 기록하지 않는다.

## 하루 처리량
- 한 요청 = 장소 15곳. Gemini 기본 RPD 250 → 이론상 하루 3,750곳이지만, 하루 상한 기본 **1,500곳**(`AI_MAX_PLACES`·`--max-places`) · 실행 50분 예산.
- 공급자를 더 켜도 상한 1,500곳은 그대로(대표가 올리기 전). 우선 도시(sources/priority_cities.json 순서)의 일정 장소부터, 이름·설명이 이미 있는 장소는 건너뜀.

## 결과·출처
- Storage `processed/ai_fill/out/aifill-<날짜>-NNNN.json.gz`(schema `aifill-1`), 행 = {ko,en,ja,zh_Hans,zh_Hant(빈 칸만), d:{언어: 설명}, src:'ai', provider, model, date, city}. 진행 위치 `processed/ai_fill/state.json`(1MB 미만)·처리한 장소 `done.json.gz`(같은 장소 두 번 처리 금지).
- 이 층은 아직 앱 목록(publish_layers)에 싣지 않는다. 싣기 전에 앱이 'AI 번역·AI 요약' 표시와 우선순위(Wikidata·OSM·Overture 이름이 먼저)를 정해야 한다 — 후속 작업.
- 겹침 확인: gen_fix3 PLACE_DESC_PLAN 은 Wikidata·TourAPI 설명(`scripts/place_desc.py`, 기계 번역 없음)이고 이 문서는 그 빈칸을 AI 로 메우는 별도 층이다. 같은 칸을 두 번 만들지 않도록 place_desc 의 desc-* 층에 설명이 있는 장소는 AI 설명을 받지 않고(이름 빈칸만), 이름도 다 있으면 후보에서 뺀다.

## 앱 묶음(publish) 연결 (11/20)
- ai-fill 실행 끝에 `ai_fill.py export` 가 Storage `processed/ai_fill/out/*` 전부를 `layers_extra/names-ai-NNN.json.gz`(schema names-2)·`desc-ai-NNN.json.gz`(desc-1)로 바꿔 올린다. 다음 process 게시(finalize_layers)가 layers_extra 를 그대로 싣는다.
- 우선순위: 기존 출처(Wikidata·OSM·Overture·수집 열) 이름·설명이 있는 칸은 내보낼 때 뺀다. 목록 항목에 `prio: 9`(AI)를 달아 앱도 같은 순서로 쓰게 한다(출처 파일 사이 충돌 시 AI 가 마지막).
- 출처 표시: 문서 src='ai'·license='AI-generated'·credit, 행 ai='provider|model|date'. 앱은 이 칸이 있으면 'AI 번역'·'AI 요약' 표시.
