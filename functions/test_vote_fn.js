'use strict';
// 262기 vote_fn 시험(네트워크·에뮬레이터 없음, 가짜 db). node test_vote_fn.js
const assert = require('assert');
const V = require('./vote_fn');
const UA = (k) => (k === 'user-agent' ? 'Mozilla/5.0 (iPhone)' : k === 'x-forwarded-for' ? '1.2.3.4, 9.9.9.9' : '');
let pass = 0;
const ok = (name, f) => Promise.resolve().then(f).then(() => { pass++; }, (e) => { console.error('FAIL', name, e.message); process.exitCode = 1; });

const NOW = Date.parse('2026-11-20T01:00:00Z');
const TOK = 'AbCdEfGhIjKlMnOpQrStUv';
const link = () => ({ trip: 'T', by: 'U', on: true, title: '오사카 <3박>', city: '오사카', img: 'https://images.unsplash.com/x', exp: new Date(NOW + 86400e3), nb: 0,
  places: [{ id: 'p1', n: '오사카성', d: 1 }, { id: 'p2', n: '도톤보리', d: 2 }], polls: [{ id: 'v1', q: '저녁', o: [{ id: 'o1', l: '스시' }, { id: 'o2', l: '라멘' }] }], memo: '비밀' });

function fakeDb(store) {
  const doc = (path) => ({
    path,
    collection: (c) => ({ doc: (id) => doc(`${path}/${c}/${id}`) }),
    get: async () => ({ exists: path in store, data: () => store[path] }),
  });
  return {
    collection: (c) => ({ doc: (id) => doc(`${c}/${id}`) }),
    runTransaction: async (fn) => {
      const w = [];
      const r = await fn({ get: (ref) => ref.get(), set: (ref, d) => w.push(['set', ref.path, d]), update: (ref, d) => w.push(['upd', ref.path, d]) });
      for (const [k, p, d] of w) store[p] = k === 'set' ? d : { ...store[p], ...d };
      return r;
    },
  };
}
function res() {
  const r = { code: 0, body: null, h: {}, t: '' };
  r.status = (c) => { r.code = c; return r; };
  r.set = (k, v) => { r.h[k] = v; return r; };
  r.type = (t) => { r.t = t; return r; };
  r.send = (b) => { r.body = b; return r; };
  r.json = (b) => { r.body = b; return r; };
  return r;
}

