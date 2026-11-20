'use strict';
// acct_fn.js — 계정 삭제 서버 함수(codebase "proxy" 방식: 순수 처리기 makeAcctHandler(deps) + 배포용 makeAcctFunctions()).
// index.js 에 한 줄:  Object.assign(exports, require('./acct_fn').makeAcctFunctions());
// 흐름: 앱(로그인 상태) → POST deleteAccount {mode:'preview'} → 지워질 것 요약 → 사용자가 재확인 → POST {mode:'run', confirm:'DELETE', shared:'transfer'|'delete'}
//   → Firestore(여행·하위 문서·초대·커뮤니티·사용량) → Storage(사용자 폴더) → 인증 계정 순서로 지움. 인증 계정을 맨 끝에 지우므로 중간에 멈추면 다시 로그인해 재시도할 수 있다(모든 단계가 여러 번 돌려도 같은 결과).
// 기록: uid·이메일을 로그에 남기지 않는다(uid 해시 앞 12자와 개수만).
const crypto = require('crypto');
const REGION = 'asia-northeast3';
const CFG = {
  retainBan: true,            // 처리방침 4절 "이용 제한 기록 1년 보관"에 맞춤. [변호사 검토] 끄면 아무것도 남기지 않음
  retainDays: 365,
  storagePrefixes: (uid) => [`uploads/import/${uid}/`, `users/${uid}/`], // Y_import 사진 올리기 경로 + FF 기본 올리기 경로
  userDocs: (uid) => [`usage/${uid}`, `_quota/u_${uid}`, `_quota/ur_${uid}`, `_quota/imp_${uid}`, `_quota/ocr_${uid}`, `invite_claims/${uid}`],
  originCoordKey: /^(lat|lng|lon|latitude|longitude|acc|accuracy|at|ts|measured_at|nearby|coord|coords)$/i,
  chunk: 20,
};
const sha = (s) => crypto.createHash('sha256').update(String(s)).digest('hex');
const refUid = (m) => (typeof m === 'string' ? m : (m && typeof m.path === 'string' ? m.path.split('/').pop() : ''));
const uniq = (docs) => { const seen = new Set(); return docs.filter((d) => (seen.has(d.ref.path) ? false : (seen.add(d.ref.path), true))); };

// 다음 주인: 남는 동행 중 편집 권한(역할 칸이 없으면 편집) 먼저, 없으면 첫 동행. 내보낸 사람(removed_members)은 제외.
function pickNextOwner(d, uid) {
  const removed = new Set((d.removed_members || []).map(refUid));
  const others = (d.members || []).map(refUid).filter((u) => u && u !== uid && !removed.has(u));
  const roles = d.member_roles || {};
  return others.find((u) => (roles[u] || 'editor') === 'editor') || others[0] || '';
}

