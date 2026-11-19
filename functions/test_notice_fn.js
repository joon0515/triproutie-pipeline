'use strict';
// node test_notice_fn.js — 외부 호출 0(가짜 fetch·인증). 264기 transitNotices.
const N = require('./notice_fn');
let pass = 0, fail = 0;
const ok = (c, m) => { if (c) pass++; else { fail++; console.log('FAIL', m); } };
(async () => {
  let t = 1000, calls = 0, lastUrl = '';
  const body = [{ 'odpt:operator': 'odpt.Operator:TokyoMetro', 'odpt:railway': 'odpt.Railway:TokyoMetro.Ginza', 'odpt:trainInformationStatus': { ja: '遅延' }, 'odpt:trainInformationText': { ja: '遅れています', en: 'Delayed' }, 'dc:date': '2026-11-20T08:00:00+09:00', 'secret': 'x', '@id': 'u' }];
  const mk = (resp, verifyOk = true) => N.makeNoticeCore({
    verify: async () => { if (!verifyOk) throw Object.assign(new Error('a'), { reason: 'auth' }); },
    fetch: async (u) => { calls++; lastUrl = u; return resp(); },
    now: () => t, key: () => 'KEY123', log: () => {},
  });
  const good = () => ({ ok: true, status: 200, json: async () => body });
  const c = mk(good);
  const r = await c.handle({ method: 'GET', query: { op: 'odpt.Operator:TokyoMetro' } });
  ok(r.status === 200 && Array.isArray(r.body) && r.body.length === 1, '200 배열');
  ok(r.body[0]['odpt:railway'] === 'odpt.Railway:TokyoMetro.Ginza', '노선 칸');
  ok(!('secret' in r.body[0]) && !('@id' in r.body[0]), '고른 칸만');
  ok(!JSON.stringify(r.body).includes('KEY123'), '키가 응답에 없음');
  ok(lastUrl.includes('acl:consumerKey=KEY123') && lastUrl.startsWith('https://api.odpt.org/'), 'ODPT 회원 주소');
  await c.handle({ method: 'GET', query: { op: 'odpt.Operator:TokyoMetro' } });
  ok(calls === 1, '60초 캐시');
  t += 61e3;
  await c.handle({ method: 'GET', query: { op: 'odpt.Operator:TokyoMetro' } });
  ok(calls === 2, '60초 뒤 다시');
  ok((await c.handle({ method: 'GET', query: { op: 'odpt.Operator:Evil' } })).status === 400, '목록 밖 운영사 거절');
  ok((await c.handle({ method: 'POST', query: {} })).status === 405, 'GET 만');
  ok((await mk(good, false).handle({ method: 'GET', query: { op: 'odpt.Operator:JR-East' } })).status === 401, '로그인 필요');
  const bad = await mk(() => ({ ok: false, status: 403, json: async () => ({}) })).handle({ method: 'GET', query: { op: 'odpt.Operator:JR-East' } });
  ok(bad.status === 200 && Array.isArray(bad.body) && bad.body.length === 0, '위쪽 오류 → 빈 배열');
  ok(!N.OPS.includes('odpt.Operator:Toei'), '도영은 공개 API(앱 직접)');
  console.log(`notice_fn: ${pass}/${fail}`);
  process.exit(fail ? 1 : 0);
})();
