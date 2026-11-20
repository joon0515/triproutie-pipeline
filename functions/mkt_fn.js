'use strict';
// 292기(11/20): 광고성 정보 푸시 — 정보통신망법 제50조·Apple 4.5.4 를 코드로 지킨다. 실제 발송·배포는 대표(Cloud Shell, 이름 지정 배포).
// 함수 3개
//   mktDispatch    (1시간마다) mkt_campaigns 의 status:'ready' 캠페인을 동의한 사람에게. 동의·빈도·기기 시각·여행 중 맞춤을 사람마다 검사.
//   mktConsentNote (users/{uid}/consents 새 기록) 광고 동의·거부 처리 결과 통지 — 광고 문구 없음, 기록 id 로 한 번만.
//   mktReconfirm   (하루 1번) 동의일(또는 지난 확인일)부터 2년이 되기 30일 전에 수신동의 확인 안내 — 광고 문구 없음, 주기마다 한 번만.
// 저장 자리(앱은 못 씀 — 규칙 v20 후보: mkt_campaigns·mkt_state·mkt_sends·mkt_notices 쓰기 false)
//   users/{uid}.mkt = { push, night, at, via, ver }   ← 앱이 씀(본인 동의). at = 동의를 바꾼 서버 시각.
//   users/{uid}.tz_off = 기기 UTC 차이(분)                  ← 앱이 열릴 때 갱신(동의 시각과 따로 — 도달 시각 계산용)
//   users/{uid}/consents/{id}                                ← 앱이 덧붙임(v11 규칙). items 에 {id:'ad_push'|'ad_night', ok}
//   users/{uid}/fcm_tokens/{id}.fcm_token                     ← p4 tlP4PushSync 와 같은 자리
//   mkt_state/{uid} = { recent:[ms…], rc_at }                  ← 서버만. 빈도 = 최근 보낸 시각 목록(더하기 아님)
//   mkt_sends/{cid}_{uid} · mkt_notices/{uid}_{key}           ← 서버만. create() 로 한 번만(멱등)
// 같은 입력으로 두 번 돌면: 이미 있는 mkt_sends / mkt_notices 문서 때문에 두 번째는 아무것도 보내지 않는다.
// 보내기 순서 = 거래(빈도 검사 + 보냄 표시 + 빈도 목록) 먼저 → 거래 밖에서 FCM. 중간에 죽으면 그 사람은 이번 캠페인을 못 받는다(광고는 '한 번도 안 감'이 '두 번 감'보다 낫다).

const REGION = 'asia-northeast3';
const DAY = 864e5;
const SENDER = '트립루티';
// 빈도·시간 정책(NOTIF_292.md 2절 표와 같은 값)
const POLICY = Object.freeze({
  perDay: 1,          // 하루 절대 상한(기기 날짜 기준이 아니라 최근 24시간)
  perWeek: 1,         // 최근 7일 1건
  perMonth: 3,        // 최근 30일 3건
  dayFrom: 10, dayTo: 20,   // 기기 시각 10:00~19:59 에만(조용한 시간 21~8 에 앞뒤 1시간 여유)
  nightFrom: 21, nightTo: 22, // 야간 동의 + 캠페인 night:true 일 때만 21:00~21:59 추가(그 밖의 밤은 동의가 있어도 안 보냄)
  quietAfterConsentDays: 3,  // 동의 직후 3일은 광고 없음(동의하자마자 광고가 오면 '속았다'는 느낌)
  maxTokens: 5,
  reconfirmEveryDays: 730, reconfirmLeadDays: 30,
});
const TITLE_MAX = 30, BODY_MAX = 90;
const OPT_OUT = '수신거부: 앱 나 탭 > 알림 > 광고 끄기';
const DEAD = new Set(['messaging/registration-token-not-registered', 'messaging/invalid-registration-token', 'messaging/invalid-argument']);

const ms = (t) => (t == null ? null : typeof t === 'number' ? t : t.toMillis ? t.toMillis() : t instanceof Date ? t.getTime() : null);
// 기기 시각(tz_off 분 = DateTime.now().timeZoneOffset, 앱이 열릴 때·동의 바꿀 때 갱신)
function localHour(now, tzOff) { return new Date(now + tzOff * 60e3).getUTCHours(); }
function localDate(now, tzOff) { return new Date(now + tzOff * 60e3).toISOString().slice(0, 10); }