(async () => {
  await ok('표 검사', () => {
    const l = link();
    assert.deepStrictEqual(V.checkBallot(l, { bid: 'x'.repeat(20), v: { p1: 1, p2: -1 }, p: { v1: 'o2' }, nick: ' 민지 ' }, NOW), { ok: true, ballot: { v: { p1: 1, p2: -1 }, p: { v1: 'o2' }, nick: '민지' } });
    assert.strictEqual(V.checkBallot(l, { bid: 'short', v: { p1: 1 } }, NOW).reason, 'bid');
    assert.strictEqual(V.checkBallot(l, { bid: 'x'.repeat(20), v: { p9: 1 } }, NOW).reason, 'v');
    assert.strictEqual(V.checkBallot(l, { bid: 'x'.repeat(20), v: { p1: 5 } }, NOW).reason, 'v');
    assert.strictEqual(V.checkBallot(l, { bid: 'x'.repeat(20), p: { v1: 'o9' } }, NOW).reason, 'p');
    assert.strictEqual(V.checkBallot(l, { bid: 'x'.repeat(20) }, NOW).reason, 'empty');
    assert.strictEqual(V.checkBallot(l, { bid: 'x'.repeat(20), v: { p1: 1 }, nick: 'http://spam' }, NOW).reason, 'nick');
    assert.strictEqual(V.checkBallot({ ...l, on: false }, { bid: 'x'.repeat(20), v: { p1: 1 } }, NOW).reason, 'closed');
    assert.strictEqual(V.checkBallot({ ...l, exp: new Date(NOW - 1) }, { bid: 'x'.repeat(20), v: { p1: 1 } }, NOW).reason, 'closed');
  });
  await ok('쪽: 이스케이프·og·내보내는 칸만·CSP', async () => {
    const store = { [`vote_links/${TOK}`]: link() };
    const h = V.makeHandlers(() => fakeDb(store), () => NOW);
    const r = res();
    await h.page({ method: 'GET', query: { t: TOK }, get: () => 'ja,en' }, r);
    assert.strictEqual(r.code, 200);
    assert.ok(r.body.includes('오사카 &lt;3박&gt;'));
    assert.ok(r.body.includes('og:image" content="https://images.unsplash.com/x"'));
    assert.ok(r.body.includes('いいね'));
    assert.ok(!r.body.includes('비밀') && !r.body.includes('"U"'));
    assert.ok(/script-src 'nonce-/.test(r.h['Content-Security-Policy']));
    const r2 = res();
    await h.page({ method: 'GET', query: { t: 'bad' }, get: () => '' }, r2);
    assert.strictEqual(r2.code, 404);
    store[`vote_links/${TOK}`].on = false;
    const r3 = res();
    await h.page({ method: 'GET', query: { t: TOK }, get: () => 'ko' }, r3);
    assert.strictEqual(r3.code, 410);
    assert.ok(r3.body.includes('닫혔어요'));
  });
  await ok('표 쓰기: 한 기기 한 문서·덮어쓰기·인원 상한', async () => {
    const store = { [`vote_links/${TOK}`]: link() };
    const h = V.makeHandlers(() => fakeDb(store), () => NOW);
    const bid = 'B'.repeat(20);
    let r = res();
    await h.cast({ method: 'POST', get: UA, body: { t: TOK, bid, v: { p1: 1 } } }, r);
    assert.deepStrictEqual([r.code, r.body], [200, { ok: true }]);
    r = res();
    await h.cast({ method: 'POST', get: UA, body: { t: TOK, bid, v: { p1: -1 } } }, r);
    assert.strictEqual(store[`vote_links/${TOK}/ballots/${bid}`].v.p1, -1);
    assert.strictEqual(store[`vote_links/${TOK}`].nb, 1);
    store[`vote_links/${TOK}`].nb = V.MAX_BALLOTS;
    r = res();
    await h.cast({ method: 'POST', get: UA, body: { t: TOK, bid: 'C'.repeat(20), v: { p1: 1 } } }, r);
    assert.deepStrictEqual([r.code, r.body.reason], [400, 'full']);
    r = res();
    await h.cast({ method: 'GET', get: UA, body: {} }, r);
    assert.strictEqual(r.code, 405);
  });
  await ok('언어 고르기', () => {
    assert.strictEqual(V.pickLang('zh-TW,zh;q=0.9'), 'zt');
    assert.strictEqual(V.pickLang('en-US,ko;q=0.8'), 'en');
    assert.strictEqual(V.pickLang('fr'), 'en');
  });
  await ok('276 웹 면: 보안 머리글·봇 거름·같은 주소 몰아넣기 막기', async () => {
    const store = { [`vote_links/${TOK}`]: link() };
    const h = V.makeHandlers(() => fakeDb(store), () => NOW);
    let r = res();
    await h.page({ method: 'GET', query: { t: TOK }, get: () => 'ko' }, r);
    for (const k of ['Strict-Transport-Security', 'X-Frame-Options', 'X-Robots-Tag', 'Permissions-Policy', 'Content-Security-Policy']) assert.ok(r.h[k], k);
    assert.ok(/noai/.test(r.h['X-Robots-Tag']));
    r = res();
    await h.cast({ method: 'POST', get: () => '', body: { t: TOK, bid: 'D'.repeat(20), v: { p1: 1 } } }, r);
    assert.deepStrictEqual([r.code, r.body.reason], [400, 'client']);
    assert.ok(r.h['Strict-Transport-Security']);
    let last = 0;
    for (let i = 0; i < V.IP_LIMIT.newPerLink + 1; i++) { r = res(); await h.cast({ method: 'POST', get: UA, body: { t: TOK, bid: 'E' + String(i).padStart(19, '0'), v: { p1: 1 } } }, r); last = r.code; }
    assert.strictEqual(last, 429);
    const xff = (ip) => (k) => (k === 'user-agent' ? 'Mozilla/5.0' : k === 'x-forwarded-for' ? ip : '');
    assert.strictEqual(V.clientKey({ get: xff('6.6.6.6, 1.1.1.1') }), V.clientKey({ get: xff('7.7.7.7, 1.1.1.1') }), '앞쪽 꾸민 주소는 무시');
    const g = V.makeIpGate(() => NOW, { windowMs: 1000, casts: 2, newPerLink: 9, memMax: 10 });
    assert.deepStrictEqual([g.hit('k', 'a', false), g.hit('k', 'a', false), g.hit('k', 'a', false)], ['ok', 'ok', 'rate']);
  });
  console.log(`vote_fn ${pass}/5 통과`);
})();
