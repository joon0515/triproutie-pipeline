// 약관·상업 사용 허락을 못 찾은 항공 조회 2곳(adsbdb·hexdb)은 환경 변수를 켜도 호출하지 않는다(2026-11-17 출처 점검 31·32번).
// 실행: node test_noterms.js
const { makeFreeFlight, NOTERMS_OK } = require('./flight_free');
let pass = 0, fail = 0; const ok = (c, m) => { if (c) pass++; else { fail++; console.log('FAIL', m); } };
function fakeDb() {
  const store = new Map();
  const doc = (p) => ({ path: p, get: async () => ({ exists: store.has(p), data: () => store.get(p) }), set: async (v, o) => { store.set(p, o && o.merge ? { ...(store.get(p) || {}), ...v } : v); } });
  return { doc, runTransaction: async (fn) => fn({ get: (r) => r.get(), set: (r, v, o) => r.set(v, o) }) };
}
(async () => {
  const urls = [];
  const on = { FLIGHT_ADSBDB_OK: '1', FLIGHT_HEXDB_OK: '1' };
  const f = makeFreeFlight({
    now: () => Date.UTC(2026, 10, 17, 3, 0, 0), db: fakeDb(), env: (n) => on[n] || '', log: () => {}, verify: async () => ({ uid: 'u1' }),
    fetch: async (u) => { urls.push(String(u)); return { ok: true, status: 200, json: async () => ({ response: { flightroute: { origin: { iata_code: 'ICN', latitude: 37.46, longitude: 126.44 }, destination: { iata_code: 'NRT', latitude: 35.76, longitude: 140.38 } } }, route: 'RKSI-RJAA' }), text: async () => '{}' }; },
  });
  let r = null, err = null;
  try { r = await f.lookup('KE703', '2026-12-01', { uid: 'u1' }); } catch (e) { err = e; }
  ok(!err, 'lookup 이 끝까지 돈다: ' + (err && err.message));
  ok(NOTERMS_OK === false, 'NOTERMS_OK 는 false');
  ok(!urls.some((u) => /adsbdb\.com|hexdb\.io/.test(u)), '스위치를 켜도 adsbdb·hexdb 로 나가지 않는다: ' + urls.join(' '));
  ok(!(r && r.flight && /adsbdb|hexdb/.test(String(r.flight.source))), '결과 출처에 adsbdb·hexdb 없음');
  const src = require('fs').readFileSync(require('path').join(__dirname, 'flight_free.js'), 'utf8');
  ok((src.match(/if \(!NOTERMS_OK \|\| env\('FLIGHT_(ADSBDB|HEXDB)_OK'\) !== '1'\) return null;/g) || []).length === 2, '두 함수 모두 첫 줄에서 막는다');
  console.log(`noterms: ${pass} ok, ${fail} fail (fetch ${urls.length})`); process.exit(fail ? 1 : 0);
})();
