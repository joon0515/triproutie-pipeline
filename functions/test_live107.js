'use strict';
// 107기: flight.live 시험. 실제 키·네트워크 없음 — 가짜 키 'TESTKEY' + data.go.kr 응답 모양(명세 15140153/15160195/15158949) 모의.
const Module = require('module');
const FAKE_KEY = 'TESTKEY_NOT_REAL';

function mockGov(table) {
  const calls = [];
  const fetch = async (url) => {
    const u = new URL(url), q = Object.fromEntries(u.searchParams), path = u.pathname;
    calls.push({ path, q });
    const h = table(path, q);
    if (h === 'throw') throw new Error('net');
    const { status = 200, body } = h || { body: { response: { header: { resultCode: '00' }, body: { items: '' } } } };
    return { status, ok: status >= 200 && status < 300, text: async () => (typeof body === 'string' ? body : JSON.stringify(body)) };
  };
  return { fetch, calls };
}
const wrap = (items) => ({ response: { header: { resultCode: '00', resultMsg: 'NORMAL SERVICE.' }, body: { items: { item: items }, numOfRows: 20, pageNo: 1, totalCount: Array.isArray(items) ? items.length : 1 } } });
const LIMIT_XML = '<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg><returnAuthMsg>LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR</returnAuthMsg><returnReasonCode>22</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>';

