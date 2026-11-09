# triproutie-pipeline (공개 코드 — 데이터·규칙 없음)
- `.github/workflows/places-world.yml` — 수동 실행만. 지역 매트릭스 → 스캔(Overture 공개 S3 + Foursquare OS Places Iceberg) → H3 타일 → 빌드 → Firebase Storage 비공개 업로드 → Cloud KMS 서명 manifest.
- `tools/world_region.py`·`world_regions.json`·`canonical_fix.py` — 공개 가능한 스캔·타일·정규 JSON 코드.
- `private_enc/` — 분류·필터·랭킹·큐레이션·음역 사전 묶음의 **암호문**(AES-256-GCM, 데이터 키는 KMS RSA 로 감쌈; 서비스 계정만 풀 수 있음).
- 인증: Workload Identity Federation(키 파일 없음). 비밀: FSQ_TOKEN 하나. 설정: `SETUP_KO.md`, `cloudshell_setup.sh`.
- 데이터 출처 표시: Overture Maps Foundation(CDLA-Permissive-2.0), Foursquare OS Places(Apache-2.0, NOTICE), AllThePlaces(CC0).
