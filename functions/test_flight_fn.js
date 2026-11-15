'use strict';
// node test_flight_fn.js [routes_iata.json.gz 경로] — 외부 호출 0. 88기 시험(키 없음) + 107기 실시간 덧붙이기 + 116기 flight.airport(모의 응답, 가짜 키)
const fs = require('fs'), path = require('path');
const { makeFlightHandler, makeRoutesLoader, makeMinuteGate, makeLiveEnricher } = require('./flight_fn');
let pass = 0, fail = 0;
const ok = (c, m) => { if (c) pass++; else { fail++; console.log('FAIL', m); } };
const gz = process.argv[2] || path.join(__dirname, 'routes_iata.json.gz');

function fakeDb() {
  const m = new Map();
  const doc = (p) => ({ get: async () => ({ exists: m.has(p), data: () => m.get(p) }), set: async (v) => { m.set(p, v); } });
  return { doc, runTransaction: async (fn) => fn({ get: (r) => r.get(), set: (r, v) => r.set(v) }), _m: m };
}
let fetchCalls = 0;
const fakeFetch = async () => { fetchCalls++; throw new Error('no network in test'); };

(async () => {
  let downloads = 0;
  const loadRoutes = makeRoutesLoader(async () => { downloads++; return fs.readFileSync(gz); });
  let t = Date.parse('2026-11-15T00:00:00Z');
  const h = makeFlightHandler({
    verify: async (req) => { const a = req.headers && req.headers.authorization; if (!a) throw new Error('auth'); return { uid: a.slice(7) }; },
    db: fakeDb(), fetch: fakeFetch, env: () => '', log: () => {}, now: () => t, loadRoutes,
  });
  const call = (no, uid = 'u1', date = '2026-12-01') => h({ method: 'POST', headers: uid ? { authorization: 'Bearer ' + uid } : {}, body: { no, date } });

  let r = await call('KE081', null); ok(r.status === 401 && r.body.reason === 'auth', 'no auth 401');
  r = await h({ method: 'GET', headers: {}, body: {} }); ok(r.status === 405, 'GET 405');
  r = await call('KE081'); ok(r.status === 200 && r.body.ok && r.body.flight.from === 'ICN' && r.body.flight.to === 'JFK', 'KE081 ICN-JFK ' + JSON.stringify(r.body));
  ok(Array.isArray(r.body.flight.toLL) && Math.abs(r.body.flight.toLL[0]-40.6) < 0.2, 'JFK coords ' + JSON.stringify(r.body.flight.toLL));
  ok(r.body.flight && r.body.flight.dep === null && r.body.flight.source === 'routes' && r.body.flight.needConfirm === true, 'KE081 no time, needConfirm');
  r = await call('lh712'); ok(r.body.ok && r.body.flight.from === 'FRA' && r.body.flight.to === 'ICN', 'LH712 FRA-ICN');
  r = await call('KE81'); ok(r.body.ok && r.body.flight.to === 'JFK', 'KE81');
  r = await call('ZZ9999'); ok(r.status === 200 && r.body.ok === false && r.body.reason === 'not_found', 'unknown not_found ' + JSON.stringify(r.body));
  r = await call('!!'); ok(r.body.ok === false && r.body.reason === 'input', 'bad input');
  ok(downloads === 1, 'routes downloaded once: ' + downloads);
  ok(fetchCalls === 0, 'no external fetch without key: ' + fetchCalls);
  // 분당 상한: u2 로 20번 OK, 21번째 429, 다음 분 풀림
  let last; for (let i = 0; i < 21; i++) last = await call('LH712', 'u2');
  ok(last.status === 429 && last.body.reason === 'quota', 'minute limit 429');
  t += 60000; r = await call('LH712', 'u2'); ok(r.status === 200, 'next minute ok');
  // 노선표 읽기 실패 → 조용히 not_found 류(500 아님), 다음 요청에 재시도
  let n = 0; const bad = makeRoutesLoader(async () => { n++; if (n === 1) throw new Error('gcs down'); return fs.readFileSync(gz); });
  const h2 = makeFlightHandler({ verify: async () => ({ uid: 'x' }), db: fakeDb(), fetch: fakeFetch, env: () => '', log: () => {}, now: () => t, loadRoutes: bad });
  r = await h2({ method: 'POST', body: { no: 'KE081', date: '2026-12-01' } }); ok(r.status === 200 && r.body.ok === false, 'gcs fail quiet ' + JSON.stringify(r.body));
  r = await h2({ method: 'POST', body: { no: 'KE081', date: '2026-12-01' } }); ok(r.body.ok && r.body.flight.to === 'JFK', 'retry after gcs fail');
  const g = makeMinuteGate(() => 0, 2); ok(g('a') && g('a') && !g('a') && g('b'), 'gate unit');
  ok(r.body.flight.live === undefined, 'no key -> no live field');
  await require('./test_live107').run(ok, { fs, gz, makeFlightHandler, makeRoutesLoader, makeLiveEnricher, fakeDb });
  await require('./test_airport116').run(ok, { fs, gz, makeFlightHandler, makeRoutesLoader, fakeDb });
  console.log(`flight_fn: ${pass}/${fail}`);
  process.exit(fail ? 1 : 0);
})();