function makeAcctHandler(deps) {
  const { db, FV } = deps;
  const now = deps.now || (() => Date.now());
  const log = deps.log || (() => {});
  const cfg = { ...CFG, ...(deps.cfg || {}) };
  const userRef = (uid) => db.doc(`users/${uid}`);
  const each = async (list, fn) => { for (let i = 0; i < list.length; i += cfg.chunk) await Promise.all(list.slice(i, i + cfg.chunk).map(fn)); };
  const q = async (query) => (await query.get()).docs;

  async function findTrips(uid) {
    const me = userRef(uid), T = db.collection('trips');
    const owned = uniq([...(await q(T.where('owner_uid', '==', uid))), ...(await q(T.where('owner', '==', me)))]);
    const ownedPaths = new Set(owned.map((d) => d.ref.path));
    const member = uniq([...(await q(T.where('members', 'array-contains', uid))), ...(await q(T.where('members', 'array-contains', me)))])
      .filter((d) => !ownedPaths.has(d.ref.path));
    const solo = [], shared = [];
    for (const d of owned) (pickNextOwner(d.data(), uid) ? shared : solo).push(d);
    return { solo, shared, member };
  }

  // 공유 여행에서 이 사람 흔적 지우기: 내 일기(작성자 칸이 없는 옛 일기는 주인 것), 내가 남긴 변경 기록, 재생성 잠금.
  async function scrubTrip(tripDoc, uid, wasOwner) {
    let n = 0;
    for (const sub of ['journal_entries', 'journal_days']) {
      const col = tripDoc.ref.collection(sub);
      const mine = await q(col.where('author_uid', '==', uid));
      const legacy = wasOwner ? (await q(col)).filter((x) => !('author_uid' in x.data())) : [];
      const list = uniq([...mine, ...legacy]);
      await each(list, (x) => x.ref.delete()); n += list.length;
    }
    const acts = await q(tripDoc.ref.collection('activity').where('by', '==', uid));
    await each(acts, (x) => x.ref.delete()); n += acts.length;
    const locks = (await q(tripDoc.ref.collection('locks'))).filter((x) => { const v = x.data(); return v.by === uid || v.uid === uid || v.owner_uid === uid; });
    await each(locks, (x) => x.ref.delete());
    return n + locks.length;
  }

  async function transferTrip(tripDoc, uid) {
    const d = tripDoc.data(), next = pickNextOwner(d, uid), me = userRef(uid), nextRef = userRef(next);
    const scrubbed = await scrubTrip(tripDoc, uid, true);
    const upd = {
      owner: nextRef, owner_uid: next,
      members: FV.arrayRemove(uid, me, next, nextRef),
      [`member_roles.${uid}`]: FV.delete(), [`member_roles.${next}`]: FV.delete(), [`member_names.${uid}`]: FV.delete(),
      updated_by: 'system:account_deleted', updated_at: FV.serverTimestamp(), owner_changed_at: FV.serverTimestamp(),
    };
    // 출발지 정밀 좌표는 떠나는 주인의 위치정보 → 좌표 칸만 지우고 동네·도시 이름은 남김(방침 4절과 같은 기준)
    if (d.origin && typeof d.origin === 'object' && !Array.isArray(d.origin)) {
      for (const k of Object.keys(d.origin)) if (cfg.originCoordKey.test(k)) upd[`origin.${k}`] = FV.delete();
    }
    await tripDoc.ref.update(upd);
    return scrubbed;
  }

  async function leaveTrip(tripDoc, uid) {
    const scrubbed = await scrubTrip(tripDoc, uid, false);
    await tripDoc.ref.update({
      members: FV.arrayRemove(uid, userRef(uid)),
      [`member_roles.${uid}`]: FV.delete(), [`member_names.${uid}`]: FV.delete(),
      updated_by: 'system:account_deleted', updated_at: FV.serverTimestamp(),
    });
    return scrubbed;
  }

  async function run(user, sharedMode) {
    const uid = user.uid, me = userRef(uid), c = { trips_deleted: 0, trips_transferred: 0, trips_left: 0, trip_items: 0, invites: 0, posts: 0, comments: 0, likes: 0, docs: 0, files: 0, retained: 0 };
    const marker = db.doc(`_acct_delete/${sha('job:' + uid).slice(0, 32)}`);
    let step = 'start';
    try {
      await marker.set({ state: 'running', at: now() });
      step = 'trips';
      const t = await findTrips(uid);
      const toDelete = sharedMode === 'delete' ? [...t.solo, ...t.shared] : t.solo;
      for (const d of toDelete) { await db.recursiveDelete(d.ref); c.trips_deleted++; }
      if (sharedMode !== 'delete') for (const d of t.shared) { c.trip_items += await transferTrip(d, uid); c.trips_transferred++; }
      for (const d of t.member) { c.trip_items += await leaveTrip(d, uid); c.trips_left++; }
      step = 'invites';
      const inv = await q(db.collection('trip_invites').where('created_by', '==', uid));
      await each(inv, (x) => x.ref.delete()); c.invites = inv.length;
      step = 'posts';
      const posts = await q(db.collection('posts').where('author', '==', me));
      for (const p of posts) { await db.recursiveDelete(p.ref); c.posts++; }
      step = 'comments';
      const gone = new Set(posts.map((p) => p.ref.path));
      const cm = (await q(db.collectionGroup('comments').where('author', '==', me))).filter((x) => !gone.has(x.ref.parent.parent.path));
      await each(cm, async (x) => { await x.ref.delete(); try { await x.ref.parent.parent.update({ comment_count: FV.increment(-1) }); } catch (_) { /* 부모 글이 이미 없음 */ } });
      c.comments = cm.length;
      step = 'likes';
      const liked = await q(db.collection('posts').where('liked_by', 'array-contains', me));
      await each(liked, (x) => x.ref.update({ liked_by: FV.arrayRemove(me), like_count: FV.increment(-1) })); c.likes = liked.length;
      step = 'social';
      const links = await q(db.collection('social_links').where('uid', '==', uid));
      await each(links, (x) => x.ref.delete()); c.docs += links.length;
      step = 'retain';
      const us = await me.get(), ud = us.exists ? us.data() : {};
      if (cfg.retainBan) {
        const until = ud.community_ban_until, untilMs = until && typeof until.toMillis === 'function' ? until.toMillis() : Number(until) || 0;
        if (untilMs > now() || (ud.strike_count || 0) > 0) {
          // 원문 식별값 없이 해시만(같은 이메일로 다시 가입했을 때 반복 위반 확인용). purge_at 에 Firestore TTL 을 걸면 자동 삭제. [변호사 검토]
          const key = sha('ban:' + (user.email ? user.email.trim().toLowerCase() : uid));
          await db.doc(`_acct_retained/${key}`).set({ kind: 'ban', community_ban_until: until || null, strike_count: ud.strike_count || 0, deleted_at: new Date(now()), purge_at: new Date(now() + cfg.retainDays * 864e5) });
          c.retained = 1;
        }
      }
      step = 'userdocs';
      for (const p of cfg.userDocs(uid)) { await db.doc(p).delete(); c.docs++; }
      await db.recursiveDelete(me); c.docs++;
      step = 'storage';
      for (const p of cfg.storagePrefixes(uid)) c.files += (await deps.storage.deletePrefix(p)) || 0;
      step = 'auth';
      try { await deps.auth.deleteUser(uid); } catch (e) { if (!(e && e.code === 'auth/user-not-found')) throw e; }
      step = 'done';
      await marker.delete();
      log('acct_delete', { u: sha(uid).slice(0, 12), ok: true, ...c });
      return { status: 200, body: { ok: true, deleted: c } };
    } catch (e) {
      log('acct_delete', { u: sha(uid).slice(0, 12), ok: false, step, err: String((e && e.code) || (e && e.name) || 'error').slice(0, 60) });
      try { await marker.set({ state: 'failed', step, at: now() }); } catch (_) { /* 표시 실패는 무시 */ }
      return { status: 500, body: { ok: false, reason: 'partial', step } }; // 인증 계정이 남아 있으므로 앱에서 다시 시도 가능
    }
  }

  async function preview(user) {
    const t = await findTrips(user.uid);
    const posts = await q(db.collection('posts').where('author', '==', userRef(user.uid)));
    const name = (d) => String(d.name || d.title || d.trip_name || d.city || '').slice(0, 40);
    return { status: 200, body: { ok: true, preview: {
      solo: t.solo.length, member: t.member.length, posts: posts.length,
      shared: t.shared.slice(0, 20).map((x) => { const d = x.data(), nx = pickNextOwner(d, user.uid); return { name: name(d), others: (d.members || []).map(refUid).filter((u) => u && u !== user.uid).length, next_name: String((d.member_names || {})[nx] || '').slice(0, 30) }; }),
      shared_count: t.shared.length,
    } } };
  }

  return async function handler(req) {
    if (req.method !== 'POST') return { status: 405, body: { ok: false, reason: 'method' } };
    let user;
    try { user = await deps.verify(req); } catch (e) { return { status: 401, body: { ok: false, reason: (e && e.reason) || 'auth' } }; }
    const b = (req.body && typeof req.body === 'object') ? req.body : {};
    const maxAge = Number(deps.maxAuthAgeMin ? deps.maxAuthAgeMin() : 0) || 0;
    if (maxAge > 0 && !user.anonymous && !(user.authTime && now() - user.authTime * 1000 <= maxAge * 60000)) return { status: 401, body: { ok: false, reason: 'reauth' } };
    if (b.mode === 'preview') { try { return await preview(user); } catch (_) { return { status: 500, body: { ok: false, reason: 'preview' } }; } }
    if (b.mode !== 'run' || b.confirm !== 'DELETE') return { status: 400, body: { ok: false, reason: 'confirm' } };
    const shared = b.shared === 'delete' ? 'delete' : 'transfer';
    return run(user, shared);
  };
}

