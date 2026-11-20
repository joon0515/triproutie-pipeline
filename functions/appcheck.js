'use strict';
// 276기: App Check 검사 한 곳. google.js(makeVerify)·acct_fn.js(makeVerifyAcct)가 함께 쓴다.
//
// 단계(SECURITY_276.md 2절):
//   1) 지금: REQUIRE_APP_CHECK=false → 막지 않고 '토큰 없음/틀림' 횟수만 로그(appcheck_soft). 값·토큰은 로그하지 않는다.
//   2) 앱 새 빌드(App Check 켬)가 대부분 퍼지면 로그의 missing 비율을 보고 REQUIRE_APP_CHECK=true 로 바꿔 배포.
//   3) true 이면 토큰 없거나 틀린 요청은 reason 'appcheck' 로 거부(401).
// consume=true 는 재사용 방지 토큰(한 번 쓰면 끝)을 요구한다 — 계정 삭제처럼 되풀이되면 안 되는 호출용(앱이 limited-use 토큰을 보내야 함).

function defaultLog() {
  try { const { logger } = require('firebase-functions'); return (n, o) => logger.info(n, o); } catch (_) { return () => {}; }
}

/**
 * @param req        express 요청(get(name) 으로 머리글을 읽음)
 * @param required   true 면 강제, false 면 기록만
 * @param opts       { consume?: boolean, log?: fn, appCheck?: () => ({verifyToken}) }
 * @returns          { ok: true, app: appId|'' } | throws {reason:'appcheck'}
 */
async function checkAppCheck(req, required, opts = {}) {
  const log = opts.log || defaultLog();
  const ac = (req.get && req.get('x-firebase-appcheck')) || '';
  const fail = (state) => {
    if (required) throw Object.assign(new Error('appcheck'), { reason: 'appcheck' });
    log('appcheck_soft', { state, path: String(req.path || '').slice(0, 40) });
    return { ok: false, app: '' };
  };
  if (!ac) return fail('missing');
  try {
    const getAc = opts.appCheck || (() => require('firebase-admin/app-check').getAppCheck());
    const r = await getAc().verifyToken(ac, opts.consume ? { consume: true } : undefined);
    if (opts.consume && r && r.alreadyConsumed) return fail('replayed');
    return { ok: true, app: (r && r.appId) || '' };
  } catch (_) {
    return fail('invalid');
  }
}

module.exports = { checkAppCheck };
