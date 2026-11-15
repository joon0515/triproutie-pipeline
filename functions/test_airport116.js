'use strict';
// 116기: flight.airport 시험. 실제 키·네트워크 없음 — 가짜 키 + data.go.kr 응답 모양(15148225/15161098/15095061/15128793/15158950 swagger) 모의.
const { makeAirportLive, GOV_AP } = require('./airport_live');
const FAKE_KEY = 'TESTKEY_NOT_REAL';
const wrap = (items) => ({ response: { header: { resultCode: '00', resultMsg: 'NORMAL SERVICE.' }, body: { items: { item: items }, numOfRows: 50, pageNo: 1, totalCount: items.length } } });
const LIMIT_XML = '<OpenAPI_ServiceResponse><cmmMsgHeader><returnAuthMsg>LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR</returnAuthMsg><returnReasonCode>22</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>';
const P = (k) => new URL(GOV_AP[k]).pathname;
const DATA = {
  [P('hallT1')]: [
    { terminalId: 'P01', gateId: 'DG1_E', waitTime: '12', waitLength: '80', occurtime: '20261115085900', operatingTime: '0600-2100' },
    { terminalId: 'P01', gateId: 'DG1_W', waitTime: '9', waitLength: '40', occurtime: '20261115085900', operatingTime: '0600-2100' },
    { terminalId: 'P01', gateId: 'DG2_E', waitTime: '3', waitLength: '15', occurtime: '20261115085900', operatingTime: '0600-2100' },
    { terminalId: 'P01', gateId: 'XX', waitTime: '1', waitLength: '1' }],
  [P('hallT2')]: [{ terminalId: 'P03', gateId: 'DG1_A', waitTime: '7', waitLength: '30', occurtime: '20261115085900' }, { terminalId: 'P03', gateId: 'DG2_B', waitTime: '20', waitLength: '150', occurtime: '20261115085900' }],
  [P('arrHall')]: [{ airport: 'KIX', entrygate: 'c', flightid: 'KE724', gatenumber: '107', korean: '30', foreigner: '12', terno: 'T1' }, { airport: 'HAN', entrygate: 'A', flightid: 'VN416', korean: '1', foreigner: '0', terno: 'T1' }],
  [P('lateral')]: [{ flightId: 'KE723', chkinRange: 'A01-C18', gateNumber: '15', scheduleDatetime: '202611151230', lateral1: 'L03', lateral1Status: 'O', terminalId: 'P03' }],
  [P('kacV1')]: [{ IATA_APCD: 'GMP', PRC_HR: '09', OPR_STS_CD: '1', STY_TCT_AVG_A: '300', STY_TCT_AVG_B: '120', STY_TCT_AVG_C: '540', STY_TCT_AVG_D: '600', STY_TCT_AVG_ALL: '1560' }, { IATA_APCD: 'CJU', OPR_STS_CD: '0', STY_TCT_AVG_A: '1' }],
  [P('kacV2')]: [{ IATA_APCD: 'PUS', OPR_STS_CD: '1', STY_TCT_AVG_A: '240', STY_TCT_AVG_B: '', STY_TCT_AVG_C: '400', STY_TCT_AVG_D: '300', STY_TCT_AVG_ALL: '940' }],
};
function mock(over = {}) {
  const calls = [];
  const fetch = async (url) => {
    const u = new URL(url), q = Object.fromEntries(u.searchParams); calls.push({ path: u.pathname, q });
    const o = over[u.pathname]; if (o === 'throw') throw new Error('net');
    if (o) return { status: o.status || 200, text: async () => o.body };
    const rows = DATA[u.pathname]; return { status: 200, text: async () => JSON.stringify(wrap(rows || [])) };
  };
  return { fetch, calls };
}

