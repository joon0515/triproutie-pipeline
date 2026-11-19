'use strict';
// 252기(11/20): 장소 상세 실시간 공식 API — liveBudget(예산 조회) · placeDetailsLive(필드 허용 목록 + phone·photos).
// index.js 끝 한 줄: Object.assign(exports, require('./live_fn').makeLiveFunctions());
//
// ★ 이름을 placeDetails 로 하지 않은 까닭: 지금 배포된 placeDetails·g 는 Cloud Shell ~/tr 원본(이용권·게스트 묶음 등)이다.
//   이 저장소에서 같은 이름으로 배포하면 그 함수를 덮어쓴다. 그래서 새 이름 placeDetailsLive 로 따로 둔다(기존 함수 불변).
//
// 정책(Google Maps Platform, 2026-11-18 확인 — docs/PLACE_DATA_PLAN.md 5절):
//  · 결과를 저장하지 않는다(Firestore 에는 횟수만). 응답에 cache-control: no-store.
//  · 지도 없는 상세 시트에서만 보인다(앱 tlLiveGoogleDisplayOk). 사진은 작성자 표기(authorAttributions)를 함께 돌려준다.
// 비용(공식 가격표): Place Details Enterprise 월 무료 1,000 · Place Details Photos 월 무료 1,000.
//  · 기본 = 무료 안에서만: 하루 상한 = 기존 proxy 와 같은 _quota/google_<태평양 날짜>.detailsRich(29/일 × 31 = 899 < 1,000)를 함께 쓴다.
//    사진은 livePhotos 29/일. 이번 달 합계도 _quota/live_m_<YYYYMM> 에 센다.
//  · 하루 추가 예산(원격 설정 config/live_budget.google_extra_daily_usd, 기본 0)이 있으면 무료 몫을 넘긴 호출을 그 금액 안에서만.
// 키는 Secret GOOGLE_MAPS_KEY(이름만). 값은 요청 머리에만 쓰고 로그·응답·저장 어디에도 안 나감.
const { makeVerify } = require('./google');

const REGION = 'asia-northeast3';
const SECRET_NAME = 'GOOGLE_MAPS_KEY';
const PLACES = 'https://places.googleapis.com/v1';
const FREE_MONTHLY = Object.freeze({ details_enterprise: 1000, details_photos: 1000 });
const USD_PER_CALL = Object.freeze({ details_enterprise: 20 / 1000, details_photos: 7 / 1000 });
const DAILY_FREE = Object.freeze({ detailsRich: 29, livePhotos: 29 }); // 무료 월 한도 ÷ 31일 × 0.9
const USER_PER_DAY = 15; // 앱 kTlPd252DailyCap 과 같음
const USER_PHOTOS_PER_DAY = 15; // 268기: 사진 SKU 도 사용자별 상한(없으면 한 사람이 사진만 요청해 하루 무료 몫 29·추가 예산을 다 씀)
// 필드 허용 목록 — 이 밖의 칸은 요청하지 않는다(요금 등급을 올리지 않게). 모두 Place Details Enterprise 이하.
const ALLOWED = Object.freeze(['regularOpeningHours', 'websiteUri', 'nationalPhoneNumber', 'rating', 'userRatingCount', 'photos']);
const ENTERPRISE = new Set(['regularOpeningHours', 'websiteUri', 'nationalPhoneNumber', 'rating', 'userRatingCount']);

const isObj = (x) => x !== null && typeof x === 'object' && !Array.isArray(x);
const str = (v) => (typeof v === 'string' ? v : '');

function pacificParts(ms) {
  const p = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Los_Angeles', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(ms));
  const g = (t) => p.find((x) => x.type === t).value;
  return { day: `${g('year')}${g('month')}${g('day')}`, month: `${g('year')}${g('month')}` };
}

/** 본문 검사 → {v} 또는 {bad}. fields 는 허용 목록과의 교집합(빈 칸이면 bad). */
function vLive(b) {
  if (!isObj(b)) return { bad: 'body' };
  if (typeof b.id !== 'string' || !/^[A-Za-z0-9_-]{10,300}$/.test(b.id)) return { bad: 'id' };
  const lang = b.lang === undefined || b.lang === null ? 'en' : b.lang;
  if (typeof lang !== 'string' || !/^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$/.test(lang)) return { bad: 'lang' };
  if (!Array.isArray(b.fields)) return { bad: 'fields' };
  const fields = [...new Set(b.fields.filter((f) => ALLOWED.includes(f)))];
  if (!fields.length) return { bad: 'fields' };
  return { v: { id: b.id, lang, fields } };
}

