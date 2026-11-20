'use strict';
// codebase "proxy" 의 일부(flightLookup·geoLookup)만 담은 진입점 — GitHub Actions(.github/workflows/deploy_functions.yml)가 이름 지정으로만 배포.
// proxy 의 나머지(Cloud Shell ~/tr: placesSearch·g·roadRoute 등)는 여기 없다. 이 폴더로 이름 없는 proxy 전체 배포를 하면 그 함수들을 지우려 하므로 금지.
const { initializeApp } = require('firebase-admin/app');
initializeApp();
Object.assign(exports, require('./flight_fn').makeFlightFunctions());
Object.assign(exports, require('./geo_fn').makeGeoFunctions());
Object.assign(exports, require('./acct_fn').makeAcctFunctions()); // 169기 계정 삭제
Object.assign(exports, require('./live_fn').makeLiveFunctions()); // 252기 장소 상세 실시간(liveBudget·placeDetailsLive) — 기존 placeDetails·g 는 Cloud Shell 원본 그대로
Object.assign(exports, require('./transit_fn').makeTransitFunctions()); // 257기 대중교통 실시간(transitLiveMta·Tfl·Lta·Oba·Idfm) — 대표가 Cloud Shell 에서 이름 지정 배포, 키 Secret 이 있는 것만
Object.assign(exports, require('./notice_fn').makeNoticeFunctions()); // 264기 공식 운행 공지(transitNotices, ODPT 회원 키) — 대표가 이름 지정 배포
Object.assign(exports, require('./link_fn').makeLinkFunctions()); // 264기 공유 글 → 장소 후보(linkPlaces264, Gemini 무료 키) — 원 페이지 안 가져옴, 대표가 이름 지정 배포
Object.assign(exports, require('./vote_fn').makeVoteFunctions()); // 262기 가입·설치 없는 링크 투표(votePage·voteCast) — 대표가 이름 지정 배포
Object.assign(exports, require('./mkt_fn').makeMktFunctions()); // 292기 광고 푸시(mktDispatch·mktConsentNote·mktReconfirm) — 대표가 이름 지정 배포, 캠페인은 콘솔 mkt_campaigns 에 status:'ready'
for (const k of ['flightLookup', 'geoLookup', 'liveBudget', 'placeDetailsLive', 'transitLiveMta', 'transitLiveTfl', 'transitLiveLta', 'transitLiveOba', 'transitLiveIdfm', 'transitNotices', 'linkPlaces264', 'votePage', 'voteCast']) if (!exports[k]) throw new Error('index: ' + k + ' 없음');