/** 캠페인 글 검사 — 앞에 붙는 '(광고) 트립루티'·끝의 수신거부는 서버가 붙이므로 캠페인 글에 넣지 않는다. */
function campaignOk(c) {
  if (!c || typeof c.title !== 'string' || typeof c.body !== 'string') return 'shape';
  const t = c.title.trim(), b = c.body.trim();
  if (!t || !b || t.length > TITLE_MAX || b.length > BODY_MAX) return 'length';
  if (/\(광고\)|수신거부/.test(t + b)) return 'dup_label';
  if (c.cities != null && (!Array.isArray(c.cities) || c.cities.length > 30)) return 'cities';
  return null;
}

/** 보낼 글. 맨 앞 '(광고)' + 전송자 명칭, 끝에 수신거부 방법. contact 가 있으면 명칭 뒤에(대표 확인 사항). */
function buildAd(c, contact) {
  const who = contact ? `${SENDER}(${contact})` : SENDER;
  return { title: `(광고) ${who} · ${c.title.trim()}`, body: `${c.body.trim()}\n${OPT_OUT}` };
}

/** 지금 이 사람에게 이 캠페인을 보내도 되나 — 이유 문자열(보내면 안 됨) 또는 null(보내도 됨). 'later' = 시간만 안 맞음(다음 시간에 다시). */
function eligible({ user, state, trip, c, now }) {
  const m = (user && user.mkt) || {};
  if (m.push !== true) return 'no_consent';
  const at = ms(m.at);
  if (at == null) return 'no_consent_time';
  if (now - at < POLICY.quietAfterConsentDays * DAY) return 'later';
  const tz = user.tz_off;
  if (typeof tz !== 'number' || Math.abs(tz) > 14 * 60) return 'no_tz'; // 도달 시각을 모르면 보내지 않는다
  const h = localHour(now, tz);
  const dayOk = h >= POLICY.dayFrom && h < POLICY.dayTo;
  const nightOk = m.night === true && c.night === true && h >= POLICY.nightFrom && h < POLICY.nightTo;
  if (!dayOk && !nightOk) return 'later';
  // 여행 중이면 그 여행에 맞는 것만(도시가 캠페인 cities 안). 여행 중 아닌 사람에게 여행지 한정 캠페인은 보내지 않음.
  if (trip) { if (!c.for_trip || !Array.isArray(c.cities) || !c.cities.includes(trip.city)) return 'on_trip_mismatch'; }
  else if (c.for_trip) return 'not_on_trip';
  const r = ((state && state.recent) || []).map(ms).filter((x) => x != null);
  if (r.filter((x) => now - x < DAY).length >= POLICY.perDay) return 'cap_day';
  if (r.filter((x) => now - x < 7 * DAY).length >= POLICY.perWeek) return 'cap_week';
  if (r.filter((x) => now - x < 30 * DAY).length >= POLICY.perMonth) return 'cap_month';
  return null;
}

/** 여행 중인 여행(기기 날짜가 start_date~end_date 안). trips 문서 start_date·end_date 는 Timestamp 또는 'YYYY-MM-DD'. */
function activeTrip(trips, now, tzOff) {
  const today = localDate(now, tzOff || 0);
  const d = (v) => (typeof v === 'string' ? v.slice(0, 10) : ms(v) != null ? localDate(ms(v), tzOff || 0) : null);
  for (const t of trips || []) { const a = d(t.start_date), b = d(t.end_date); if (a && b && a <= today && today <= b) return { city: String(t.city || '') }; }
  return null;
}