// ID 토큰(취소 여부까지) + 설정 시 App Check. google.js 의 makeVerify 와 같은 방식에 auth_time 만 더함(google.js 는 건드리지 않음).
function makeVerifyAcct(requireAppCheck) {
  const { getAuth } = require('firebase-admin/auth');
  return async (req) => {
    const idTok = (req.get('authorization') || '').replace(/^Bearer /, '');
    if (!idTok) throw Object.assign(new Error('auth'), { reason: 'auth' });
    const u = await getAuth().verifyIdToken(idTok, true);
    // 276기: 계정 삭제는 되풀이되면 안 되므로 강제 모드에서는 재사용 방지 토큰(consume)을 요구
    await require('./appcheck').checkAppCheck(req, requireAppCheck.value() === true, { consume: requireAppCheck.value() === true });
    return { uid: u.uid, email: u.email || '', authTime: u.auth_time || 0, anonymous: !!u.firebase && u.firebase.sign_in_provider === 'anonymous' };
  };
}

function makeAcctFunctions() {
  const { onRequest } = require('firebase-functions/v2/https');
  const { defineBoolean, defineInt } = require('firebase-functions/params');
  const { logger } = require('firebase-functions');
  const { getFirestore, FieldValue } = require('firebase-admin/firestore');
  const { getAuth } = require('firebase-admin/auth');
  const { getStorage } = require('firebase-admin/storage');
  const REQUIRE_APP_CHECK = defineBoolean('REQUIRE_APP_CHECK', { default: false });
  const ACCT_MAX_AUTH_AGE_MIN = defineInt('ACCT_MAX_AUTH_AGE_MIN', { default: 0 }); // 0 = 최근 로그인 요구 안 함
  let handler = null;
  const opts = { region: REGION, maxInstances: 2, concurrency: 4, timeoutSeconds: 300, memory: '512MiB', cors: true, invoker: 'public' };
  return {
    deleteAccount: onRequest(opts, async (req, res) => {
      if (!handler) {
        const db = getFirestore();
        handler = makeAcctHandler({
          db, FV: FieldValue, verify: makeVerifyAcct(REQUIRE_APP_CHECK), now: () => Date.now(), log: (n, o) => logger.info(n, o),
          maxAuthAgeMin: () => { try { return ACCT_MAX_AUTH_AGE_MIN.value(); } catch (_) { return 0; } },
          auth: { deleteUser: (uid) => getAuth().deleteUser(uid) },
          storage: { deletePrefix: async (prefix) => { const b = getStorage().bucket(); const [files] = await b.getFiles({ prefix }); await b.deleteFiles({ prefix, force: true }); return files.length; } },
        });
      }
      const r = await handler(req);
      res.status(r.status).json(r.body);
    }),
  };
}

module.exports = { CFG, REGION, makeAcctHandler, makeAcctFunctions, makeVerifyAcct, pickNextOwner };
