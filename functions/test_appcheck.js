'use strict';
// node test_appcheck.js — 276기 App Check 단계 시험(외부 호출 0, 가짜 verifyToken).
const { checkAppCheck } = require('./appcheck');
let pass = 0, fail = 0;
const ok = (c, m) => { if (c) pass++; else { fail++; console.log('FAIL', m); } };
const req = (h) => ({ path: '/x', get: (k) => h[k.toLowerCase()] || '' });
const ac = (mode) => () => ({ verifyToken: async (t, o) => { if (t !== 'good') throw new Error('bad'); return { appId: 'app1', alreadyConsumed: mode === 'used' && !!(o && o.consume) }; } });
(async () => {
  const logs = []; const log = (n, o) => logs.push([n, o]);
  let r = await checkAppCheck(req({}), false, { log, appCheck: ac() });
  ok(r.ok === false && logs[0][0] === 'appcheck_soft' && logs[0][1].state === 'missing', '기록 모드: 없으면 통과·기록');
  r = await checkAppCheck(req({ 'x-firebase-appcheck': 'bad' }), false, { log, appCheck: ac() });
  ok(r.ok === false && logs[1][1].state === 'invalid', '기록 모드: 틀리면 통과·기록');
  ok(!JSON.stringify(logs).includes('bad'), '토큰 값은 로그에 없음');
  r = await checkAppCheck(req({ 'x-firebase-appcheck': 'good' }), true, { log, appCheck: ac() });
  ok(r.ok && r.app === 'app1', '강제 모드: 맞으면 통과');
  const rej = async (h, o) => { try { await checkAppCheck(req(h), true, { log, appCheck: ac(o && o.mode), consume: o && o.consume }); return false; } catch (e) { return e.reason === 'appcheck'; } };
  ok(await rej({}), '강제 모드: 없으면 거부');
  ok(await rej({ 'x-firebase-appcheck': 'bad' }), '강제 모드: 틀리면 거부');
  ok(await rej({ 'x-firebase-appcheck': 'good' }, { consume: true, mode: 'used' }), '재사용 방지: 이미 쓴 토큰 거부');
  console.log(`appcheck: ${pass}/${fail}`);
  process.exit(fail ? 1 : 0);
})();