async function run(ok, ctx) {
  let n = 0; const t = (c, m) => { n++; ok(c, '[116] ' + m); };
  let T = Date.parse('2026-11-15T00:00:00Z'); const today = '2026-11-15'; const logs = [];
  const mk = (m, key = FAKE_KEY, opt) => makeAirportLive({ fetch: m.fetch, key: () => key, now: () => T, log: (a, b) => logs.push(JSON.stringify([a, b])) }, opt);

  // 1 인천 T1 출발: 출국장 묶기(동·서 합산), 적재대로 카운터 보충, 107 live 게이트는 유지
  let m = mock(), A = mk(m);
  let r = await A({ no: 'KE723', from: 'ICN', to: 'KIX', date: today }, { src: 'icn', kind: 'dep', terminal: 'T1', gate: '250' });
  t(r && r.depCongestion.terminal === 'T1' && r.depCongestion.halls.length === 2, 'T1 halls grouped ' + JSON.stringify(r));
  t(r && r.depCongestion.halls[0].id === '1' && r.depCongestion.halls[0].wait === 120 && r.depCongestion.halls[0].waitMin === 12, 'hall1 sum people, max minutes');
  t(r && r.checkin === 'A01-C18' && r.gate === undefined, 'lateral fills checkin only (live gate kept)');
  t(r && typeof r.at === 'number' && /인천/.test(r.credit), 'at + credit');
  const hq = m.calls.find((c) => c.path === P('hallT1'));
  t(hq && hq.q.terminalId === 'P01' && hq.q.type === 'json' && hq.q.pageNo === '1' && hq.q.serviceKey === FAKE_KEY, 'T1 params');
  const lq = m.calls.find((c) => c.path === P('lateral'));
  t(lq && lq.q.flightId === 'KE723' && lq.q.searchDay === '0' && !('d' in lq.q), 'lateral params');
  // 2 캐시: 5분 안 다시 안 부름, 6분 뒤 혼잡도만 다시(적재대는 1일)
  let c0 = m.calls.length; await A({ no: 'KE723', from: 'ICN', to: 'KIX', date: today }, { src: 'icn', kind: 'dep', terminal: 'T1' });
  t(m.calls.length === c0, 'cache 5m');
  T += 6 * 60e3; c0 = m.calls.length; await A({ no: 'KE723', from: 'ICN', to: 'KIX', date: today }, { src: 'icn', kind: 'dep', terminal: 'T1' });
  t(m.calls.length === c0 + 1 && m.calls[c0].path === P('hallT1'), 'congestion refetch after 6m, lateral cached 1d');
  // 3 107 live 에 카운터·게이트가 있으면 적재대 안 부름
  m = mock(); A = mk(m);
  await A({ no: 'KE723', from: 'ICN', to: 'KIX', date: today }, { src: 'icn', kind: 'dep', terminal: 'T1', checkin: 'D', gate: '12' });
  t(!m.calls.some((c) => c.path === P('lateral')), 'no lateral when live has checkin+gate');
  // 4 T2
  r = await A({ no: 'KE001', from: 'ICN', to: 'NRT', date: today }, { src: 'icn', kind: 'dep', terminal: 'T2', checkin: 'D', gate: '12' });
  const q2 = m.calls.find((c) => c.path === P('hallT2'));
  t(r && r.depCongestion.terminal === 'T2' && r.depCongestion.halls[1].wait === 150 && q2 && !('terminalId' in q2.q), 'T2 path');
  // 5 인천 도착: 편명 맞춤(대소문자·0), 대기 인원 합, 수취대 옮김
  r = await A({ no: 'KE0724', from: 'KIX', to: 'ICN', date: today }, { src: 'icn', kind: 'arr', terminal: 'T1', carousel: '14' });
  t(r && r.arrHall.gate === 'C' && r.arrHall.wait === 42 && r.arrHall.korean === 30 && r.carousel === '14', 'arrHall ' + JSON.stringify(r));
  const aq = m.calls.find((c) => c.path === P('arrHall'));
  t(aq && aq.q.terno === 'T1', 'arr params');
  r = await A({ no: 'OZ999', from: 'KIX', to: 'ICN', date: today }, { src: 'icn', kind: 'arr', terminal: 'T1' });
  t(r === null, 'arr no match, no carousel → null');
  // 6 한국공항공사 소요시간: GMP v1, PUS v2(빈 구간 빼기), CJU 비운영 → null
  r = await A({ no: 'KE1101', from: 'GMP', to: 'CJU', date: today }, { src: 'kac_rt', kind: 'dep' });
  t(r && r.steps.length === 4 && r.steps[0].key === 'checkin' && r.steps[0].min === 5 && r.steps[2].min === 9 && r.stepsTotalMin === 26 && /한국공항공사/.test(r.credit), 'GMP steps');
  r = await A({ no: 'BX101', from: 'PUS', to: 'GMP', date: today }, null);
  t(r && r.steps.length === 3 && r.steps.map((x) => x.seg).join() === '1,3,4' && m.calls.some((c) => c.path === P('kacV2')), 'PUS v2');
  t((await A({ no: 'KE1201', from: 'CJU', to: 'GMP', date: today }, null)) === null, 'CJU closed → null');
  t((await A({ no: 'LJ101', from: 'CJU', to: 'GMP', date: today }, null)) === null && m.calls.filter((c) => c.path === P('kacV1')).length === 1, 'kac v1 shared cache');
  // 7 오늘 편 아님 / 키 없음 / 키 throw → 부르지 않음
  m = mock(); A = mk(m);
  t((await A({ no: 'KE723', from: 'ICN', to: 'KIX', date: '2026-11-16' }, null)) === null && m.calls.length === 0, 'not today skip');
  A = mk(m, '');
  t((await A({ no: 'KE723', from: 'ICN', to: 'KIX', date: today }, null)) === null && m.calls.length === 0, 'no key skip');
  A = makeAirportLive({ fetch: m.fetch, key: () => { throw new Error('s'); }, now: () => T });
  t((await A({ no: 'KE723', from: 'ICN', to: 'KIX', date: today }, null)) === null && m.calls.length === 0, 'key throw quiet');
  t((await A(null, null)) === null, 'null flight');
  // 8 실패: 네트워크, XML(모양 다름), 오류 코드, 한도 → 조용히 생략 + 한도는 자정까지 끔
  m = mock({ [P('hallT1')]: 'throw', [P('lateral')]: { body: '<xml>oops</xml>' } }); A = mk(m);
  t((await A({ no: 'KE723', from: 'ICN', to: 'KIX', date: today }, null)) === null, 'net + xml fail quiet');
  m = mock({ [P('arrHall')]: { body: JSON.stringify({ response: { header: { resultCode: '30', resultMsg: 'x' } } }) } }); A = mk(m);
  t((await A({ no: 'KE724', from: 'KIX', to: 'ICN', date: today }, null)) === null, 'error code quiet');
  m = mock({ [P('hallT1')]: { body: LIMIT_XML } }); A = mk(m);
  await A({ no: 'KE723', from: 'ICN', to: 'KIX', date: today }, { checkin: 'A', gate: '1' });
  T += 10 * 60e3; c0 = m.calls.length;
  t((await A({ no: 'KE723', from: 'ICN', to: 'KIX', date: today }, { checkin: 'A', gate: '1' })) === null && m.calls.length === c0, 'limit → off until midnight');
  T += 864e5; c0 = m.calls.length;
  await A({ no: 'KE723', from: 'ICN', to: 'KIX', date: '2026-11-16' }, { checkin: 'A', gate: '1' });
  t(m.calls.length === c0 + 1, 'limit resets next KST day');
  T -= 864e5 + 16 * 60e3;
  // 9 하루 한도(인스턴스당)
  m = mock(); A = mk(m, FAKE_KEY, { perDay: { lateral: 2 } });
  for (const no of ['KE1', 'KE2', 'KE3']) await A({ no, from: 'ICN', to: 'KIX', date: today }, { gate: '1' });
  t(m.calls.filter((c) => c.path === P('lateral')).length === 2, 'per-day cap');
  // 10 키는 주소에만, 로그에 없음
  t(!logs.some((x) => x.includes(FAKE_KEY)), 'key never logged');

  // 11 배포 진입 핸들러: flight.airport 붙고, 실패해도 결과는 그대로
  if (ctx && ctx.makeFlightHandler) {
    const { fs, gz, makeFlightHandler, makeRoutesLoader, fakeDb } = ctx;
    const mk2 = (mm) => makeFlightHandler({ verify: async () => ({ uid: 'A1' }), db: fakeDb(), fetch: mm.fetch, env: () => '', log: (a, b) => logs.push(JSON.stringify([a, b])), now: () => T,
      loadRoutes: makeRoutesLoader(async () => fs.readFileSync(gz)), liveKey: () => FAKE_KEY });
    let mm = mock(); let res = await mk2(mm)({ method: 'POST', headers: { authorization: 'Bearer A1' }, body: { no: 'KE081', date: today } });
    const fl = res.body && res.body.flight;
    t(res.status === 200 && fl && fl.from === 'ICN' && fl.airport && fl.airport.depCongestion && fl.airport.depCongestion.halls.length === 2, 'handler adds flight.airport ' + JSON.stringify(fl && fl.airport).slice(0, 120));
    mm = { fetch: async () => { throw new Error('down'); } };
    res = await mk2(mm)({ method: 'POST', headers: { authorization: 'Bearer A1' }, body: { no: 'KE081', date: today } });
    t(res.status === 200 && res.body.ok && res.body.flight.to === 'JFK' && !res.body.flight.airport, 'handler: all down → no airport, ok');
    res = await mk2(mock())({ method: 'POST', headers: { authorization: 'Bearer A1' }, body: { no: 'KE081', date: '2026-12-01' } });
    t(res.status === 200 && res.body.ok && !res.body.flight.airport, 'handler: future date → no airport');
    t(!logs.some((x) => x.includes(FAKE_KEY)), 'key never logged (handler)');
  }
  console.log('airport116 checks: ' + n);
}
module.exports = { run };
if (require.main === module) { let p = 0, f = 0; run((c, m) => { if (c) p++; else { f++; console.log('FAIL', m); } }).then(() => console.log(`airport116: ${p}/${f}`)); }
