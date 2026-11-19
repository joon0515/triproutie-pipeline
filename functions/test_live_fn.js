'use strict';
// node test_live_fn.js — 외부 호출 0(가짜 Firestore·fetch·인증). 가짜 키만 씀.
const L = require('./live_fn');
let pass = 0, fail = 0;
const ok = (c, m) => { if (c) pass++; else { fail++; console.log('FAIL', m); } };

function fakeDb(init = {}) {
  const store = JSON.parse(JSON.stringify(init));
  const snap = (p) => ({ exists: Object.prototype.hasOwnProperty.call(store, p), data: () => store[p] });
  const set = (p, v, o) => { store[p] = o && o.merge ? { ...(store[p] || {}), ...v } : { ...v }; };
  const doc = (p) => ({ path: p, get: async () => snap(p) });
  return { store, doc, runTransaction: async (fn) => fn({ get: async (r) => snap(r.path), set: (r, v, o) => set(r.path, v, o) }) };
}
const T = Date.parse('2026-11-20T03:00:00Z'); // 태평양 2026-11-19
const day = '20261119', month = '202611';
function core(db, { fetchLog = [], places = {}, user = 'u1' } = {}) {
  return L.makeLiveCore({
    verify: async (req) => { if (!req.get('authorization')) throw Object.assign(new Error('auth'), { reason: 'auth' }); return { uid: user }; },
    db, now: () => T, key: () => 'FAKE_KEY', log: () => {},
    fetch: async (url, init) => {
      fetchLog.push({ url, mask: init.headers['X-Goog-FieldMask'] });
      if (url.includes('/media?')) return { ok: true, status: 200, json: async () => ({ photoUri: 'https://lh3.googleusercontent.com/p/x' }) };
      return { ok: true, status: 200, json: async () => places };
    },
  });
}
const req = (body, auth = true) => ({ method: 'POST', body, get: (h) => (h === 'authorization' && auth ? 'Bearer t' : '') });
const PL = { regularOpeningHours: { weekdayDescriptions: ['Mon: 9-18', 'Tue: 9-18'] }, websiteUri: 'https://a.example', nationalPhoneNumber: '02-000-0000', rating: 4.4, userRatingCount: 12,
  photos: [{ name: 'places/ChIJabcdefghij/photos/AXyz123', authorAttributions: [{ displayName: 'Kim' }] }] };