function fmtDate(t, tzOff) { const s = localDate(t, tzOff || 540); return `${s.slice(0, 4)}년 ${+s.slice(5, 7)}월 ${+s.slice(8, 10)}일`; }
/** 처리 결과 통지(광고 아님). 전송자·처리 일자·결과·바꾸는 법. */
function consentNote(items, at, tzOff) {
  const it = (id) => (items || []).find((x) => x && x.id === id);
  const p = it('ad_push'), n = it('ad_night');
  if (!p && !n) return null;
  const part = [];
  if (p) part.push(`광고성 정보 수신 ${p.ok ? '동의' : '거부'}`);
  if (n) part.push(`야간(21~8시) 광고성 정보 수신 ${n.ok ? '동의' : '거부'}`);
  return { title: `${SENDER} 수신 설정 처리 결과`, body: `${fmtDate(at, tzOff)} ${part.join(' · ')}가 처리됐어요. 바꾸기: 나 탭 > 알림` };
}
/** 2년 재확인 안내가 필요한가 — 기준 = max(동의 시각, 지난 안내 시각). 주기 번호를 돌려 멱등 열쇠로 쓴다. */
function reconfirmDue(mkt, state, now) {
  if (!mkt || mkt.push !== true) return null;
  const base = Math.max(ms(mkt.at) || 0, ms(state && state.rc_at) || 0);
  if (!base) return null;
  if (now < base + (POLICY.reconfirmEveryDays - POLICY.reconfirmLeadDays) * DAY) return null;
  return { key: 'rc_' + localDate(base, 0) };
}
function reconfirmNote(mkt, tzOff) {
  return { title: `${SENDER} 광고성 정보 수신동의 확인 안내`,
    body: `${fmtDate(ms(mkt.at), tzOff)}에 광고성 정보 수신에 동의하셨어요${mkt.night ? '(야간 포함)' : ''}. 계속 받으시려면 그대로 두시고, 원치 않으면 나 탭 > 알림에서 끌 수 있어요.` };
}

function makeMktCore(deps) {
  const { db, messaging, now, log } = deps;
  async function tokensOf(uid) {
    return (await db.collection(`users/${uid}/fcm_tokens`).limit(POLICY.maxTokens).get()).docs;
  }
  async function pushTo(uid, msg, data) {
    const toks = await tokensOf(uid);
    let sent = 0;
    for (const td of toks) {
      const token = String((td.data() || {}).fcm_token || '');
      if (!token) continue;
      try { await messaging.send({ token, notification: msg, data }); sent++; } catch (e) {
        if (DEAD.has(e && e.code)) { try { await td.ref.delete(); } catch (_) { /* 다음에 다시 */ } }
        log('mkt_send_err', { code: e && e.code });
      }
    }
    return { sent, tokens: toks.length };
  }

  /** 한 사람·한 캠페인. 돌려주는 값 = 결과 이유(시험·로그용). */
  async function sendOne(cid, c, uid, user) {
    const t = now();
    const trips = (await db.collection('trips').where('owner_uid', '==', uid).limit(20).get()).docs.map((d) => d.data());
    const trip = activeTrip(trips, t, user.tz_off);
    const sendRef = db.doc(`mkt_sends/${cid}_${uid}`), stRef = db.doc(`mkt_state/${uid}`);
    const why = await db.runTransaction(async (tx) => {
      const [s, st] = await Promise.all([tx.get(sendRef), tx.get(stRef)]);
      if (s.exists) return 'dup';
      const state = st.exists ? st.data() : {};
      const r = eligible({ user, state, trip, c, now: t });
      if (r) return r;
      const recent = (state.recent || []).map(ms).filter((x) => x != null && t - x < 30 * DAY).concat([t]);
      tx.create(sendRef, { uid, cid, at: t, status: 'claimed' });
      tx.set(stRef, { recent }, { merge: true });
      return null;
    });
    if (why) return why;
    const r = await pushTo(uid, buildAd(c, deps.contact && deps.contact()), { type: 'mkt', cid: String(cid) });
    await sendRef.update({ status: r.sent ? 'sent' : 'no_token', sent: r.sent });
    return r.sent ? 'sent' : 'no_token';
  }

  async function dispatch() {
    const t = now();
    const out = { campaigns: 0, sent: 0, skipped: {} };
    const cs = (await db.collection('mkt_campaigns').where('status', '==', 'ready').limit(5).get()).docs;
    for (const cd of cs) {
      const c = cd.data();
      const bad = campaignOk(c);
      if (bad) { await cd.ref.update({ status: 'rejected', reason: bad }); continue; }
      if (ms(c.send_after) != null && t < ms(c.send_after)) continue;
      if (ms(c.expires_at) == null || t > ms(c.expires_at)) { await cd.ref.update({ status: 'done' }); continue; }
      out.campaigns++;
      let q = db.collection('users').where('mkt.push', '==', true).orderBy('__name__').limit(300), last = null;
      for (;;) {
        const page = (await (last ? q.startAfter(last) : q).get()).docs;
        for (const u of page) {
          const why = await sendOne(cd.id, c, u.id, u.data());
          if (why === 'sent') out.sent++; else out.skipped[why] = (out.skipped[why] || 0) + 1;
        }
        if (page.length < 300) break;
        last = page[page.length - 1];
      }
    }
    log('mkt_dispatch', out);
    return out;
  }

  async function onConsent(uid, cid, d) {
    const note = consentNote(d && d.items, ms(d && d.at) || now(), d && d.tz_off);
    if (!note) return 'not_mkt';
    try { await db.doc(`mkt_notices/${uid}_c_${cid}`).create({ uid, kind: 'consent', at: now() }); } catch (_) { return 'dup'; }
    const r = await pushTo(uid, note, { type: 'mkt_notice' });
    return r.sent ? 'sent' : 'no_token'; // 토큰이 없어도 앱 화면에 처리 결과 한 줄을 이미 보였다(tl_notif292 tlN292ResultLine)
  }

  async function reconfirm() {
    const t = now();
    const cutoff = new Date(t - (POLICY.reconfirmEveryDays - POLICY.reconfirmLeadDays) * DAY);
    const out = { sent: 0, none: 0, skip: 0 };
    let q = db.collection('users').where('mkt.at', '<=', cutoff).orderBy('mkt.at').limit(300), last = null;
    for (;;) {
      const page = (await (last ? q.startAfter(last) : q).get()).docs;
      for (const u of page) {
        const ud = u.data() || {}, m = ud.mkt;
        const stRef = db.doc(`mkt_state/${u.id}`);
        const st = await stRef.get();
        const due = reconfirmDue(m, st.exists ? st.data() : {}, t);
        if (!due) { out.skip++; continue; }
        try { await db.doc(`mkt_notices/${u.id}_${due.key}`).create({ uid: u.id, kind: 'reconfirm', at: t }); } catch (_) { out.skip++; continue; }
        const r = await pushTo(u.id, reconfirmNote(m, ud.tz_off), { type: 'mkt_notice' });
        await stRef.set({ rc_at: t, rc_channel: r.sent ? 'push' : 'app' }, { merge: true }); // 'app' = 앱이 다음에 열릴 때 나 탭에 한 줄(tlN292ReconfirmDue)
        if (r.sent) out.sent++; else out.none++;
      }
      if (page.length < 300) break;
      last = page[page.length - 1];
    }
    log('mkt_reconfirm', out);
    return out;
  }
  return { dispatch, onConsent, reconfirm, sendOne };
}

