'use strict';
// node test_acct_fn.js — 에뮬레이터·네트워크·키 없이 도는 단위 시험(메모리 가짜 Firestore·Storage·Auth).
const { makeAcctHandler, pickNextOwner, CFG } = require('./acct_fn');
let pass = 0, fail = 0;
const ok = (c, m) => { if (c) pass++; else { fail++; console.log('FAIL', m); } };

// ---- 가짜 Firestore(필요한 만큼만): doc/collection/collectionGroup/where(==, array-contains)/recursiveDelete/FieldValue ----
function fakeDb(seed) {
  const store = new Map(); const ops = []; const db = { store, ops, failOn: null };
  const isRef = (v) => v && typeof v === 'object' && typeof v.path === 'string' && typeof v.get === 'function';
  const same = (a, b) => (isRef(a) && isRef(b) ? a.path === b.path : a === b);
  const FV = { arrayRemove: (...v) => ({ __fv: 'rm', v }), delete: () => ({ __fv: 'del' }), increment: (n) => ({ __fv: 'inc', n }), serverTimestamp: () => ({ __fv: 'ts' }) };
  const chk = (op, path) => { ops.push(op + ' ' + path); if (db.failOn && db.failOn(op, path)) throw Object.assign(new Error('injected'), { code: 'unavailable' }); };
  const snap = (path) => ({ ref: doc(path), id: path.split('/').pop(), exists: store.has(path), data: () => store.get(path) });
  function doc(path) {
    const parts = path.split('/');
    return {
      path, id: parts[parts.length - 1], parent: coll(parts.slice(0, -1).join('/')),
      get: async () => { chk('get', path); return snap(path); },
      set: async (v) => { chk('set', path); store.set(path, { ...v }); },
      delete: async () => { chk('delete', path); store.delete(path); },
      update: async (u) => {
        chk('update', path); if (!store.has(path)) throw Object.assign(new Error('nf'), { code: 5 });
        const d = JSON.parse(JSON.stringify(store.get(path), (k, v) => (isRef(v) ? { __ref: v.path } : v)), (k, v) => (v && v.__ref ? doc(v.__ref) : v));
        for (const [k, v] of Object.entries(u)) {
          const ks = k.split('.'); let o = d; for (const s of ks.slice(0, -1)) { if (typeof o[s] !== 'object' || o[s] === null) o[s] = {}; o = o[s]; }
          const last = ks[ks.length - 1];
          if (v && v.__fv === 'del') delete o[last];
          else if (v && v.__fv === 'rm') o[last] = (o[last] || []).filter((x) => !v.v.some((y) => same(x, y)));
          else if (v && v.__fv === 'inc') o[last] = (o[last] || 0) + v.n;
          else if (v && v.__fv === 'ts') o[last] = 'TS';
          else o[last] = v;
        }
        store.set(path, d);
      },
      collection: (n) => coll(path + '/' + n),
    };
  }
  function query(filterPath, conds) {
    return {
      where: (f, op, v) => query(filterPath, [...conds, [f, op, v]]),
      get: async () => {
        chk('query', filterPath.label);
        const docs = [...store.keys()].filter(filterPath.match).filter((p) => conds.every(([f, op, v]) => {
          const x = store.get(p)[f];
          return op === '==' ? same(x, v) : op === 'array-contains' ? Array.isArray(x) && x.some((y) => same(y, v)) : false;
        })).map(snap);
        return { docs, size: docs.length };
      },
    };
  }
  function coll(path) {
    if (!path) return null;
    const parts = path.split('/'), depth = parts.length + 1;
    return { path, id: parts[parts.length - 1], parent: parts.length > 1 ? doc(parts.slice(0, -1).join('/')) : null, doc: (id) => doc(path + '/' + id),
      ...query({ label: path, match: (p) => p.startsWith(path + '/') && p.split('/').length === depth }, []) };
  }
  db.doc = doc; db.collection = coll;
  db.collectionGroup = (n) => query({ label: '**/' + n, match: (p) => { const s = p.split('/'); return s[s.length - 2] === n; } }, []);
  db.recursiveDelete = async (ref) => { chk('rdelete', ref.path); for (const k of [...store.keys()]) if (k === ref.path || k.startsWith(ref.path + '/')) store.delete(k); };
  for (const [k, v] of Object.entries(seed(doc))) store.set(k, v);
  return { db, FV };
}

