'use strict';
// codebase "proxy" 의 일부(flightLookup·geoLookup)만 담은 진입점 — GitHub Actions(.github/workflows/deploy_functions.yml)가 이름 지정으로만 배포.
// proxy 의 나머지(Cloud Shell ~/tr: placesSearch·g·roadRoute 등)는 여기 없다. 이 폴더로 이름 없는 proxy 전체 배포를 하면 그 함수들을 지우려 하므로 금지.
const { initializeApp } = require('firebase-admin/app');
initializeApp();
Object.assign(exports, require('./flight_fn').makeFlightFunctions());
Object.assign(exports, require('./geo_fn').makeGeoFunctions());
Object.assign(exports, require('./acct_fn').makeAcctFunctions()); // 169기 계정 삭제
for (const k of ['flightLookup', 'geoLookup']) if (!exports[k]) throw new Error('index: ' + k + ' 없음');
