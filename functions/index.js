'use strict';
// codebase "tools" 진입점 — GitHub Actions(.github/workflows/deploy_functions.yml)가 배포. 이 codebase 의 함수만 담는다.
// 기존 codebase "proxy"(Cloud Shell ~/tr: placesSearch·g·roadRoute 등)는 여기 없고, 이 배포는 그 함수를 건드리지 않는다.
const { initializeApp } = require('firebase-admin/app');
initializeApp();
Object.assign(exports, require('./flight_fn').makeFlightFunctions());
Object.assign(exports, require('./geo_fn').makeGeoFunctions());
for (const k of ['flightLookup', 'geoLookup']) if (!exports[k]) throw new Error('index: ' + k + ' 없음');
