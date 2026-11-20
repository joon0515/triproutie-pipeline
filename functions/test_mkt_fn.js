'use strict';
// 292기 광고 푸시 시험(적응형 상한판) — 가짜 Firestore·FCM(네트워크 0). node test_mkt_fn.js
const assert = require('assert');
const M = require('./mkt_fn');
const DAY = 864e5, H = 36e5;
let pass = 0;
const t = (name, fn) => Promise.resolve().then(fn).then(() => { pass++; }, (e) => { console.error('FAIL', name, e && e.message); process.exitCode = 1; });

function fakeDb() {
  const S = new Map();
  const get = (o, p) => p.split('.').reduce((a, k) => (a == null ? a : a[k]), o);
  const val = (v) => (v instanceof Date ? v.getTime() : v && v.toMillis ? v.toMillis() : v);
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
  const OPS = { '==': (x, v) => x === v, '<=': (x, v) => x != null && x <= v, '>=': (x, v) => x != null && x >= v };
  function query(col, fs = [], lim = 1e9, after = null) {
    return {
      where: (f, op, v) => query(col, fs.concat([[f, op, v]]), lim, after),
      orderBy: () => query(col, fs, lim, after),
      limit: (n) => query(col, fs, n, after),
      startAfter: (d) => query(col, fs, lim, d),
      get: async () => {
        let ks = [...S.keys()].filter((k) => k.startsWith(col + '/') && k.split('/').length === col.split('/').length + 1).sort();
        ks = ks.filter((k) => fs.every(([f, op, v]) => OPS[op](val(get(S.get(k), f)), val(v))));
        if (after) ks = ks.filter((k) => k > after.ref.path);
        return { docs: ks.slice(0, lim).map(snap) };
      },
    };
  }
  return {
    S, doc: docRef, collection: (c) => query(c),
    runTransaction: async (fn) => {
      const w = [];
      const r = await fn({ get: (ref) => ref.get(), create: (ref, v) => w.push(['c', ref, v]), set: (ref, v, o) => w.push(['s', ref, v, o]), update: (ref, v) => w.push(['u', ref, v]) });
      for (const [k, ref, v, o] of w) { if (k === 'c') await ref.create(v); else if (k === 'u') await ref.update(v); else await ref.set(v, o); }
      return r;
    },
  };
}
const ts = (x) => ({ toMillis: () => x });
const NOW = Date.UTC(2026, 10, 25, 3, 0); // 서울 12:00
const camp = { title: '오사카 겨울 숙소 모음', body: '이번 주 고른 숙소 5곳을 모았어요.', status: 'ready', expires_at: NOW + 9 * DAY, night: false, for_trip: false };
const U = (night) => ({ mkt: { push: true, night, at: ts(NOW - 10 * DAY) }, tz_off: 540 });
function world(over = {}) {
  const db = fakeDb(), sent = [];
  db.S.set('mkt_campaigns/c1', { ...camp, ...(over.camp || {}) });
  const users = over.users || { u2: U(false) };
  for (const [u, d] of Object.entries(users)) { db.S.set('users/' + u, d); db.S.set(`users/${u}/fcm_tokens/a`, { fcm_token: 'tok_' + u }); }
  let now = over.now || NOW;
  const send = over.send || (async (m) => { sent.push(m); });
  const core = M.makeMktCore({ db, messaging: { send }, now: () => now, contact: () => '', log: () => {} });
  return { db, sent, core, setNow: (x) => { now = x; } };
}
const row = (o) => ({ reach: 1000, sent: 2000, opens: 200, optout: 2, perm_off: 2, uninstall: 1, lvl: 2, ...o });

