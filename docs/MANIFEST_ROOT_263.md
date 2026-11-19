# 뿌리 목록(layers.json) 크기 고정과 옛 키 전환 계획 (263기, 2026-11-20)

## 문제
뿌리 목록은 KMS 서명 한도(64 KiB, Ed25519 는 본문 그대로 서명)와 앱 읽기 한도(64 KiB) 안에 있어야 한다.
253기가 모든 종류를 2단(layer_parts/<kind>-NNN.json)으로 나눴지만 호환용 옛 항목(`layers`)을 남은 자리까지 채워서, 뿌리가 55.6 KB 까지 찼다.
사진·ai-fill·쇼핑·교통 층이 늘면 다시 한도를 넘는다.

## 지금 규칙 (이 PR)
- 뿌리 = 머리 + 2단 목록(장, 많으면 3단 색인)의 `{file, kind, n, rows, size, sha256, cc?, bbox?}` + 옛 항목(고정 예산 안에서만).
- 목표 `ROOT_TARGET` 44 KiB, 가드 `ROOT_GUARD` 48 KiB. 가드를 넘으면 서명 전에 게시가 실패한다(publish_layers.py).
- 장 목록이 `ROOT_TARGET - 16 KiB` 를 넘으면 종류별로 색인(3단)으로 접는다 → 뿌리는 (종류 수 × 색인 장 수)에만 비례. 색인 한 장은 장 약 280개(층 파일 약 6만 개)를 담는다.
- 옛 항목은 남는 자리 안에서 작은 종류부터 통째로 싣고, 못 실은 종류는 `legacy_omitted` 에 적는다(실패 아님, 일부만 싣지 않음).
- 2026-11-19 크기 기준: 뿌리 약 13 KB. 옛 항목은 hours·names·photos 가 남고 amenities(약 42 KB)는 `legacy_omitted`(2단 목록에는 있음).
- CI 가드: `scripts/guards/root_size.py`(guards.yml) 가 층 수 1~200배 합성 목록으로 뿌리를 만들어 48 KiB 초과 시 실패. 시험 tests/test_root_size_guard.py·test_publish_layers.py.

## 주의: 앱이 아직 옛 키만 읽는 동안
지금 앱(tl_place_layers)은 amenities 를 뿌리 `layers` 에서만 읽는다. 이 PR 이후 amenities 는 `legacy_omitted` 라서 앱이 parts 를 읽기 전까지 보이지 않는다.
앱 묶음 `kit_1114/CC_bench/manifest263`(2단·3단 읽기, 서명·sha256 검증, 옛 키 있으면 그대로 사용)를 FF 에 적용하면 해소된다. 적용 전에 오늘 판의 amenities 가 필요하면 `ROOT_TARGET` 을 임시로 올리지 말고(가드 48 KiB 유지) 앱 묶음 적용을 앞당긴다.

## 옛 키 단계적 제거
| 단계 | 조건 | 뿌리 변화 |
|---|---|---|
| 0 (지금) | 이 PR 머지 | 옛 항목은 고정 예산 안에서만, 못 실으면 legacy_omitted |
| 1 | manifest263 이 FF 앱에 적용·빌드 통과 | 변화 없음. 앱은 parts 를 읽고, 옛 항목이 있으면 같은 파일을 중복 없이 합쳐 읽음 |
| 2 | 1단계 앱이 출시 채널(Play·App Store)에서 사용자 대부분(예: 90% 이상)에 퍼짐 | 환경변수 `LEGACY_ROOT=off` 한 판 시험 게시(옛 항목 0, 뿌리 약 10 KB) — 되돌리기는 이전 태그 layers.json 복사 |
| 3 | 2단계 판 2주 동안 앱 오류·누락 신고 없음 | LEGACY_KINDS 를 빈 튜플로, `legacy_omitted`·`LEGACY_FIELDS` 코드와 시험 삭제 |
| 4 | 3단계 후 구버전 앱이 거의 없음 | 앱의 옛 키 읽는 길(tlPlPick 의 root.layers)도 삭제 |
앞 단계 조건을 못 채우면 다음 단계로 가지 않는다. 각 단계는 PR 하나.

## 4단 이상이 필요한 때
색인 장 수 × 종류 수가 뿌리 목표를 넘는 규모(층 파일 수백만 개)가 되면 색인의 색인(4단)이 필요하다. 지금 규모(7천 개)의 수백 배까지는 3단으로 충분하다(root_size.py 가 200배까지 확인).