/** 예산 판정(순수). q = 오늘 문서, extraUsd = 원격 하루 추가 예산, spent = 오늘 추가 지출. → {ok, paid} */
function judge(sku, q, extraUsd, spent) {
  const key = sku === 'details_enterprise' ? 'detailsRich' : 'livePhotos';
  const used = Number.isInteger(q[key]) && q[key] > 0 ? q[key] : 0;
  if (used < DAILY_FREE[key]) return { ok: true, paid: 0, key };
  const cost = USD_PER_CALL[sku];
  if (extraUsd > 0 && spent + cost <= extraUsd + 1e-9) return { ok: true, paid: cost, key };
  return { ok: false, paid: 0, key };
}

/** Places 응답 → 앱 모양(tl_pd252 tlPd252FromGoogle 이 읽는 칸). 값만, 원문 길이 제한. */
function shape(j, fields = ALLOWED) {
  j = Object.fromEntries(Object.entries(j).filter(([k]) => fields.includes(k))); // 요청한 칸만 돌려준다
  const h = isObj(j.regularOpeningHours) ? j.regularOpeningHours : null;
  const out = {};
  if (h && Array.isArray(h.weekdayDescriptions)) out.hours = { weekdayText: h.weekdayDescriptions.filter((s) => typeof s === 'string').slice(0, 7).map((s) => s.slice(0, 120)) };
  if (/^https?:\/\//i.test(str(j.websiteUri))) out.website = j.websiteUri.slice(0, 500);
  if (str(j.nationalPhoneNumber)) out.phone = j.nationalPhoneNumber.slice(0, 40);
  if (typeof j.rating === 'number') out.rating = j.rating;
  if (Number.isInteger(j.userRatingCount)) out.ratingCount = j.userRatingCount;
  return out;
}

function photoName(j) {
  const p = Array.isArray(j.photos) ? j.photos.find((x) => isObj(x) && /^places\/[A-Za-z0-9_-]+\/photos\/[A-Za-z0-9_-]+$/.test(str(x.name))) : null;
  if (!p) return null;
  const at = Array.isArray(p.authorAttributions) ? p.authorAttributions.filter(isObj).map((a) => str(a.displayName).slice(0, 80)).filter(Boolean).slice(0, 3) : [];
  return { name: p.name, attributions: at };
}

/** deps: {verify, db, fetch, key()→string, now()→ms, log} — 시험에서 모두 바꿔 끼운다. */
function makeLiveCore(deps) {
  const docs = (ms) => {
    const { day, month } = pacificParts(ms);
    return { q: deps.db.doc(`_quota/google_${day}`), m: deps.db.doc(`_quota/live_m_${month}`), d: deps.db.doc(`_quota/live_d_${day}`), cfg: deps.db.doc('config/live_budget') };
  };
  async function auth(req) {
    let user;
    try { user = await deps.verify(req); } catch (e) { return { err: { status: 401, body: { ok: false, reason: e && e.reason === 'appcheck' ? 'appcheck' : 'auth' } } }; }
    if (!user || typeof user.uid !== 'string' || !user.uid) return { err: { status: 401, body: { ok: false, reason: 'auth' } } };
    if (req.method !== 'POST') return { err: { status: 405, body: { ok: false, reason: 'method' } } };
    return { user };
  }
  const num = (x) => (typeof x === 'number' && isFinite(x) && x > 0 ? x : 0);
  const int = (x) => (Number.isInteger(x) && x > 0 ? x : 0);

  async function budget(req) {
    const a = await auth(req); if (a.err) return a.err;
    const D = docs(deps.now());
    const [q, m, d, c] = await Promise.all([D.q.get(), D.m.get(), D.d.get(), D.cfg.get()]);
    const Q = (q.exists && q.data()) || {}, M = (m.exists && m.data()) || {}, Dd = (d.exists && d.data()) || {}, C = (c.exists && c.data()) || {};
    return { status: 200, body: { ok: true,
      month: { details_enterprise: int(M.details_enterprise), details_photos: int(M.details_photos) },
      today: { details_rich: int(Q.detailsRich), photos: int(Q.livePhotos), free_daily: DAILY_FREE },
      free_monthly: FREE_MONTHLY, today_extra_usd: num(Dd.extra_usd), extra_daily_usd: num(C.google_extra_daily_usd) } };
  }

  /** 트랜잭션 안에서 SKU 하나 예약. 'ok' | 'quota' | 'user'. */
  async function reserve(uid, sku, ms) {
    const D = docs(ms), uref = deps.db.doc(`_quota/lu_${uid}`), { day } = pacificParts(ms);
    return deps.db.runTransaction(async (tx) => {
      const [q, m, d, c, u] = await Promise.all([tx.get(D.q), tx.get(D.m), tx.get(D.d), tx.get(D.cfg), tx.get(uref)]);
      const Q = (q.exists && q.data()) || {}, M = (m.exists && m.data()) || {}, Dd = (d.exists && d.data()) || {}, C = (c.exists && c.data()) || {}, U = (u.exists && u.data()) || {};
      const un = U.d === day ? int(U.n) : 0, up = U.d === day ? int(U.p) : 0;
      if (sku === 'details_enterprise' && un >= USER_PER_DAY) return 'user';
      if (sku === 'details_photos' && up >= USER_PHOTOS_PER_DAY) return 'user';
      const j = judge(sku, Q, num(C.google_extra_daily_usd), num(Dd.extra_usd));
      if (!j.ok) return 'quota';
      tx.set(D.q, { [j.key]: int(Q[j.key]) + 1, ...(sku === 'details_enterprise' ? { details: int(Q.details) + 1 } : {}) }, { merge: true });
      tx.set(D.m, { [sku]: int(M[sku]) + 1 }, { merge: true });
      if (j.paid) tx.set(D.d, { extra_usd: Math.round((num(Dd.extra_usd) + j.paid) * 1e6) / 1e6 }, { merge: true });
      tx.set(uref, sku === 'details_enterprise' ? { d: day, n: un + 1, p: up } : { d: day, n: un, p: up + 1 }, { merge: true });
      return 'ok';
    });
  }

  async function details(req) {
    const a = await auth(req); if (a.err) return a.err;
    const vv = vLive(req.body); if (vv.bad) return { status: 400, body: { ok: false, reason: 'input', field: vv.bad } };
    const v = vv.v, wantPhoto = v.fields.includes('photos');
    const ent = v.fields.filter((f) => ENTERPRISE.has(f));
    const mask = [...ent, ...(wantPhoto ? ['photos'] : [])];
    // Enterprise 칸이 없고 사진만이면 Details 는 IDs Only 등급(무료 무제한) — 예약은 사진 SKU 만.
    if (ent.length) {
      const r = await reserve(a.user.uid, 'details_enterprise', deps.now());
      if (r !== 'ok') return { status: 200, body: { ok: false, reason: r === 'user' ? 'rate' : 'quota' } };
    }
    const headers = { 'X-Goog-Api-Key': deps.key(), 'X-Goog-FieldMask': mask.join(','), accept: 'application/json' };
    let res;
    try { res = await deps.fetch(`${PLACES}/places/${encodeURIComponent(v.id)}?languageCode=${encodeURIComponent(v.lang)}`, { method: 'GET', headers }); } catch (_) { return { status: 502, body: { ok: false, reason: 'upstream' } }; }
    if (res.status === 404) return { status: 200, body: { ok: false, reason: 'not_found' } };
    if (res.status === 429) return { status: 200, body: { ok: false, reason: 'quota' } };
    if (!res.ok) { deps.log('live_upstream', { status: res.status }); return { status: 502, body: { ok: false, reason: 'upstream', status: res.status } }; }
    const j = await res.json();
    const place = shape(isObj(j) ? j : {}, v.fields);
    if (wantPhoto) {
      const p = photoName(isObj(j) ? j : {});
      if (p && (await reserve(a.user.uid, 'details_photos', deps.now())) === 'ok') {
        try {
          const r2 = await deps.fetch(`${PLACES}/${p.name}/media?maxWidthPx=800&skipHttpRedirect=true`, { method: 'GET', headers: { 'X-Goog-Api-Key': deps.key(), accept: 'application/json' } });
          const pj = r2.ok ? await r2.json() : null;
          if (isObj(pj) && /^https:\/\//.test(str(pj.photoUri))) place.photos = [{ url: pj.photoUri, attributions: p.attributions }];
        } catch (_) { /* 사진만 빠짐 */ }
      }
    }
    return { status: 200, body: { ok: true, place } };
  }
  return { budget, details };
}

function makeLiveFunctions({ region = REGION } = {}) {
  const { onRequest } = require('firebase-functions/v2/https');
  const { defineSecret, defineBoolean } = require('firebase-functions/params');
  const { logger } = require('firebase-functions');
  const { getFirestore } = require('firebase-admin/firestore');
  const KEY = defineSecret(SECRET_NAME);
  const REQUIRE_APP_CHECK = defineBoolean('REQUIRE_APP_CHECK', { default: false });
  const core = makeLiveCore({
    verify: makeVerify(REQUIRE_APP_CHECK), get db() { return getFirestore(); },
    fetch: (u, i) => fetch(u, i), key: () => KEY.value(), now: () => Date.now(), log: (n, d) => logger.warn(n, d),
  });
  const opts = { region, maxInstances: 3, concurrency: 40, timeoutSeconds: 15, memory: '256MiB', cors: true };
  const wrap = (fn) => async (req, res) => { res.set('cache-control', 'no-store'); try { const r = await fn(req); res.status(r.status).json(r.body); } catch (_) { res.status(500).json({ ok: false, reason: 'server' }); } };
  return { liveBudget: onRequest(opts, wrap(core.budget)), placeDetailsLive: onRequest({ ...opts, secrets: [KEY] }, wrap(core.details)) };
}

module.exports = { makeLiveFunctions, makeLiveCore, vLive, judge, shape, photoName, pacificParts, ALLOWED, FREE_MONTHLY, DAILY_FREE, USER_PER_DAY, USER_PHOTOS_PER_DAY };