(async () => {
  await t('문구: 맨 앞 (광고)+전송자, 끝 수신거부', () => { const m = M.buildAd(camp, ''); assert.ok(m.title.startsWith('(광고) 트립루티 · ')); assert.ok(m.body.endsWith(M.OPT_OUT)); });
  await t('캠페인 글에 (광고)·수신거부 넣으면 거부', () => assert.strictEqual(M.campaignOk({ ...camp, title: '(광고) 세일' }), 'dup_label'));
  await t('동의 없음·시각 모름·동의 직후 24시간', () => {
    assert.strictEqual(M.eligible({ user: { mkt: { push: false } }, c: camp, now: NOW }), 'no_consent');
    assert.strictEqual(M.eligible({ user: { mkt: { push: true, at: ts(NOW - 9 * DAY) } }, c: camp, now: NOW }), 'no_tz');
    assert.strictEqual(M.eligible({ user: { mkt: { push: true, at: ts(NOW - 2 * H) }, tz_off: 540 }, c: camp, now: NOW }), 'later');
  });
  await t('슬롯(평일 12·18·10시)에만 · 야간은 야간 동의+night 캠페인 21~22시만 · 23시~8시는 안 감', () => {
    const at = (h) => Date.UTC(2026, 10, 25, (h - 9 + 24) % 24, 30); // 수요일(평일)
    for (const h of [10, 12, 18]) assert.strictEqual(M.eligible({ user: U(false), c: camp, now: at(h) }), null, 'h' + h);
    for (const h of [9, 14, 15, 20, 21, 23, 3, 8]) assert.strictEqual(M.eligible({ user: U(false), c: camp, now: at(h) }), 'later', 'h' + h);
    assert.strictEqual(M.eligible({ user: U(true), c: { ...camp, night: true }, now: at(22) }), null);
    assert.strictEqual(M.eligible({ user: U(false), c: { ...camp, night: true }, now: at(22) }), 'later');
    assert.strictEqual(M.eligible({ user: U(true), c: { ...camp, night: true }, now: at(23) }), 'later');
    assert.strictEqual(M.eligible({ user: U(true), c: { ...camp, night: true }, now: at(7) }), 'later');
  });
  await t('시작 단계 = 하루 3·주 21, 2시간 간격', () => {
    assert.deepStrictEqual(M.LEVELS[M.START], { day: 3, week: 21 });
    assert.strictEqual(M.eligible({ user: U(false), state: { recent: [NOW - 1 * H] }, c: camp, now: NOW }), 'later');
    assert.strictEqual(M.eligible({ user: U(false), state: { recent: [NOW - 4 * H] }, c: camp, now: NOW }), null);
    assert.strictEqual(M.eligible({ user: U(false), state: { recent: [NOW - 4 * H, NOW - 8 * H, NOW - 10 * H] }, c: camp, now: NOW }), 'cap_day');
    const wk = [1, 2, 3, 4, 5, 6, 6.5].flatMap((d) => [NOW - d * DAY - H, NOW - d * DAY - 5 * H, NOW - d * DAY - 9 * H]);
    assert.strictEqual(M.eligible({ user: U(false), state: { recent: wk }, c: camp, now: NOW }), 'cap_week');
  });
  await t('맨 위 단계 = 하루 4(슬롯 4개) · 개인 무시 3회마다 한 단계 내림 · 9회 쉼', () => {
    const r3 = [NOW - 4 * H, NOW - 8 * H, NOW - 12 * H];
    assert.strictEqual(M.eligible({ user: U(false), state: { recent: r3 }, c: camp, now: NOW, cohortLvl: 3 }), null);
    assert.strictEqual(M.eligible({ user: U(false), state: { recent: r3 }, c: camp, now: Date.UTC(2026, 10, 25, 6, 30), cohortLvl: 3 }), null); // 15시 = 4번째 슬롯
    assert.strictEqual(M.eligible({ user: U(false), state: { recent: r3, ign: 3 }, c: camp, now: NOW, cohortLvl: 3 }), 'cap_day');
    assert.strictEqual(M.personalLevel(2, 7), 0);
    assert.strictEqual(M.eligible({ user: U(false), state: { rest_until: NOW + DAY }, c: camp, now: NOW }), 'rest');
  });
  await t('여행 중엔 그 도시 캠페인만', () => {
    const trip = M.activeTrip([{ start_date: '2026-11-24', end_date: '2026-11-27', city: '오사카' }], NOW, 540);
    assert.strictEqual(M.eligible({ user: U(false), trip, c: camp, now: NOW }), 'on_trip_mismatch');
    assert.strictEqual(M.eligible({ user: U(false), trip, c: { ...camp, for_trip: true, cities: ['오사카'] }, now: NOW }), null);
  });
  await t('자동 조절: 표본 적음 → 그대로, 기준선 7일 없음 → 그대로', () => {
    assert.strictEqual(M.decideLevel(row({ reach: 100 }), [], { lvl: 2 }).why, 'small');
    assert.strictEqual(M.decideLevel(row(), [row(), row()], { lvl: 2 }).why, 'no_base');
  });
  const base = Array.from({ length: 10 }, () => row());
  await t('자동 조절: 거부율 1.5배+0.3%p → 한 단계, 2배+1%p → 두 단계, 1% 넘음 → 한 단계', () => {
    assert.deepStrictEqual(M.decideLevel(row({ optout: 6 }), base, { lvl: 3 }).lvl, 2);   // 0.6% vs 0.2%
    assert.deepStrictEqual(M.decideLevel(row({ optout: 13 }), base, { lvl: 3 }).why, 'hard_optout');
    assert.deepStrictEqual(M.decideLevel(row({ optout: 9 }), base.map((b) => ({ ...b, optout: 0 })), { lvl: 3 }).why, 'down1'); // 0.9%: 0+1%p 못 넘음
    assert.deepStrictEqual(M.decideLevel(row({ uninstall: 4 }), base, { lvl: 2 }).why, 'down1');
    assert.deepStrictEqual(M.decideLevel(row({ perm_off: 6 }), base, { lvl: 2 }).lvl, 1);
    assert.strictEqual(M.decideLevel(row({ optout: 6 }), base, { lvl: 0 }).lvl, 0);
  });
  await t('자동 조절: 괜찮은 날 7일 연속 → 한 단계 올림, 열람률이 떨어지면 그대로', () => {
    let lv = { lvl: 2, good: 0 };
    for (let i = 0; i < 6; i++) { lv = M.decideLevel(row(), base, lv); assert.strictEqual(lv.lvl, 2); }
    lv = M.decideLevel(row(), base, lv); assert.strictEqual(lv.why, 'up'); assert.strictEqual(lv.lvl, 3);
    assert.strictEqual(M.decideLevel(row({ opens: 100 }), base, { lvl: 2, good: 6 }).why, 'hold');
    assert.strictEqual(M.decideLevel(row(), base, { lvl: 3, good: 6 }).lvl, 3);
  });
  await t('자동 조절: 올린 단계에서 보낸 날은 기준선에서 뺌(기준선이 따라 올라가지 않음)', () => {
    const hi = Array.from({ length: 10 }, () => row({ optout: 6, lvl: 4 }));
    assert.strictEqual(M.decideLevel(row({ optout: 6 }), base.concat(hi), { lvl: 4 }).why, 'down1');
  });
  await t('보내기 두 번 돌려도 1번(멱등) · 같은 날 다음 캠페인은 다음 슬롯(18시) · 슬롯 기록', async () => {
    const w = world();
    assert.strictEqual((await w.core.dispatch()).sent, 1); assert.strictEqual((await w.core.dispatch()).sent, 0);
    assert.strictEqual(w.db.S.get('mkt_sends/c1_u2').slot, 12);
    w.db.S.set('mkt_campaigns/c2', { ...camp });
    w.setNow(NOW + 3 * H); assert.strictEqual((await w.core.dispatch()).skipped.later, 1); // 15시 = 슬롯 아님
    w.setNow(NOW + 6 * H); assert.strictEqual((await w.core.dispatch()).sent, 1);         // 18시
    w.db.S.set('mkt_campaigns/c3', { ...camp });
    w.setNow(NOW + 22 * H); assert.strictEqual((await w.core.dispatch()).sent, 1);        // 다음 날 10시
    w.db.S.set('mkt_campaigns/c4', { ...camp });
    w.setNow(NOW + 24 * H); assert.strictEqual((await w.core.dispatch()).sent, 1); // 다음 날 12시(슬롯 3개 = 하루 3건, 넘지 않음)
    w.db.S.set('mkt_campaigns/c5', { ...camp });
    w.setNow(NOW + 25 * H); assert.strictEqual((await w.core.dispatch()).skipped.later, 1); // 13시 = 슬롯 아님
    assert.strictEqual(w.sent.length, 4);
  });
  await t('A/B 탐색: 10명 중 약 1명 하루 슬롯 하나를 후보로 바꿈 · 슬롯 재선정(점수·2시간 간격·자료 적으면 그대로)', () => {
    const ex = Array.from({ length: 2000 }, (_, i) => M.slotsFor('x' + i, M.SLOTS_DEFAULT.wk, 3, '2026-11-25').explore).filter((x) => x != null).length;
    assert.ok(ex > 120 && ex < 280, 'explore ' + ex);
    const r = (open, optout = 1) => ({ sent: 1000, reach: 500, opens: open, optout, perm_off: 0, uninstall: 0 });
    assert.deepStrictEqual(M.reselectSlots({ 8: r(150), 10: r(90), 12: r(120), 15: r(60), 18: r(140), 19: r(160), 20: r(50) }, [12, 18, 10, 15]).order, [19, 8, 12, 10]);
    assert.strictEqual(M.reselectSlots({ 8: r(150), 19: r(160) }, [12, 18, 10, 15]).why, 'few');
    assert.ok(M.slotScore(r(160, 20)) < M.slotScore(r(120, 1))); // 거부가 많으면 열람이 높아도 뒤로
    assert.strictEqual(M.dayType(Date.UTC(2026, 10, 28, 3), 540), 'we');
  });
  await t('광고 거부한 사람에게는 광고 0', async () => {
    const w = world({ users: { u2: { mkt: { push: false, at: ts(NOW - 10 * DAY) }, tz_off: 540 } } });
    await w.core.dispatch(); assert.strictEqual(w.sent.length, 0);
  });
  await t('열람: 무시 횟수 0, 같은 열람 두 번이면 1번', async () => {
    const w = world(); await w.core.dispatch();
    w.db.S.set('mkt_state/u2', { ...w.db.S.get('mkt_state/u2'), ign: 5 });
    assert.strictEqual(await w.core.onOpen('u2', 'c1'), 'open'); assert.strictEqual(await w.core.onOpen('u2', 'c1'), 'dup');
    assert.strictEqual(w.db.S.get('mkt_state/u2').ign, 0); assert.strictEqual(w.db.S.get('mkt_sends/c1_u2').status, 'opened');
  });
  await t('무시: 48시간 안 열면 1회, 두 번 돌려도 1회', async () => {
    const w = world(); await w.core.dispatch(); w.setNow(NOW + 49 * H);
    assert.strictEqual(await w.core.markIgnored(), 1); assert.strictEqual(await w.core.markIgnored(), 0);
    assert.strictEqual(w.db.S.get('mkt_state/u2').ign, 1);
  });
  await t('지표: 어제 묶음별 보냄·받은 사람·열람·거부·끄기·삭제 → 문서, 단계 하루 한 번', async () => {
    const w = world(); await w.core.dispatch();
    await w.core.onOpen('u2', 'c1'); await w.core.onOpen('u2', 'c1_k');
    await w.core.onConsent('u2', 'k1', { items: [{ id: 'ad_push', ok: false }], at: ts(NOW), tz_off: 540 });
    assert.strictEqual(await w.core.onUserChange('u2', { perm_on: true }, { perm_on: false }), 'perm_off');
    w.setNow(Date.UTC(2026, 10, 25, 19, 7)); // 다음 날 04:07 KST
    const out = await w.core.metrics();
    const c = M.cohortOf('u2');
    assert.deepStrictEqual([out.all.sent, out.all.reach, out.all.opens, out.all.clicks, out.all.optout, out.all.perm_off], [1, 1, 1, 1, 1, 1]);
    const sl = w.db.S.get('mkt_metrics/2026-11-25_s_wk_12'), kv = w.db.S.get('mkt_metrics/2026-11-25_k_c1');
    assert.deepStrictEqual([sl.sent, sl.opens, sl.clicks, sl.optout, sl.perm_off], [1, 1, 1, 1, 1]); // 거부·끄기는 마지막으로 받은 광고 슬롯으로
    assert.strictEqual(kv.click, 1);
    assert.strictEqual(out.slots.why, 'init');
    assert.strictEqual(out[c].decision.why, 'small');
    assert.ok(w.db.S.has(`mkt_metrics/2026-11-25_${c}`));
    const again = await w.core.metrics(); assert.strictEqual(again[c].decision.why, 'already');
  });
  await t('삭제 추정: 토큰이 전부 죽으면 사건 1, 토큰 지움', async () => {
    const w = world({ send: async () => { throw Object.assign(new Error('x'), { code: 'messaging/registration-token-not-registered' }); } });
    await w.core.dispatch();
    assert.ok(!w.db.S.has('users/u2/fcm_tokens/a')); assert.ok([...w.db.S.keys()].some((k) => k.startsWith('mkt_ev/u2_u_')));
  });
  await t('서비스 통지(처리 결과·재확인)에 광고 낱말 없음 + 낱말 검사기', async () => {
    const w = world();
    assert.strictEqual(await w.core.onConsent('u2', 'k1', { items: [{ id: 'ad_push', ok: true }], at: ts(NOW), tz_off: 540 }), 'sent');
    assert.strictEqual(await w.core.onConsent('u2', 'k1', { items: [{ id: 'ad_push', ok: true }], at: ts(NOW) }), 'dup');
    assert.ok(M.serviceTextOk(w.sent[0].notification));
    assert.ok(M.serviceTextOk(M.reconfirmNote({ push: true, at: ts(NOW) }, 540)));
    assert.ok(!M.serviceTextOk({ title: '출발 전날 안내', body: '오사카 숙소 20% 할인 쿠폰' }));
  });
  await t('2년 재확인: 700일에 1번만', async () => {
    const w = world({ users: { u2: { mkt: { push: true, at: ts(NOW - 701 * DAY) }, tz_off: 540 }, u3: { mkt: { push: true, at: ts(NOW - 100 * DAY) }, tz_off: 540 } } });
    assert.strictEqual((await w.core.reconfirm()).sent, 1); assert.strictEqual((await w.core.reconfirm()).sent, 0);
    assert.strictEqual(M.reconfirmDue({ push: true, at: ts(NOW - 701 * DAY) }, { rc_at: NOW }, NOW + 600 * DAY), null);
  });
  console.log(`mkt_fn: ${pass}/${process.exitCode ? 1 : 0}`);
})();