(async () => {
  // 검사
  ok(L.vLive({ id: 'ChIJabcdefghij', fields: ['rating', 'reviews', 'editorialSummary'] }).v.fields.join() === 'rating', '허용 목록 밖 칸은 뺌');
  ok(L.vLive({ id: 'ChIJabcdefghij', fields: ['reviews'] }).bad === 'fields', '허용 칸 없으면 거절');
  ok(L.vLive({ id: '../x', fields: ['rating'] }).bad === 'id', 'id 경로 주입 막음');
  ok(L.vLive({ id: 'ChIJabcdefghij', fields: ['rating'], lang: 'zh-Hant' }).v.lang === 'zh-Hant', 'lang');
  ok(L.pacificParts(T).day === day && L.pacificParts(T).month === month, '태평양 날짜');
  ok(L.FREE_MONTHLY.details_enterprise === 1000 && L.FREE_MONTHLY.details_photos === 1000, '무료 한도 상수');
  ok(L.DAILY_FREE.detailsRich * 31 < 1000 && L.DAILY_FREE.livePhotos * 31 < 1000, '하루 몫 × 31 < 월 무료');
  ok(L.judge('details_enterprise', { detailsRich: 28 }, 0, 0).ok, '무료 몫 안');
  ok(!L.judge('details_enterprise', { detailsRich: 29 }, 0, 0).ok, '무료 몫 끝 + 추가 예산 0 → 거절');
  ok(L.judge('details_enterprise', { detailsRich: 29 }, 1, 0.98).ok && !L.judge('details_enterprise', { detailsRich: 29 }, 1, 0.99).ok, '추가 예산 안에서만');

  // 인증·메서드
  let db = fakeDb(), c = core(db);
  ok((await c.details(req({ id: 'ChIJabcdefghij', fields: ['rating'] }, false))).status === 401, '토큰 없음 401');
  ok((await c.budget({ method: 'GET', body: {}, get: () => 'Bearer t' })).status === 405, 'GET 405');

  // 정상 호출: 마스크 = 요청 칸만, phone·photos·작성자 표기
  const log = []; db = fakeDb(); c = core(db, { fetchLog: log, places: PL });
  let r = await c.details(req({ id: 'ChIJabcdefghij', fields: ['regularOpeningHours', 'nationalPhoneNumber', 'photos'], lang: 'ko' }));
  ok(r.status === 200 && r.body.ok, '정상 200');
  ok(log[0].mask === 'regularOpeningHours,nationalPhoneNumber,photos', '필드 마스크 = 요청 칸만: ' + log[0].mask);
  ok(r.body.place.phone === '02-000-0000', 'phone');
  ok(r.body.place.hours.weekdayText.length === 2, 'hours');
  ok(r.body.place.website === undefined, '요청 안 한 칸 없음');
  ok(r.body.place.photos[0].url.startsWith('https://') && r.body.place.photos[0].attributions[0] === 'Kim', 'photos + 작성자');
  ok(log[1].url.includes('/places/ChIJabcdefghij/photos/AXyz123/media?maxWidthPx=800&skipHttpRedirect=true'), '사진 1장만 media');
  ok(db.store[`_quota/google_${day}`].detailsRich === 1 && db.store[`_quota/google_${day}`].livePhotos === 1, '기존 proxy 하루 문서와 같은 칸에 셈');
  ok(db.store[`_quota/live_m_${month}`].details_enterprise === 1 && db.store[`_quota/live_m_${month}`].details_photos === 1, '월 합계');
  ok(JSON.stringify(db.store).indexOf('02-000-0000') < 0 && JSON.stringify(db.store).indexOf('googleusercontent') < 0, 'Firestore 에 내용 저장 없음(횟수만)');

  // 사진만 요청 → Enterprise 예약 없음
  const log2 = []; db = fakeDb(); c = core(db, { fetchLog: log2, places: PL });
  r = await c.details(req({ id: 'ChIJabcdefghij', fields: ['photos'] }));
  ok(r.body.ok && !(db.store[`_quota/google_${day}`] || {}).detailsRich, '사진만이면 Enterprise 안 셈');

  // 무료 몫 다 씀 + 추가 예산 0 → quota, 구글 안 부름
  const log3 = []; db = fakeDb({ [`_quota/google_${day}`]: { detailsRich: 29 } }); c = core(db, { fetchLog: log3, places: PL });
  r = await c.details(req({ id: 'ChIJabcdefghij', fields: ['rating'] }));
  ok(r.body.ok === false && r.body.reason === 'quota' && log3.length === 0, '무료 끝 → quota·구글 미호출');
  // 원격 추가 예산이 있으면 통과·지출 기록
  db = fakeDb({ [`_quota/google_${day}`]: { detailsRich: 29 }, 'config/live_budget': { google_extra_daily_usd: 1 } }); c = core(db, { places: PL });
  r = await c.details(req({ id: 'ChIJabcdefghij', fields: ['rating'] }));
  ok(r.body.ok && Math.abs(db.store[`_quota/live_d_${day}`].extra_usd - 0.02) < 1e-9, '추가 예산 지출 0.02');
  // 사용자 하루 상한
  db = fakeDb({ '_quota/lu_u1': { d: day, n: L.USER_PER_DAY } }); c = core(db, { places: PL });
  r = await c.details(req({ id: 'ChIJabcdefghij', fields: ['rating'] }));
  ok(r.body.reason === 'rate', '사용자 하루 상한');

  // liveBudget
  db = fakeDb({ [`_quota/live_m_${month}`]: { details_enterprise: 5, details_photos: 2 }, [`_quota/google_${day}`]: { detailsRich: 3 } }); c = core(db);
  r = await c.budget(req({}));
  ok(r.body.ok && r.body.month.details_enterprise === 5 && r.body.month.details_photos === 2 && r.body.extra_daily_usd === 0 && r.body.today.details_rich === 3, 'liveBudget 모양: ' + JSON.stringify(r.body));

  console.log(`live_fn: ${pass}/${fail}`);
  process.exit(fail ? 1 : 0);
})();