function makeMktFunctions({ region = REGION } = {}) {
  const { onSchedule } = require('firebase-functions/v2/scheduler');
  const { onDocumentCreated } = require('firebase-functions/v2/firestore');
  const { defineString } = require('firebase-functions/params');
  const { logger } = require('firebase-functions');
  const { getFirestore } = require('firebase-admin/firestore');
  const { getMessaging } = require('firebase-admin/messaging');
  const CONTACT = defineString('MKT_SENDER_CONTACT', { default: '' });
  let core;
  const C = () => core || (core = makeMktCore({ db: getFirestore(), messaging: { send: (m) => getMessaging().send(m) }, now: () => Date.now(),
    contact: () => CONTACT.value(), log: (n, o) => logger.info(n, o) }));
  const base = { region, maxInstances: 1, timeoutSeconds: 540, memory: '256MiB' };
  return {
    mktDispatch: onSchedule({ ...base, schedule: 'every 60 minutes', timeZone: 'Asia/Seoul' }, async () => { await C().dispatch(); }),
    mktReconfirm: onSchedule({ ...base, schedule: '17 11 * * *', timeZone: 'Asia/Seoul' }, async () => { await C().reconfirm(); }),
    mktConsentNote: onDocumentCreated({ region, document: 'users/{uid}/consents/{cid}', maxInstances: 2 }, async (ev) => {
      await C().onConsent(ev.params.uid, ev.params.cid, ev.data && ev.data.data());
    }),
  };
}

module.exports = { makeMktFunctions, makeMktCore, eligible, campaignOk, buildAd, activeTrip, consentNote, reconfirmDue, reconfirmNote, POLICY, OPT_OUT };
