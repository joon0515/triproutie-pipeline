# 장소 파이프라인 설정(대표님용, 10줄)
1. (완료) Blaze 전환·기본 버킷 trip-mate-0vum9k.firebasestorage.app(US-EAST1) 생성. KMS 도 us-east1 에 만듭니다.
2. Google Cloud Shell 을 열고 `cloudshell_setup.sh` 내용을 그대로 붙여넣습니다(버킷 이름이 다르면 맨 앞에 `BUCKET=이름` 을 붙임). 여러 번 실행해도 됩니다.
3. 마지막에 나온 5줄(GCP_WIF_PROVIDER·GCP_SA_EMAIL·GCS_BUCKET·KMS_SIGN_KEY·KMS_RULES_KEY)을 GitHub 저장소 joon0515/triproutie-pipeline → Settings → Secrets and variables → Actions → **Variables** 에 그대로 넣습니다(비밀 아님).
4. 같은 화면 **Secrets** 에는 FSQ_TOKEN 하나만 넣습니다(Foursquare Places Portal 토큰).
5. 출력된 공개키 2개(PEM)를 개발팀에 전달합니다 → 개발팀이 앱 서명 확인용 공개키 변환·규칙 묶음 암호화(private_enc/)를 해서 저장소에 올립니다.
6. Firebase 콘솔 → Storage → 규칙에 `storage.rules` 를 붙여넣고 게시, App Check → Storage 를 '시행'으로 켭니다.
7. 저장소 Actions → places-world → Run workflow(regions=all, tag=날짜) 로 실행합니다. 지역 25개가 병렬로 돌고, 결과는 Storage places/v1/<tag>/ 에만 저장됩니다.
8. GitHub 에는 코드만 있고 데이터·키·장소 목록은 남지 않습니다(Release·artifact·cache 미사용, 로그에는 개수만).
9. 되돌리기: Storage 의 places/v1/manifest.json 을 이전 판으로 덮어쓰면 앱이 이전 판을 씁니다.
10. 비용(추정): Storage 수 GB·KMS 키 2개(월 수백 원)·Actions 는 공개 저장소라 무료.
