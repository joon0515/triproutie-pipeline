'use strict';
// 292기 광고 푸시 시험 — 가짜 Firestore·FCM(네트워크 0). node test_mkt_fn.js
const assert = require('assert');
const M = require('./mkt_fn');
const DAY = 864e5;
let pass = 0;
const t = (name, fn) => Promise.resolve().then(fn).then(() => { pass++; }, (e) => { console.error('FAIL', name, e && e.message); process.exitCode = 1; });

// ---- 가짜 Firestore(필요한 만큼만) ----
function fakeDb() {
  const S = new Map();
  const get = (o, p) => p.split('.').reduce((a, k) => (a == null ? a : a[k]), o);
  const snap = (path) => ({ id: path.split('/').pop(), exists: S.has(path), data: () => S.get(path), ref: docRef(path) });
  function docRef(path) {
    return {
      path, get: async () => snap(path),
      set: async (v, o) => { S.set(path, o && o.merge ? { ...(S.get(path) || {}), ...v } : v); },
      update: async (v) => { if (!S.has(path)) throw new Error('no doc'); S.set(path, { ...S.get(path), ...v }); },
      create: async (v) => { if (S.has(path)) throw Object.assign(new Error('exists'), { code: 6 }); S.set(path, v); },
      delete: async () => { S.delete(path); },
    };
  }
  function query(col, fs = [], lim = 1e9, after = null, ord = null) {
    const val = (v) => (v instanceof Date ? v.getTime() : v && v.toMillis ? v.toMillis() : v);
    return {
      where: (f, op, v) => query(col, fs.concat([[f, op, v]]), lim, after, ord),
      orderBy: (f) => query(col, fs, lim, after, f),
      limit: (n) => query(col, fs, n, after, ord),
      startAfter: (d) => query(col, fs, lim, d, ord),
      get: async () => {
        let ks = [...S.keys()].filter((k) => k.startsWith(col + '/') && k.split('/').length === col.split('/').length + 1).sort();
        ks = ks.filter((k) => fs.every(([f, op, v]) => { const x = val(get(S.get(k), f)); return op === '==' ? x === v : op === '<=' ? x != null && x <= val(v) : false; }));
        if (after) ks = ks.filter((k) => k > after.ref.path);
        return { docs: ks.slice(0, lim).map(snap) };
      },
    };
  }
  return {
    S, doc: docRef, collection: (c) => query(c),
    runTransaction: async (fn) => {
      const w = [];
      const r = await fn({ get: (ref) => ref.get(), create: (ref, v) => w.push(['c', ref, v]), set: (ref, v, o) => w.push(['s', ref, v, o]) });
      for (const [k, ref, v, o] of w) { if (k === 'c') await ref.create(v); else await ref.set(v, o); }
      return r;
    },
  };
}
const ts = (x) => ({ toMillis: () => x });
const NOW = Date.UTC(2026, 10, 25, 3, 0); // 서울 12:00
const camp = { title: '오사카 겨울 숙소 모음', body: '이번 주 고른 숙소 5곳을 모았어요.', status: 'ready', expires_at: NOW + 3 * DAY, night: false, for_trip: false };
function world(over = {}) {
  const db = fakeDb(), sent = [];
  db.S.set('mkt_campaigns/c1', { ...camp, ...(over.camp || {}) });
  const users = over.users || { u1: { mkt: { push: true, night: false, at: ts(NOW - 10 * DAY) }, tz_off: 540 } };
  for (const [u, d] of Object.entries(users)) { db.S.set('users/' + u, d); db.S.set(`users/${u}/fcm_tokens/a`, { fcm_token: 'tok_' + u }); }
  for (const [k, v] of Object.entries(over.extra || {})) db.S.set(k, v);
  let now = over.now || NOW;
  const core = M.makeMktCore({ db, messaging: { send: async (m) => { sent.push(m); } }, now: () => now, contact: () => '', log: () => {} });
  return { db, sent, core, setNow: (x) => { now = x; } };
}