const NOW = Date.parse('2026-11-17T03:00:00Z');
function rig(opt = {}) {
  const { db, FV } = fakeDb((ref) => ({
    'users/A': { uid: 'A', display_name: '가', email: 'a@x.test', strike_count: opt.strike || 0 },
    'users/A/blocks/b1': { target: 'Z' },
    'users/B': { uid: 'B' }, 'users/C': { uid: 'C' },
    // A 혼자 쓰는 여행(주인 칸이 uid 인 것 / 참조인 것)
    'trips/solo1': { owner_uid: 'A', owner: ref('users/A'), members: [], name: '오사카' },
    'trips/solo1/places/p1': { n: 1 }, 'trips/solo1/expenses/e1': { paid_by: '가', amount: 1 }, 'trips/solo1/journal_entries/j1': { text: '비밀' },
    'trips/solo2': { owner: ref('users/A'), name: '옛 여행' },
    // A 가 주인, B(보기)·C(편집) 동행 → C 에게 이전
    'trips/sh1': { owner_uid: 'A', owner: ref('users/A'), members: ['B', 'C'], member_roles: { B: 'viewer', C: 'editor' }, member_names: { A: '가', B: '나', C: '다' }, name: '부산', origin: { lat: 37.5, lng: 127, acc: 12, at: 1, city: '서울', dong: '역삼동' } },
    'trips/sh1/places/p1': { n: 1 }, 'trips/sh1/expenses/e1': { paid_by: '가', amount: 3 },
    'trips/sh1/journal_entries/jA': { author_uid: 'A', text: 'A 일기' }, 'trips/sh1/journal_entries/jOld': { text: '작성자 칸 없는 옛 일기(주인 것)' },
    'trips/sh1/journal_entries/jC': { author_uid: 'C', text: 'C 일기' }, 'trips/sh1/journal_days/dA': { author_uid: 'A' },
    'trips/sh1/activity/a1': { by: 'A', type: 'edit' }, 'trips/sh1/activity/a2': { by: 'C', type: 'edit' }, 'trips/sh1/locks/regen': { by: 'A' },
    // 동행이 내보낸 사람뿐이면 혼자 쓰는 여행으로 본다
    'trips/sh2': { owner_uid: 'A', members: [], removed_members: ['B'], name: '빈 공유' },
    // B 가 주인, A 는 동행(uid 로 / 참조로)
    'trips/m1': { owner_uid: 'B', owner: ref('users/B'), members: ['A', 'C'], member_roles: { A: 'editor' }, member_names: { A: '가', C: '다' } },
    'trips/m1/journal_entries/jA': { author_uid: 'A' }, 'trips/m1/journal_entries/jOld': { text: 'B 의 옛 일기' }, 'trips/m1/activity/a1': { by: 'A' },
    'trips/m2': { owner_uid: 'C', members: [ref('users/A')] },
    'trips/other': { owner_uid: 'B', members: ['C'] }, 'trips/other/places/p': { n: 1 },
    'trip_invites/CODEAAAAAAAAAAAA1': { created_by: 'A', trip_id: 'sh1' }, 'trip_invites/CODEBBBBBBBBBBBB1': { created_by: 'B', trip_id: 'm1' },
    'invite_claims/A': { code: 'x' }, 'invite_claims/B': { code: 'y' },
    'posts/pa': { author: ref('users/A'), hidden: false, liked_by: [ref('users/B')], like_count: 1, comment_count: 1 },
    'posts/pa/comments/c1': { author: ref('users/B'), content: 'hi' }, 'posts/pa/comments/c0': { author: ref('users/A'), content: 'mine' }, 'posts/pa/places/pl': { n: 1 },
    'posts/pb': { author: ref('users/B'), hidden: false, liked_by: [ref('users/A'), ref('users/C')], like_count: 2, comment_count: 2 },
    'posts/pb/comments/c2': { author: ref('users/A'), content: 'A 댓글' }, 'posts/pb/comments/c3': { author: ref('users/C'), content: 'C 댓글' },
    'reports/r1': { reporter: ref('users/A'), reason: 'x' },
    'social_links/kakao_1': { uid: 'A' }, 'social_links/kakao_2': { uid: 'B' },
    'usage/A': { tokens: 3 }, 'usage/B': { tokens: 1 }, '_quota/u_A': { n: 1 }, '_quota/ur_A': { n: 1 }, '_quota/imp_A': { n: 1 }, '_quota/ocr_A': { n: 1 }, '_quota/u_B': { n: 1 }, '_quota/google_2026-11-16': { n: 9 },
    'geo_cache/k': { r: [] },
  }));
  const files = new Set(['uploads/import/A/1_0.jpg', 'uploads/import/A/1_1.jpg', 'users/A/uploads/x.png', 'uploads/import/B/9.jpg', 'places/v1/t.json',
    // 289기(SCAN1120 S3): 영수증 사진 trips/{여행}/receipts/{올린 사람}/… — 탈퇴하면 A 것만(혼자 여행은 통째) 지워져야 함
    'trips/solo1/receipts/A/r1.jpg', 'trips/sh1/receipts/A/r2.jpg', 'trips/sh1/receipts/C/r3.jpg', 'trips/m1/receipts/A/r4.jpg', 'trips/m1/receipts/C/r5.jpg', 'trips/other/receipts/B/r6.jpg']);
  const authDeleted = []; const logs = [];
  const deps = {
    db, FV, now: () => NOW, log: (n, o) => logs.push(o),
    verify: async (req) => { const t = (req.get('authorization') || '').replace(/^Bearer /, ''); if (!t) throw Object.assign(new Error('auth'), { reason: 'auth' }); const [uid, at, anon] = t.split(':'); return { uid, email: uid === 'A' ? 'A@X.test' : '', authTime: Number(at) || 0, anonymous: anon === 'anon' }; },
    maxAuthAgeMin: () => opt.maxAge || 0,
    auth: { deleteUser: async (uid) => { if (opt.authFail) throw Object.assign(new Error('x'), { code: opt.authFail }); authDeleted.push(uid); } },
    storage: { deletePrefix: async (p) => { let n = 0; for (const f of [...files]) if (f.startsWith(p)) { files.delete(f); n++; } return n; } },
    cfg: opt.cfg,
  };
  return { db, files, authDeleted, logs, h: makeAcctHandler(deps) };
}
const req = (tok, body, method = 'POST') => ({ method, body, get: (h) => (h === 'authorization' && tok ? 'Bearer ' + tok : '') });
const has = (x, p) => x.db.store.has(p);
const keysOf = (x, pre) => [...x.db.store.keys()].filter((k) => k.startsWith(pre));

