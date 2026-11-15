'use strict';
// makeVerify 만 담은 얇은 판. Cloud Shell ~/tr/functions/google.js(11/14판) 의 makeVerify 와 같은 코드(Firebase ID 토큰 + 설정 시 App Check).
// 구글 대리 함수(placesSearch 등)는 이 codebase 에 없다 — 그쪽 원본은 여전히 Cloud Shell(codebase proxy).
function makeVerify(requireAppCheck) {
  const { getAuth } = require('firebase-admin/auth');
  const { getAppCheck } = require('firebase-admin/app-check');
  return async (req) => {
    const idTok = (req.get('authorization') || '').replace(/^Bearer /, '');
    if (!idTok) throw Object.assign(new Error('auth'), { reason: 'auth' });
    const user = await getAuth().verifyIdToken(idTok);
    if (requireAppCheck.value()) {
      const ac = req.get('x-firebase-appcheck') || '';
      if (!ac) throw Object.assign(new Error('appcheck'), { reason: 'appcheck' });
      try { await getAppCheck().verifyToken(ac); } catch (_) { throw Object.assign(new Error('appcheck'), { reason: 'appcheck' }); }
    }
    return { uid: user.uid, email: user.email || '', emailVerified: user.email_verified === true,
      anonymous: !!user.firebase && user.firebase.sign_in_provider === 'anonymous' };
  };
}
module.exports = { makeVerify };
