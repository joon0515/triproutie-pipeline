# places-layers — 편의시설 층·이름 보충 층을 GitHub Actions 로 만드는 묶음 (2026-11-13, 파일만 — 저장소에 올리지 않았다)

기존 `triproutie-pipeline` 저장소(places-world)에 **더하는** 파일들이다. 같은 WIF(OIDC) 로그인·같은 KMS Ed25519 서명 키·같은 버킷을 쓴다.
대표 지시가 있을 때 아래 "올릴 파일"을 저장소에 복사한다(다른 파일은 건드리지 않는다).

## 올릴 파일
| 이 폴더 | 저장소 경로 | 하는 일 |
|---|---|---|
| `.github/workflows/places-layers.yml` | 같은 경로 | 수동 실행 워크플로(plan → build(지역 병렬) → finalize) |
| `scripts/layers_plan.py` | `scripts/` | 입력(groups) → 지역 matrix, tag 형식 확인 |
| `scripts/run_layers_region.sh` | `scripts/` | 지역 1개: PBF 받기 → 편의시설 층 → (선택) 이름 보충 층 → 업로드 |
| `scripts/region_tiles.py` | `scripts/` | 이름 보충 층에 쓸 세계 타일 목록(gs:// 주소) |
| `scripts/finalize_layers.sh` | `scripts/` | 층 파일 모아 목록 만들기 → KMS 서명 → layers.json 올리기 |
| `scripts/publish_layers.py` | `scripts/` | 목록(places-layers-1) 작성·서명·업로드(로컬 `--dry-run` 가능) |
| `tools/osm_amenities.py` | `tools/` | OSM PBF → 편의시설 층(amenities-1). `--split-deg 4` = 큰 나라를 4도 칸으로 |
| `tools/names_osm.py` | `tools/` | 팩·타일 + OSM PBF → 이름 보충 층(names-1). 타일 여러 개는 쉼표로 |
| `tools/layers_regions.json` | `tools/` | 지역 표(추출본 주소·묶음·세계 타일 지역) |
이미 저장소에 있는 것을 그대로 쓴다: `tools/canonical_fix.py`(서명용 정규 JSON), 변수 `GCS_BUCKET`·`KMS_SIGN_KEY`·`GCP_WIF_PROVIDER`·`GCP_SA_EMAIL`.
새 비밀값·새 권한은 필요 없다(서비스 계정은 이미 버킷 쓰기·KMS 서명 권한이 있다).

## 실행(Actions 탭 → places-layers → Run workflow)
- `tag`: 층 판 이름(예 2026-11-20). 타일 판과 따로 간다.
- `groups`: `priority`(일본 8개 지역·한국·대만·홍콩/마카오 = 11개) · `asia` · `europe` · `americas` · `oceania` · `all`, 또는 지역 이름을 쉼표로(`kr,tw`).
- `names`: `yes` 면 `world` 가 적힌 지역(지금은 일본 8개)에 이름 보충 층도 만든다.
- `world_tag`: 이름 층을 붙일 세계 타일 판. 비우면 `places/v1/manifest.json` 의 version.
끝나면 `gs://<bucket>/places/v1/<tag>/layers/*.json.gz` 와 서명된 `places/v1/<tag>/layers.json`, 최신 사본 `places/v1/layers.json` 이 생긴다.
앱(s62 tl_place_layers)은 `places/v1/layers.json` 만 본다. 되돌리기 = 이전 판의 `layers.json` 을 `places/v1/layers.json` 에 덮어쓴다.

### 목록 크기 한도와 2단 목록 (11/17)
- 서명 키는 Ed25519(PureEdDSA)다. KMS 는 본문을 그대로 받아 서명하고(요약값만 보내는 `--digest-file` 은 이 알고리즘에 쓸 수 없다) 한 번에 64 KiB 까지만 받는다. 앱도 `layers.json` 을 64 KiB 까지만 읽는다.
- 그래서 수집 층(`col_*`, kind `collect`) 항목은 `places/v1/<tag>/layer_parts/collect-NNN.json`(schema `places-layers-part-1`, 나라 단위 200개씩)에 싣고,
  `layers.json` 에는 `parts_path` 와 `parts: [{file, kind, n, rows, size, sha256, cc[], bbox}]` 만 넣는다. 검증 순서: 뿌리 서명 → 2단 목록 sha256·크기 → 층 파일 sha256·크기.
- `layers.json` 의 `schema`·`path`·`layers` 모양은 그대로다(다른 종류는 전처럼 `layers` 에 있음). 수집 층이 없으면 `parts` 키도 없다.
- 뿌리 목록이 그래도 62 KiB 를 넘으면 `publish_layers.py` 가 서명·업로드 전에 멈추고 종류별 크기를 알려 준다.
- 되돌리기는 같다(2단 목록은 판 폴더 안에 있어 `layers.json` 만 덮어쓰면 된다).

## 결과물 크기·시간(로컬 실측에서 옮긴 것)
- 일본 8개 지역: 편의시설 69KB~948KB, 지역당 15~47초(PBF 100~600MB). 이름 보충: 도시 팩 기준 56KB~303KB, 30초 안팎.
- 러너 1대가 지역 1개를 맡는다. 가장 큰 추출본(독일·프랑스 4GB대)도 추출은 6분 안쪽 — 시간은 대부분 내려받기다.
- 대륙 전체 추정은 `amenities/README.md` 5절.

## 인기 층(pop_*.json.gz)은 여기서 만들지 않는다
위키미디어가 같은 주소의 잦은 요청을 429 로 막는다(로컬에서도 도시 하나에 수 분). 따로 만든 파일을 `gs://<bucket>/places/v1/layers_extra/` 에 올려 두면
finalize 가 함께 목록에 싣는다(저장소의 `layers_extra/` 폴더에 넣어도 된다).

## 확인이 필요한 것 [미확인]
1. 추출본 주소: `tools/layers_regions.json` 의 Geofabrik 주소는 이 작업 환경에서 열어 보지 못했다(연결이 막혀 있었다). 첫 실행에서 받기에 실패한 지역은 그 지역만 실패로 끝난다 → 주소를 고친다.
2. 세계 타일의 지역 manifest 경로: `regions/<지역>.json` 을 먼저, 없으면 `parts/<지역>.json` 을 읽는다. 둘 다 없으면 이름 보충 층만 건너뛴다(편의시설 층은 만든다).
3. 한국·대만의 이름 보충 층: 지금은 만들지 않는다(`world` 비움). 한국 팩은 이름이 원래 한글이고, 대만은 한자 읽기(주음·병음)가 OSM 에 드물어 효과를 재 본 뒤 넣는다.
4. Geofabrik 이용 예절: 한 번에 최대 8개 지역, 실패 시 60초 쉬고 3번까지. 월 1회 실행을 권한다.

## 로컬 확인(이 폴더에서)
```
IN_TAG=2026-11-20 IN_GROUPS=priority python3 scripts/layers_plan.py     # matrix 11개 지역
bash -n scripts/run_layers_region.sh && bash -n scripts/finalize_layers.sh
python3 scripts/publish_layers.py --tag t --dir <층 폴더> --bucket b --tools <canonical_fix.py 폴더> --dry-run
```

## 11/15 추가 — 영업시간 층(hours-1)·사진 층(photos-1), 자동 실행
- 일정: `places-layers.yml` 이 **매월 2일**(UTC 17:40) 전 지역(all, 라오스 `la` 추가로 29개) 다시 만들고, **매주 월요일**(UTC 19:10) `mode=merge` 로 빌드 없이 최신 판에 공식 사이트 영업시간·사진 층을 다시 합쳐 서명한다. 수동 실행은 그대로(tag 를 비우면 `L-<날짜>`).
- 영업시간: 지역 빌드가 `tools/hours_osm.py` 로 세계 타일 행 ↔ OSM(opening_hours·wikidata, 80m+이름) 을 맞춰 `places/v1/<tag>/hours_src/` 에 올리고,
  finalize 가 `tools/hours_merge.py` 로 세계 수집 가공본(`collect/v1/processed/out/<run>/col_*` — 공식 사이트 JSON-LD openingHours·Wikivoyage)을 **같은 `oh` 칸**에 합친다(공식 > OSM > 그 밖, 다르면 `hd=1`).
  행 `{oh, src, url?, osm?, q?, hd?}`. OSM 층이 없는 나라는 `hours-col-<cc>.json.gz`.
- 사진: 수집 순환(collect, 매시)의 `commons_photos` 출처가 세계 타일 지역마다 `tools/photo_layer.py` 를 돌려 `collect/v1/layers/photos-<지역>.json.gz` 를 만들고 finalize 가 함께 싣는다.
  행 `{photo_url, photo_thumb, photo_credit, photo_license, photo_license_url?, photo_author?, photo_page?, photo_src: tourapi|commons, no_modify?}`. 한국은 TourAPI(공공누리 1·3유형) 먼저, 그다음 Commons(CC0·CC BY·CC BY-SA).
- 앱: `places/v1/layers.json` 의 `kind: hours | photos` 항목(bbox 포함)을 읽는 코드는 앱 팀 몫(s62 tl_place_layers 에 두 종류 추가).

## 11/17 추가 — 장소 소개 층(desc-1) — '왜 여기?' 첫 조각 (218기)
근거: kit_1114/CC_bench/gen_fix3/PLACE_DESC_PLAN.md(212기, 표본 47곳 실측: Wikidata 설명 ko 19% · en 36% · zh_Hant 9%).

| 파일 | 만드는 곳 | 출처·라이선스 | 칸 |
|---|---|---|---|
| `desc-wd-NN.json.gz` | process 의 `place-desc` 단계(`scripts/place_desc.py`) | Wikidata 설명, CC0. registry `wikidata_official` 의 QLever 끝점 | ko·en·ja·zh_Hans·zh_Hant |
| `desc-kto.json.gz` | 같은 단계(TourAPI 층 `kto_*` 의 `ov`) | 한국관광공사 TourAPI 4.0 overview 첫 문장, **공공누리 제1유형만** | ko |
| `desc-tw.json.gz` | 같은 단계(수집 층 `dsc` 칸) | 대만 관광서 소개 첫 문장, OGDL-Taiwan-1.0 | zh_Hant |
| `desc-osm-<지역>.json.gz` | places-layers 지역 빌드(`tools/desc_osm.py`) | OSM `description:<lang>`, **ODbL — 따로 파일** | 태그 언어대로 |

- 행: `{장소 id: {ko, en, ja, zh_Hans, zh_Hant: 글|null, q?(QID), osm?}}`. 파일 머리에 `src · license · credit · page`(원문 주소 틀: `https://www.wikidata.org/wiki/{q}`, `https://www.openstreetmap.org/{osm}`) · `langs`(칸별 개수).
  장소 id 는 수집 층 id 와 타일 id(`wid`·`kowiki:Q…`) 둘 다 싣는다. TourAPI 는 `kto:<contentid>`.
- **없는 언어는 null.** 다른 언어로 채우지 않고 번역·간체↔번체 변환·요약·생성을 하지 않는다. 꼬리표 없는 zh 는 글자로 간체·번체가 갈릴 때만 그 칸.
- 거르기: 4자 미만 · 이름/라벨과 같음 · 위키미디어 동음이의·분류 문서 · 주소 · 글자 깨짐 · GLOSSARY 금지 낱말(추정·어림·대략·estimated·約+숫자 등 — 원문을 고치지 않으므로 그런 글은 싣지 않음).
  긴 문단(TourAPI·대만·OSM)은 첫 문장만, 120자를 넘으면 버린다(중간에서 자르지 않음).
- QID 잇기: 수집 층 `wd`(Wikivoyage·Wikidata 씨앗) · 세계 타일 id 의 Q-id · 영업시간 원본 층(`hours_src`)의 `q`(OSM `wikidata` 태그). **이름 검색은 쓰지 않는다.**
- 받는 길과 robots: `www.wikidata.org/w/api.php`·`query.wikidata.org/sparql`·`*.wikipedia.org/w/`·`/api/` 는 robots.txt 가 `User-agent: *` 에 Disallow(2026-11-17 확인) → 쓰지 않는다.
  QLever(qlever.dev, robots.txt 없음) 에 QID 250개씩, 5초 간격(registry interval_s), 하루 3000회(desc_daily_limit). 캐시 `collect/v1/processed/desc/wd.json.gz`, 30일 지나면 다시 묻는다.
  ※ 기존 `tools/photo_layer.py`·`tools/names_osm.py` 는 아직 `www.wikidata.org/w/api.php` 를 부른다 — robots 기준으로 다시 볼 것(이번 PR 범위 밖).
- 게시: `places/v1/layers_extra/desc-*.json.gz` → process 의 publish 가 함께 싣는다. 앱 목록에서는 **2단 목록(`layer_parts/desc-NNN.json`)** 에 들어간다(앱 연결 전이라 뿌리 목록 64 KiB 를 아낀다). 끄기: 저장소 변수 `PLACE_DESC_OFF=1`.
- TourAPI: `tourapi_daily.py` 가 목록을 한 번 다 받은 뒤 매일 한도의 25%(`--ov-share`)를 먼저 `detailCommon2` 에 쓴다(관광지·문화시설, 제1유형, 제주·경주 먼저).
  약관: data.go.kr 15101578 '이용허락범위 제한 없음'(2026-11-17 확인). 제3유형(변경 금지)·2·4유형 소개는 받지 않는다. 영·일·중 서비스는 따로 활용 신청이 필요해 받지 않는다.

### 앱 표시(앱 팀)
- 그 언어 칸이 비면 소개 조각을 생략한다(다른 언어 글을 보여 주지 않는다). 우선순위: 공식(kto·tw) > Wikidata > OSM.
- 출처 줄: '설명: Wikidata (CC0)' / '한국관광공사 TourAPI 4.0 (공공누리 제1유형)' / '交通部觀光署 觀光資訊資料庫 (政府資料開放授權條款-第1版)' / '© OpenStreetMap contributors (ODbL)' — 파일의 `credit` 그대로. 장소 상세 '출처'에서 `page` 를 연다.

### 위키백과 첫 문단 — 판정: 이번에는 쓰지 않음(보류)
근거: docs 밖 `kit_1114/BB_mapmory/LICENSE_AUDIT.md` 24번(위키백과 요약문 = CC BY-SA, '표기만 보완', 요약 문장은 파생물), PR #24 의 `docs/PATTERNS_POLICY.md` 5절(BY-SA 자료는 `by-sa` 묶음으로 따로, 기본 모델에 섞지 않음, 앱에 넣을지는 대표·법무).
1. 받는 길이 없다: 위키백과 `w/api.php`·REST `/api/` 는 robots.txt Disallow. 덤프(`pages-articles`)는 위키 문법을 풀어야 해 '원문 그대로'가 아니다(틀·각주 처리 = 손댐).
2. 동일조건: '왜 여기?' 문장에 섞으면 그 문장이 CC BY-SA 개작물이 된다.
3. 쓰게 될 때의 표시(대표 승인 뒤): `desc-wp-*` 따로 파일, `license_class: by-sa`. 첫 문단을 고치지 않고 통째로, '왜 여기?' 문장과 **떨어진 문단**으로만 보여 주고
   바로 아래에 '출처: 위키백과 「문서 제목」 기여자, CC BY-SA 4.0' + 문서 링크 + 라이선스 링크(https://creativecommons.org/licenses/by-sa/4.0/). 자르거나 요약하지 않는다.
   registry `wikipedia_lead`(candidate) 에 같은 내용을 적어 두었다.