(async () => {
  // 다음 주인 고르기
  ok(pickNextOwner({ members: ['B', 'C'], member_roles: { B: 'viewer', C: 'editor' } }, 'A') === 'C', 'next: editor first');
  ok(pickNextOwner({ members: ['B', 'C'], member_roles: { B: 'viewer', C: 'viewer' } }, 'A') === 'B', 'next: first when no editor');
  ok(pickNextOwner({ members: ['B'] }, 'A') === 'B', 'next: no role = editor');
  ok(pickNextOwner({ members: ['A'] }, 'A') === '' && pickNextOwner({}, 'A') === '', 'next: none');
  ok(pickNextOwner({ members: [{ path: 'users/B' }], removed_members: ['B'] }, 'A') === '', 'next: removed skipped, ref form');

  // 입구 검사: 쓰기 0
  let x = rig(); const before = x.db.store.size;
  ok((await x.h(req('A', {}, 'GET'))).status === 405, 'GET 405');
  ok((await x.h(req('', { mode: 'run', confirm: 'DELETE' }))).status === 401, 'no token 401');
  let r = await x.h(req('A', { mode: 'run' })); ok(r.status === 400 && r.body.reason === 'confirm', 'no confirm 400');
  r = await x.h(req('A', { mode: 'run', confirm: 'delete' })); ok(r.status === 400, 'wrong confirm 400');
  r = await x.h(req('A', { confirm: 'DELETE' })); ok(r.status === 400, 'no mode 400');
  r = await x.h(req('A', null)); ok(r.status === 400, 'null body 400');
  ok(x.db.store.size === before && x.authDeleted.length === 0 && !x.db.ops.some((o) => /^(set|update|delete|rdelete)/.test(o)), 'rejects write nothing');

  // 미리 보기: 쓰기 0, 개수 맞음
  r = await x.h(req('A', { mode: 'preview' }));
  ok(r.status === 200 && r.body.preview.solo === 3 && r.body.preview.shared_count === 1 && r.body.preview.member === 2 && r.body.preview.posts === 1, 'preview counts ' + JSON.stringify(r.body.preview));
  ok(r.body.preview.shared[0].name === '부산' && r.body.preview.shared[0].others === 2 && r.body.preview.shared[0].next_name === '다', 'preview shared row');
  ok(x.db.store.size === before && !x.db.ops.some((o) => /^(set|update|delete|rdelete)/.test(o)), 'preview writes nothing');
  ok(!JSON.stringify(r.body).includes('"B"') && !JSON.stringify(r.body).includes('users/'), 'preview has no other uid');

  // 실행(기본: 공유 여행은 넘김)
  r = await x.h(req('A', { mode: 'run', confirm: 'DELETE' }));
  ok(r.status === 200 && r.body.ok === true, 'run 200');
  const c = r.body.deleted;
  ok(c.trips_deleted === 3 && c.trips_transferred === 1 && c.trips_left === 2, 'trip counts ' + JSON.stringify(c));
  ok(keysOf(x, 'trips/solo1').length === 0 && keysOf(x, 'trips/solo2').length === 0 && keysOf(x, 'trips/sh2').length === 0, 'solo trips + subdocs gone');
  const sh = x.db.store.get('trips/sh1');
  ok(sh.owner_uid === 'C' && sh.owner.path === 'users/C', 'transferred to editor C');
  ok(JSON.stringify(sh.members) === '["B"]' && !('A' in sh.member_roles) && !('C' in sh.member_roles) && sh.member_roles.B === 'viewer', 'members/roles after transfer');
  ok(!('A' in sh.member_names) && sh.member_names.C === '다', 'old owner name removed');
  ok(!('lat' in sh.origin) && !('lng' in sh.origin) && !('acc' in sh.origin) && !('at' in sh.origin) && sh.origin.city === '서울' && sh.origin.dong === '역삼동', 'origin coords stripped, names kept');
  ok(!has(x, 'trips/sh1/journal_entries/jA') && !has(x, 'trips/sh1/journal_entries/jOld') && !has(x, 'trips/sh1/journal_days/dA') && has(x, 'trips/sh1/journal_entries/jC'), 'owner journals gone, C journal kept');
  ok(!has(x, 'trips/sh1/activity/a1') && has(x, 'trips/sh1/activity/a2') && !has(x, 'trips/sh1/locks/regen'), 'activity/lock by A gone');
  ok(has(x, 'trips/sh1/places/p1') && has(x, 'trips/sh1/expenses/e1'), 'shared plan + expenses kept');
  const m1 = x.db.store.get('trips/m1'), m2 = x.db.store.get('trips/m2');
  ok(JSON.stringify(m1.members) === '["C"]' && !('A' in m1.member_roles) && !('A' in m1.member_names) && m1.owner_uid === 'B', 'left m1');
  ok(m2.members.length === 0, 'left m2 (ref member)');
  ok(!has(x, 'trips/m1/journal_entries/jA') && has(x, 'trips/m1/journal_entries/jOld') && !has(x, 'trips/m1/activity/a1'), 'member scrub keeps owner legacy journal');
  ok(has(x, 'trips/other') && has(x, 'trips/other/places/p'), 'unrelated trip untouched');
  ok(!has(x, 'trip_invites/CODEAAAAAAAAAAAA1') && has(x, 'trip_invites/CODEBBBBBBBBBBBB1') && !has(x, 'invite_claims/A') && has(x, 'invite_claims/B'), 'invites');
  ok(keysOf(x, 'posts/pa').length === 0, 'own post + comments + places gone');
  const pb = x.db.store.get('posts/pb');
  ok(!has(x, 'posts/pb/comments/c2') && has(x, 'posts/pb/comments/c3') && pb.comment_count === 1, 'own comment on other post gone, count -1');
  ok(pb.liked_by.length === 1 && pb.liked_by[0].path === 'users/C' && pb.like_count === 1, 'like removed');
  ok(!has(x, 'social_links/kakao_1') && has(x, 'social_links/kakao_2'), 'social link');
  ok(!has(x, 'usage/A') && has(x, 'usage/B') && keysOf(x, '_quota/').sort().join() === '_quota/google_2026-11-16,_quota/u_B', 'usage/quota');
  ok(keysOf(x, 'users/A').length === 0 && has(x, 'users/B') && has(x, 'users/C'), 'user doc + subcollections gone');
  ok(has(x, 'reports/r1') && has(x, 'geo_cache/k'), 'reports retained(1y policy), shared cache untouched');
  ok([...x.files].sort().join() === 'places/v1/t.json,trips/m1/receipts/C/r5.jpg,trips/other/receipts/B/r6.jpg,trips/sh1/receipts/C/r3.jpg,uploads/import/B/9.jpg' && c.files === 6, 'storage: only A folders + A receipts (S3) ' + [...x.files].sort().join());
  ok(x.authDeleted.join() === 'A', 'auth deleted');
  ok(keysOf(x, '_acct_delete/').length === 0 && keysOf(x, '_acct_retained/').length === 0, 'marker cleared, nothing retained');
  ok(x.db.ops.indexOf('rdelete users/A') > x.db.ops.findIndex((o) => o.startsWith('query trips')), 'order: trips before user doc');
  const lg = JSON.stringify(x.logs); ok(!/a@x|A@X/.test(lg) && !/"u":"A"/.test(lg) && x.logs[0].ok === true, 'log has no uid/email');
  ok([...x.db.store.entries()].every(([k, v]) => k.startsWith('reports/') || (!JSON.stringify(v, (kk, vv) => (vv && vv.path ? vv.path : vv)).includes('users/A') && v.owner_uid !== 'A' && !(v.members || []).includes('A'))), 'no A reference left outside reports');

  // 다시 불러도 같은 결과(이미 지워진 상태)
  r = await x.h(req('A', { mode: 'run', confirm: 'DELETE' })); ok(r.status === 200 && r.body.deleted.trips_deleted === 0, 'idempotent second run');

  // 공유 여행도 지우기 선택
  x = rig(); r = await x.h(req('A', { mode: 'run', confirm: 'DELETE', shared: 'delete' }));
  ok(r.status === 200 && r.body.deleted.trips_deleted === 4 && r.body.deleted.trips_transferred === 0 && keysOf(x, 'trips/sh1').length === 0, 'shared=delete removes shared owned trip');
  ok(has(x, 'trips/m1') && x.db.store.get('trips/m1').owner_uid === 'B', 'shared=delete never deletes trips owned by others');

  // 중간 실패: 인증 계정은 남고, 다시 시도하면 끝남
  x = rig(); x.db.failOn = (op, p) => op === 'rdelete' && p === 'posts/pa';
  r = await x.h(req('A', { mode: 'run', confirm: 'DELETE' }));
  ok(r.status === 500 && r.body.reason === 'partial' && r.body.step === 'posts', 'partial failure reported');
  ok(x.authDeleted.length === 0 && has(x, 'users/A') && x.files.has('users/A/uploads/x.png'), 'auth/user/storage kept on failure');
  const mk = keysOf(x, '_acct_delete/'); ok(mk.length === 1 && x.db.store.get(mk[0]).state === 'failed' && !mk[0].includes('/A') && !JSON.stringify(x.db.store.get(mk[0])).includes('"A"'), 'failed marker without raw uid');
  ok(!JSON.stringify(r.body).includes('injected'), 'no internal error text in response');
  x.db.failOn = null; r = await x.h(req('A', { mode: 'run', confirm: 'DELETE' }));
  ok(r.status === 200 && x.authDeleted.join() === 'A' && keysOf(x, '_acct_delete/').length === 0 && x.db.store.get('trips/sh1').owner_uid === 'C', 'retry completes');

  // 인증 계정이 이미 없으면 성공 / 다른 오류면 실패
  x = rig({ authFail: 'auth/user-not-found' }); ok((await x.h(req('A', { mode: 'run', confirm: 'DELETE' }))).status === 200, 'auth already gone = ok');
  x = rig({ authFail: 'auth/internal-error' }); r = await x.h(req('A', { mode: 'run', confirm: 'DELETE' })); ok(r.status === 500 && r.body.step === 'auth', 'auth error = partial');

  // 이용 제한 기록 보관(해시만) / 끄면 보관 없음
  x = rig({ strike: 2 }); r = await x.h(req('A', { mode: 'run', confirm: 'DELETE' }));
  const kept = keysOf(x, '_acct_retained/');
  ok(r.body.deleted.retained === 1 && kept.length === 1 && /^_acct_retained\/[0-9a-f]{64}$/.test(kept[0]), 'ban record retained under hash');
  const kv = x.db.store.get(kept[0]); ok(kv.strike_count === 2 && kv.purge_at - kv.deleted_at === 365 * 864e5 && !/a@x|"A"/i.test(JSON.stringify(kv)), 'retained has no identifier, 365d purge');
  x = rig({ strike: 2, cfg: { retainBan: false } }); await x.h(req('A', { mode: 'run', confirm: 'DELETE' })); ok(keysOf(x, '_acct_retained/').length === 0, 'retainBan off');

  // 최근 로그인 요구(설정 시) / 게스트(익명) 계정은 면제
  const sec = Math.floor(NOW / 1000);
  x = rig({ maxAge: 10 });
  r = await x.h(req('A:' + (sec - 3600), { mode: 'run', confirm: 'DELETE' })); ok(r.status === 401 && r.body.reason === 'reauth' && x.authDeleted.length === 0, 'stale login → reauth');
  r = await x.h(req('A:' + (sec - 60), { mode: 'run', confirm: 'DELETE' })); ok(r.status === 200, 'fresh login ok');
  x = rig({ maxAge: 10 }); r = await x.h(req('G:0:anon', { mode: 'run', confirm: 'DELETE' })); ok(r.status === 200 && x.authDeleted.join() === 'G', 'guest account deletable');
  ok(has(x, 'users/A') && has(x, 'trips/solo1') && x.db.store.get('trips/sh1').owner_uid === 'A', 'other users untouched by G');

  ok(typeof CFG.storagePrefixes('u')[0] === 'string' && CFG.userDocs('u').every((p) => p.endsWith('u')), 'cfg paths end with uid');
  console.log(`acct_fn: ${pass}/${fail}`);
  process.exit(fail ? 1 : 0);
})();
