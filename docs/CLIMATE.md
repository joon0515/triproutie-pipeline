# 기후 평년값(climate-normals)

도시별 월별 평년값 표(평균 최고·최저기온, 월 강수량)를 만든다. 앱의 여행지 카드 '월별 기온·강수 막대'에 쓴다.

## 자료와 라이선스 (2026-11-17 확인)
| 무엇 | 출처 | 조건 | 표기 |
|---|---|---|---|
| 일별 기온·강수 1991–2020 | NASA POWER Daily API (MERRA-2 격자, 0.5°×0.625°), 키·가입 없음 | "There are no restrictions on the use, access, and/or download of data from the NASA POWER Project" — NASA 가 관리하는 AWS 공개 자료 목록의 License 칸(같은 칸에 CC BY 4.0). NASA 자료 이용 정책은 표시가 없으면 CC0. NASA 가 제품을 보증하는 듯한 표현 금지 | "NASA POWER (MERRA-2), 1991–2020 평균, 트립루티 계산" + ATTRIBUTION.txt 의 감사 문구 |
| 도시 이름·좌표 | GeoNames cities15000 | CC BY 4.0 | "GeoNames" |

원문: https://registry.opendata.aws/nasa-power/ · https://www.earthdata.nasa.gov/earth-observation-data/data-use-policy · https://power.larc.nasa.gov/docs/referencing/ · https://download.geonames.org/export/dump/readme.txt

## 값의 뜻 (앱 문구가 이 뜻을 벗어나면 안 된다)
- tx·tn: 일 최고·최저기온의 30년 월 평균. pr: 월 강수량의 30년 평균(비·눈 합).
- **격자 평균값이다. 관측소 평년값이 아니다.** 산·바닷가 도시는 관측소 값과 차이가 난다(격자 평균 고도가 도시와 다르다).
- 앱에 이미 있는 관측소 평년값 표(11개 지역·120개 달)와 견준 결과(2026-11-17): 월 평균 최고기온 차 평균 2.1°C·최저 2.3°C, 가장 큰 차 6.5°C(센다이 — 격자에 산이 들어감). 도쿄 −0.5°C, 삿포로 −3.2°C, 오사카 −2.8°C. 연 강수량 −25%~+28%.
  그래서 앱은 관측소 표가 있는 지역은 그 표를 그리고, 없는 도시에만 이 격자값을 '격자 평균값'이라고 밝혀 쓴다.
- wd(1mm 이상 내린 날 수)는 격자 평균이라 관측소 표보다 25~103% 많이 나온다. JSON 에는 넣지만 dart 파일에는 기본으로 넣지 않는다(`--wet` 로만).

## 쓰는 법
```
python3 -m unittest tests/test_climate_normals.py           # 모의 시험(네트워크 없음)
python3 scripts/climate_normals.py --cities sources/climate_cities.csv --out climate/out --dart
```
- 도시 하나에 요청 1번(약 0.5MB, 디스크에 남기지 않음), 1초 쉼. 약 720곳 ≈ 25분. 끊기면 다시 돌리면 이어받는다(`climate/out/cache`).
- 출력: `climate_normals.json`(한도 400KB) · `tl_p7_data.dart`(앱에 붙이는 파일) · `ATTRIBUTION.txt`.
- 도시 목록을 바꾸려면: `python3 scripts/climate_cities.py cities15000.txt > sources/climate_cities.csv` (기준은 그 파일 머리말).
- Actions: `climate-normals`(손으로 실행, 비밀값 없음) → 실행 산출물로 받는다.