async function run(ok, { fs, gz, makeFlightHandler, makeRoutesLoader, makeLiveEnricher, fakeDb }) {
  let pass0 = 0; const ok2 = (c, m) => { pass0++; ok(c, '[107] ' + m); };
  let t = Date.parse('2026-11-15T00:00:00Z'); // KST 2026-11-15 09:00
  const logs = [];
  let keyVal = FAKE_KEY;
  const icnRow = {
    KE081: { flightId: 'KE081', airline: '대한항공', airport: '뉴욕', airportCode: 'JFK', scheduleDatetime: '202611161000', estimatedDatetime: '202611161045', gateNumber: '250', terminalId: 'P03', chkinRange: 'A01-A12', remark: '지연', codeshare: 'Master', typeOfFlight: 'I' },
    LH712: { flightId: 'LH712', airline: '루프트한자', airport: '프랑크푸르트', airportCode: 'FRA', scheduleDatetime: '202611161630', estimatedDatetime: '202611161630', gateNumber: '', terminalId: 'P01', carousel: '12', exitNumber: 'B', remark: '결항', codeshare: 'Master' },
  };
  let mode = 'ok';
  const gov = mockGov((path, q) => {
    if (mode === 'throw') return 'throw';
    if (mode === 'limit') return { body: LIMIT_XML };
    if (mode === 'xml') return { body: '<response><header><resultCode>00</resultCode></header></response>' };
    if (path.endsWith('/getFltDeparturesDeOdp') || path.endsWith('/getFltArrivalsDeOdp')) {
      const r = icnRow[q.flightId]; if (!r) return { body: wrap([]) };
      return { body: wrap([{ ...r, scheduleDatetime: q.searchDate + r.scheduleDatetime.slice(8), estimatedDatetime: q.searchDate + r.estimatedDatetime.slice(8) }]) };
    }
    if (path.endsWith('/flight-search/info')) return { body: wrap([{ airFln: 'KE1201', airport: 'GMP', city: 'CJU', std: '0800', etd: '0830', gate: '7', io: 'O', line: '국내', rmkKor: '지연', rmkEng: 'DELAYED' }]) };
    if (path.endsWith('/flight-schedule/dom')) return { body: wrap([{ domesticNum: 'KE1201', startcityCode: 'GMP', arrivalcityCode: 'CJU', domesticStartTime: '0800', domesticArrivalTime: '0910', domesticMon: 'Y', domesticTue: 'Y', domesticWed: 'N', domesticThu: 'Y', domesticFri: 'Y', domesticSat: 'Y', domesticSun: 'Y', domesticStdate: '2026-10-26', domesticEddate: '2027-03-27' }]) };
    if (path.endsWith('/flight-schedule/int')) return { body: wrap([{ internationalNum: 'KE2131', airportCode: 'PUS', cityCode: 'NRT', internationalIoType: 'OUT', internationalTime: '1030', internationalTue: 'Y', internationalStdate: '20261025', internationalEddate: '20270327' }]) };
    return { status: 404, body: 'nf' };
  });
  const loadRoutes = makeRoutesLoader(async () => fs.readFileSync(gz));
  const h = makeFlightHandler({ verify: async () => ({ uid: 'L1' }), db: fakeDb(), fetch: gov.fetch, env: (n) => (n === 'DATA_GO_KR_KEY' ? FAKE_KEY : ''), log: (n, o) => logs.push(JSON.stringify([n, o])), now: () => t, loadRoutes, liveKey: () => keyVal });
  const call = (no, date) => h({ method: 'POST', body: { no, date } });

  // 1) 인천 출발 KE081, 내일(D+1)
  let r = await call('KE081', '2026-11-16'), f = r.body.flight, L = f && f.live;
  ok2(r.status === 200 && r.body.ok && f.from === 'ICN' && f.to === 'JFK', 'KE081 ok');
  ok2(gov.calls.length === 1 && gov.calls[0].path.endsWith('/getFltDeparturesDeOdp'), 'ICN dep endpoint ' + JSON.stringify(gov.calls.map((c) => c.path)));
  const q0 = gov.calls[0].q;
  ok2(q0.flightId === 'KE081' && q0.searchDate === '20261116' && q0.type === 'json' && q0.searchdtCode === 'S' && q0.serviceKey === FAKE_KEY && q0.pageNo === '1' && q0.numOfRows, 'ICN params ' + JSON.stringify({ ...q0, serviceKey: q0.serviceKey ? '(set)' : '' }));
  ok2(L && L.src === 'icn' && L.kind === 'dep' && L.gate === '250' && L.terminal === 'T2' && L.sched === '10:00' && L.est === '10:45' && L.delayMin === 45 && L.delayed === true && L.cancelled === false && L.checkin === 'A01-A12', 'ICN live ' + JSON.stringify(L));
  ok2(f.dep === null && f.arr === null && f.source === 'routes' && f.needConfirm === true && Array.isArray(f.toLL), 'existing fields unchanged');
  ok2(!JSON.stringify(r.body).includes(FAKE_KEY), 'key not in response');
  // 2) 캐시 10분
  r = await call('KE081', '2026-11-16'); ok2(gov.calls.length === 1 && r.body.flight.live && r.body.flight.live.gate === '250', 'cache hit no fetch');
  t += 11 * 60e3; r = await call('KE081', '2026-11-16'); ok2(gov.calls.length === 2, 'cache expired refetch');
  // 3) 인천 도착 LH712 결항
  r = await call('LH712', '2026-11-16'); L = r.body.flight.live;
  ok2(gov.calls[2].path.endsWith('/getFltArrivalsDeOdp') && L && L.kind === 'arr' && L.cancelled === true && L.delayed === false && L.terminal === 'T1' && L.carousel === '12' && L.exit === 'B' && !L.gate && !L.est, 'ICN arr cancelled ' + JSON.stringify(L));
  // 4) 범위 밖: D+10, D-4, 먼 미래 → 호출 없음
  const n4 = gov.calls.length;
  r = await call('KE081', '2026-11-25'); ok2(r.body.ok && !r.body.flight.live, 'D+10 no live');
  r = await call('KE081', '2026-11-11'); ok2(r.body.ok && !r.body.flight.live, 'D-4 no live');
  r = await call('KE081', '2026-12-01'); ok2(r.body.ok && !r.body.flight.live, 'D+16 no live');
  ok2(gov.calls.length === n4, 'out of window no fetch');
  r = await call('KE081', '2026-11-12'); ok2(gov.calls.length === n4 + 1 && r.body.flight.live && r.body.flight.live.sched === '10:00', 'D-3 inside window');
  r = await call('KE081', '2026-11-21'); ok2(gov.calls.length === n4 + 2 && r.body.flight.live, 'D+6 inside window');
  // 5) 네트워크 실패 → 조용히 노선표만
  mode = 'throw'; r = await call('LH712', '2026-11-17'); ok2(r.status === 200 && r.body.ok && r.body.flight.from === 'FRA' && !r.body.flight.live, 'net fail quiet');
  mode = 'xml'; r = await call('LH712', '2026-11-18'); ok2(r.status === 200 && r.body.ok && !r.body.flight.live, 'xml/shape fail quiet');
  // 6) 한도초과 → 그날 인천 끔, 다음 날 다시
  mode = 'limit'; r = await call('KE081', '2026-11-19'); ok2(r.body.ok && !r.body.flight.live, 'limit quiet');
  mode = 'ok'; let n6 = gov.calls.length; r = await call('KE081', '2026-11-20'); ok2(gov.calls.length === n6 && !r.body.flight.live, 'icn off for rest of KST day');
  t = Date.parse('2026-11-15T15:00:00Z') + 60e3; // KST 11-16 00:01
  r = await call('KE081', '2026-11-20'); ok2(gov.calls.length === n6 + 1 && r.body.flight.live, 'next KST day icn back');
  // 7) 키 없음 → 호출 0
  keyVal = ''; n6 = gov.calls.length; r = await call('LH712', '2026-11-19'); ok2(gov.calls.length === n6 && r.body.ok && !r.body.flight.live, 'no key no fetch');
  keyVal = FAKE_KEY;
  ok2(!logs.some((x) => x.includes(FAKE_KEY)), 'key never logged');

  // 8) 한국공항공사: 오늘 GMP→CJU 실시간, 다른 날 스케줄, 국제선 PUS→NRT
  const T0 = Date.parse('2026-11-15T00:00:00Z');
  let tt = T0;
  const calls2 = [];
  const fetch2 = async (url) => { const u = new URL(url); calls2.push({ path: u.pathname, q: Object.fromEntries(u.searchParams) }); return gov.fetch(url); };
  const e = makeLiveEnricher({ fetch: fetch2, key: () => FAKE_KEY, now: () => tt, log: () => {} });
  let v = await e({ no: 'KE1201', from: 'GMP', to: 'CJU' }, '2026-11-15');
  let c = calls2[0];
  ok2(c && c.path.endsWith('/flight-search/info') && c.q.schFln === 'KE1201' && c.q.schAirCode === 'GMP' && c.q.schIOType === 'O' && c.q.schLineType === 'D' && c.q.type === 'json', 'KAC rt params ' + JSON.stringify(c && { ...c.q, serviceKey: '-' }));
  ok2(v && v.src === 'kac_rt' && v.gate === '7' && v.sched === '08:00' && v.est === '08:30' && v.delayMin === 30 && v.delayed && v.status === '지연', 'KAC rt live ' + JSON.stringify(v));
  v = await e({ no: 'KE1201', from: 'GMP', to: 'CJU' }, '2026-11-17'); c = calls2[1]; // 화요일
  ok2(c.path.endsWith('/flight-schedule/dom') && c.q.schDate === '20261117' && c.q.schAirLine === 'KE' && c.q.schFlightNum === '1201' && c.q.schDeptCityCode === 'GMP' && c.q.schArrvCityCode === 'CJU', 'KAC dom params ' + JSON.stringify({ ...c.q, serviceKey: '-' }));
  ok2(v && v.src === 'kac_sched' && v.sched === '08:00' && v.schedArr === '09:10' && !v.gate, 'KAC dom live ' + JSON.stringify(v));
  v = await e({ no: 'KE1201', from: 'GMP', to: 'CJU' }, '2026-11-18'); ok2(v === null, 'KAC dom not operating Wed -> null');
  v = await e({ no: 'KE2131', from: 'PUS', to: 'NRT' }, '2026-11-17'); c = calls2[calls2.length - 1];
  ok2(c.path.endsWith('/flight-schedule/int') && c.q.schFlightNum === 'KE2131' && v && v.kind === 'dep' && v.airport === 'PUS' && v.sched === '10:30', 'KAC int live ' + JSON.stringify(v));
  // 9) 해외↔해외 → 호출 없음
  const n9 = calls2.length; v = await e({ no: 'LH400', from: 'FRA', to: 'JFK' }, '2026-11-16'); ok2(v === null && calls2.length === n9, 'foreign-only no fetch');
  // 10) 동시 요청 합침 + 분당 상한(8) + 인스턴스 하루 상한
  const e2 = makeLiveEnricher({ fetch: fetch2, key: () => FAKE_KEY, now: () => tt, log: () => {} });
  let n10 = calls2.length; await Promise.all([e2({ no: 'KE081', from: 'ICN', to: 'JFK' }, '2026-11-16'), e2({ no: 'KE081', from: 'ICN', to: 'JFK' }, '2026-11-16')]);
  ok2(calls2.length === n10 + 1, 'inflight dedupe');
  n10 = calls2.length; for (let i = 0; i < 12; i++) await e2({ no: 'KE' + (500 + i), from: 'ICN', to: 'NRT' }, '2026-11-16');
  ok2(calls2.length - n10 === 7, 'per-minute cap 8 total: ' + (calls2.length - n10)); // 1 already used this minute
  tt += 60e3; n10 = calls2.length; await e2({ no: 'KE600', from: 'ICN', to: 'NRT' }, '2026-11-16'); ok2(calls2.length === n10 + 1, 'next minute ok');
  const e3 = makeLiveEnricher({ fetch: fetch2, key: () => FAKE_KEY, now: () => tt, log: () => {} }, { perMinute: 1000, perDay: { icn: 5 } });
  n10 = calls2.length; for (let i = 0; i < 9; i++) await e3({ no: 'OZ' + (100 + i), from: 'ICN', to: 'NRT' }, '2026-11-16');
  ok2(calls2.length - n10 === 5, 'per-day icn cap: ' + (calls2.length - n10));
  // 11) 날짜 다른 행·코드쉐어
  const e4 = makeLiveEnricher({ fetch: async () => ({ status: 200, text: async () => JSON.stringify(wrap([{ flightId: 'KE5781', airportCode: 'NRT', scheduleDatetime: '202611170900', estimatedDatetime: '202611170900', remark: '', codeshare: 'Slave', masterFlightId: 'JL5204', terminalId: 'P03', gateNumber: '231' }])) }), key: () => FAKE_KEY, now: () => T0, log: () => {} });
  v = await e4({ no: 'KE5781', from: 'ICN', to: 'NRT' }, '2026-11-16'); ok2(v === null, 'other-date row rejected');
  v = await e4({ no: 'KE5781', from: 'ICN', to: 'NRT' }, '2026-11-17'); ok2(v && v.codeshareOf === 'JL5204' && v.gate === '231' && !v.est && !v.delayMin, 'codeshare ' + JSON.stringify(v));
  // 12) JSON 머리 resultCode 22 → 끔
  let n12 = 0; const e5 = makeLiveEnricher({ fetch: async () => { n12++; return { status: 200, text: async () => JSON.stringify({ response: { header: { resultCode: '22', resultMsg: 'LIMIT' } } }) }; }, key: () => FAKE_KEY, now: () => T0, log: () => {} });
  await e5({ no: 'KE1', from: 'ICN', to: 'NRT' }, '2026-11-16'); await e5({ no: 'KE2', from: 'ICN', to: 'NRT' }, '2026-11-16'); ok2(n12 === 1, 'json code 22 -> off');
  // 13) key() 가 throw 해도 조용히
  const e6 = makeLiveEnricher({ fetch: async () => { throw new Error('x'); }, key: () => { throw new Error('secret'); }, now: () => T0 });
  ok2((await e6({ no: 'KE1', from: 'ICN', to: 'NRT' }, '2026-11-16')) === null, 'key throw quiet');

  // 14) 배포 진입(makeFlightFunctions): firebase 모듈을 가짜로 바꿔 secret 바인딩·값 전달 확인
  const seen = {}; const origLoad = Module._load;
  Module._load = function (req, parent, isMain) {
    if (req === 'firebase-functions/v2/https') return { onRequest: (opts, fn) => { seen.opts = opts; seen.fn = fn; return fn; } };
    if (req === 'firebase-functions/params') return { defineBoolean: (n) => ({ name: n, value: () => false }), defineSecret: (n) => ({ name: n, value: () => FAKE_KEY }) };
    if (req === 'firebase-functions') return { logger: { info: (n, o) => logs.push(JSON.stringify([n, o])) } };
    if (req === 'firebase-admin/firestore') return { getFirestore: () => fakeDb() };
    if (req === 'firebase-admin/storage') return { getStorage: () => ({ bucket: () => ({ file: () => ({ download: async () => [fs.readFileSync(gz)] }) }) }) };
    if (req === './google') return { makeVerify: () => async () => ({ uid: 'D1' }) };
    return origLoad.apply(this, arguments);
  };
  const realFetch = globalThis.fetch; const n14 = gov.calls.length; globalThis.fetch = gov.fetch;
  const realNow = Date.now; Date.now = () => T0;
  try {
    delete require.cache[require.resolve('./flight_fn')];
    const fns = require('./flight_fn').makeFlightFunctions();
    ok2(seen.opts && Array.isArray(seen.opts.secrets) && seen.opts.secrets.some((x) => x.name === 'DATA_GO_KR_KEY') && seen.opts.region === 'asia-northeast3' && typeof fns.flightLookup === 'function', 'secret bound in opts');
    const res = { code: 0, body: null, status(c) { this.code = c; return this; }, json(b) { this.body = b; } };
    await seen.fn({ method: 'POST', headers: {}, body: { no: 'KE081', date: '2026-11-16' } }, res);
    ok2(res.code === 200 && res.body.ok && res.body.flight.live && res.body.flight.live.gate === '250' && gov.calls.length === n14 + 1, 'deployed entry uses secret ' + JSON.stringify(res.body).slice(0, 160));
  } finally { Module._load = origLoad; globalThis.fetch = realFetch; Date.now = realNow; }
  ok2(!logs.some((x) => x.includes(FAKE_KEY)), 'key never logged (final)');
  console.log('live107 checks: ' + pass0);
}
module.exports = { run };