(async () => {
  await t('문구: 맨 앞 (광고)+전송자, 끝 수신거부', () => {
    const m = M.buildAd(camp, '');
    assert.ok(m.title.startsWith('(광고) 트립루티 · '));
    assert.ok(m.body.endsWith(M.OPT_OUT));
  });
  await t('캠페인 글에 (광고)·수신거부를 넣으면 거부(서버가 붙임)', () => { assert.strictEqual(M.campaignOk({ ...camp, title: '(광고) 세일' }), 'dup_label'); assert.strictEqual(M.campaignOk({ ...camp, body: 'x'.repeat(91) }), 'length'); });
  await t('동의 없으면 안 감', () => assert.strictEqual(M.eligible({ user: { mkt: { push: false } }, c: camp, now: NOW }), 'no_consent'));
  await t('도달 시각 모르면 안 감', () => assert.strictEqual(M.eligible({ user: { mkt: { push: true, at: ts(NOW - 9 * DAY) } }, c: camp, now: NOW }), 'no_tz'));
  await t('동의 직후 3일은 보류', () => assert.strictEqual(M.eligible({ user: { mkt: { push: true, at: ts(NOW - DAY) }, tz_off: 540 }, c: camp, now: NOW }), 'later'));
  const U = (night) => ({ mkt: { push: true, night, at: ts(NOW - 10 * DAY) }, tz_off: 540 });
  await t('기기 시각 21~8시는 안 감(서울 22시·07시·20시)', () => {
    for (const h of [22, 7, 20, 23, 3]) assert.strictEqual(M.eligible({ user: U(false), c: camp, now: Date.UTC(2026, 10, 25, (h - 9 + 24) % 24) }), 'later', 'h' + h);
    assert.strictEqual(M.eligible({ user: U(false), c: camp, now: Date.UTC(2026, 10, 25, 1) }), null); // 서울 10시
  });
  await t('야간 동의+야간 캠페인이어도 21시대만, 23시는 안 감', () => {
    assert.strictEqual(M.eligible({ user: U(true), c: { ...camp, night: true }, now: Date.UTC(2026, 10, 25, 12, 30) }), null); // 21:30
    assert.strictEqual(M.eligible({ user: U(false), c: { ...camp, night: true }, now: Date.UTC(2026, 10, 25, 12, 30) }), 'later');
    assert.strictEqual(M.eligible({ user: U(true), c: { ...camp, night: true }, now: Date.UTC(2026, 10, 25, 14) }), 'later'); // 23:00
  });
  await t('LA 사용자는 LA 시각으로(서울 12시 = LA 19시 → 감, 서울 15시 = LA 22시 → 보류)', () => {
    const la = { mkt: { push: true, at: ts(NOW - 10 * DAY) }, tz_off: -480 };
    assert.strictEqual(M.eligible({ user: la, c: camp, now: Date.UTC(2026, 10, 25, 3) }), null);
    assert.strictEqual(M.eligible({ user: la, c: camp, now: Date.UTC(2026, 10, 25, 6) }), 'later');
  });
  await t('빈도: 6일 전에 1건이면 주 상한, 8일 전이면 감, 30일에 3건이면 월 상한', () => {
    assert.strictEqual(M.eligible({ user: U(false), state: { recent: [NOW - 6 * DAY] }, c: camp, now: NOW }), 'cap_week');
    assert.strictEqual(M.eligible({ user: U(false), state: { recent: [NOW - 8 * DAY] }, c: camp, now: NOW }), null);
    assert.strictEqual(M.eligible({ user: U(false), state: { recent: [NOW - 8 * DAY, NOW - 16 * DAY, NOW - 24 * DAY] }, c: camp, now: NOW }), 'cap_month');
  });
  await t('여행 중: 그 도시 여행 캠페인만', () => {
    const trip = M.activeTrip([{ start_date: '2026-11-24', end_date: '2026-11-27', city: '오사카' }], NOW, 540);
    assert.deepStrictEqual(trip, { city: '오사카' });
    assert.strictEqual(M.eligible({ user: U(false), trip, c: camp, now: NOW }), 'on_trip_mismatch');
    assert.strictEqual(M.eligible({ user: U(false), trip, c: { ...camp, for_trip: true, cities: ['도쿄'] }, now: NOW }), 'on_trip_mismatch');
    assert.strictEqual(M.eligible({ user: U(false), trip, c: { ...camp, for_trip: true, cities: ['오사카'] }, now: NOW }), null);
    assert.strictEqual(M.eligible({ user: U(false), c: { ...camp, for_trip: true, cities: ['오사카'] }, now: NOW }), 'not_on_trip');
  });
  await t('보내기 두 번 돌려도 1번만(멱등) + 빈도 목록 기록', async () => {
    const w = world();
    const a = await w.core.dispatch(); const b = await w.core.dispatch();
    assert.strictEqual(a.sent, 1); assert.strictEqual(b.sent, 0); assert.strictEqual(w.sent.length, 1);
    assert.strictEqual(w.db.S.get('mkt_sends/c1_u1').status, 'sent');
    assert.strictEqual(w.db.S.get('mkt_state/u1').recent.length, 1);
    assert.ok(w.sent[0].notification.title.startsWith('(광고) 트립루티'));
  });
  await t('다른 캠페인이 이틀 뒤 와도 주 상한으로 안 감', async () => {
    const w = world(); await w.core.dispatch();
    w.db.S.set('mkt_campaigns/c2', { ...camp, expires_at: NOW + 9 * DAY }); w.setNow(NOW + 2 * DAY);
    const r = await w.core.dispatch();
    assert.strictEqual(r.sent, 0); assert.strictEqual(r.skipped.cap_week, 1); assert.strictEqual(w.sent.length, 1);
  });
  await t('동의 끈 사람·만료 캠페인은 안 감', async () => {
    const w = world({ users: { u1: { mkt: { push: false, at: ts(NOW - 10 * DAY) }, tz_off: 540 } } });
    assert.strictEqual((await w.core.dispatch()).sent, 0);
    const w2 = world({ camp: { expires_at: NOW - 1 } }); await w2.core.dispatch();
    assert.strictEqual(w2.db.S.get('mkt_campaigns/c1').status, 'done'); assert.strictEqual(w2.sent.length, 0);
  });
  await t('처리 결과 통지: 광고 문구 없음, 같은 기록 두 번이면 1번', async () => {
    const w = world();
    const d = { items: [{ id: 'ad_push', ok: true }], at: ts(NOW), tz_off: 540 };
    assert.strictEqual(await w.core.onConsent('u1', 'k1', d), 'sent');
    assert.strictEqual(await w.core.onConsent('u1', 'k1', d), 'dup');
    assert.strictEqual(w.sent.length, 1);
    const n = w.sent[0].notification;
    assert.ok(!/\(광고\)/.test(n.title + n.body)); assert.ok(n.body.includes('2026년 11월 25일 광고성 정보 수신 동의'));
    assert.strictEqual(await w.core.onConsent('u1', 'k2', { items: [{ id: 'terms', ok: true }] }), 'not_mkt');
  });
  await t('2년 재확인: 2년-30일 전엔 없음, 지나면 1번만, 광고 문구 없음', async () => {
    const at = NOW - 701 * DAY;
    assert.strictEqual(M.reconfirmDue({ push: true, at: ts(NOW - 600 * DAY) }, {}, NOW), null);
    const w = world({ users: { u1: { mkt: { push: true, at: ts(at) }, tz_off: 540 }, u2: { mkt: { push: true, at: ts(NOW - 100 * DAY) }, tz_off: 540 } } });
    const r1 = await w.core.reconfirm(); const r2 = await w.core.reconfirm();
    assert.strictEqual(r1.sent, 1); assert.strictEqual(r2.sent, 0); assert.strictEqual(w.sent.length, 1);
    assert.ok(!/\(광고\)/.test(w.sent[0].notification.body)); assert.ok(w.sent[0].notification.title.includes('수신동의 확인'));
    // 다음 주기 = 안내한 날부터 다시 2년
    assert.strictEqual(M.reconfirmDue({ push: true, at: ts(at) }, { rc_at: NOW }, NOW + 600 * DAY), null);
    assert.ok(M.reconfirmDue({ push: true, at: ts(at) }, { rc_at: NOW }, NOW + 701 * DAY));
  });
  await t('죽은 토큰은 지움', async () => {
    const w = world();
    w.core = M.makeMktCore({ db: w.db, messaging: { send: async () => { throw Object.assign(new Error('x'), { code: 'messaging/registration-token-not-registered' }); } }, now: () => NOW, contact: () => '', log: () => {} });
    await w.core.dispatch();
    assert.ok(!w.db.S.has('users/u1/fcm_tokens/a')); assert.strictEqual(w.db.S.get('mkt_sends/c1_u1').status, 'no_token');
  });
  console.log(`mkt_fn: ${pass}/${process.exitCode ? 1 : 0}`);
})();
