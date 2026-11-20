'use strict';
// 292기(11/20, 대표 정정 반영): 광고성 정보 푸시 — 동의한 사람에게 '거부감 직전'까지, 적응형 상한. 정보통신망법 제50조·Apple 4.5.4 는 그대로.
// 함수 6개(배포 = 대표, 이름 지정)
//   mktDispatch    (1시간마다) status:'ready' 캠페인을 동의자에게. 동의·기기 시각·간격·적응형 상한(묶음 단계 − 개인 무시 감점)·여행 중 맞춤.
//   mktConsentNote (consents 새 기록) 광고 동의·거부 처리 결과 통지(광고 문구 없음) + 지표 사건(optout·optin).
//   mktReconfirm   (매일) 동의일·지난 안내일부터 700일에 수신동의 확인 안내(광고 없음).
//   mktOpen        (users/{uid}/mkt_opens/{cid} 새 기록 = 앱이 광고 알림을 눌러 열었음, '{cid}_k' = 클릭) 열람·클릭 사건·개인 무시 횟수 0.
//   mktPermWatch   (users/{uid} 바뀜) perm_on true→false = 알림 끄기 사건.
//   mktMetrics     (매일 04:07 KST) ① 48시간 안 연 광고 = 무시(개인 감점) ② 어제 묶음·슬롯·문구별 지표 → mkt_metrics ③ 묶음 단계 자동 조절 → mkt_level ④ 14일마다 슬롯 재선정 → mkt_slots/current.
// 저장 자리(앱 쓰기 0, 규칙 v20 후보): mkt_campaigns · mkt_state/{uid} · mkt_sends/{cid}_{uid} · mkt_notices · mkt_ev/{사건 id} · mkt_metrics/{날짜}_{묶음} · mkt_level/{묶음}
// 앱이 쓰는 것: users/{uid}.mkt{push,night,at,via,ver} · tz_off · perm_on · users/{uid}/consents · fcm_tokens · mkt_opens/{cid}{at}
// 멱등: 보냄 = mkt_sends create(거래 안에서 상한 검사와 함께) · 사건 = mkt_ev 문서 id 고정 create · 무시 = status 'sent'→'ignored' 바꾸기와 감점을 한 거래 · 지표 = 날짜 문서 덮어쓰기.
// 광고를 거부한 사람에게는 이 파일이 아무것도 보내지 않는다(서비스 알림은 앱 로컬 알림 — tl_tourj 등). 이 파일이 보내는 서비스 통지(처리 결과·재확인)는 serviceTextOk 로 광고 낱말을 막는다.

const REGION = 'asia-northeast3';
const H = 36e5, DAY = 864e5;
const SENDER = '트립루티';
// 단계표(NOTIF_292.md 2절과 같은 값). 대표 확정 11/20: 시작 = 하루 3(슬롯 3개). 위 = 하루 4. 거부·삭제가 오르면 자동으로 내림.
const LEVELS = Object.freeze([
  { day: 1, week: 7 },
  { day: 2, week: 14 },
  { day: 3, week: 21 },
  { day: 4, week: 28 },
]);
const START = 2;
// 보내는 슬롯(기기 시각의 '시'). 공개 자료 근거(NOTIF_292.md 2절 출처): 여행 10~14시·17~19시(CleverTap), 점심 12~14시·저녁 18:30~20:30(Batch),
// 커머스 11~13시·17~20시(CleverTap), 아시아·태평양 주말은 오전이 가장 높음(Leanplum/EyeforTravel). 앞에서부터 단계의 하루 건수만큼 쓴다.
const SLOTS_DEFAULT = Object.freeze({ wk: [12, 18, 10, 15], we: [10, 12, 18, 15] });
const SLOT_POOL = Object.freeze([8, 10, 12, 15, 18, 19, 20]); // 재선정 후보(08시 = 출근길, 법상 낮). 21·22 = 야간 동의자 전용(아래)
const NIGHT_SLOTS = Object.freeze([21, 22]);
const EXPLORE = Object.freeze({ every: 10, reselectDays: 14, minSent: 300, w: { optout: 5, uninstall: 5, perm_off: 3 } });
const POLICY = Object.freeze({
  dayFrom: 8, dayTo: 21,        // 기기 시각 08:00~20:59 안에서 슬롯만(21시~다음 날 8시 = 야간, 야간 동의 필요)
  nightTo: 23,                  // 야간 동의 + 캠페인 night:true 면 21:00~22:59 추가. 23시~8시는 동의가 있어도 안 보냄
  gapH: 2,                      // 광고 사이 최소 2시간(슬롯 10·12시 사이)
  quietAfterConsentH: 24,       // 동의 직후 24시간 보류
  ignoreAfterH: 48,             // 48시간 안 열면 무시 1회
  ignPerStep: 3,                // 연속 무시 3회마다 개인 한 단계 내림
  ignRest: 9, restDays: 14,     // 연속 무시 9회 = 14일 쉼
  maxTokens: 5,
  reconfirmEveryDays: 730, reconfirmLeadDays: 30,
});
// 묶음 자동 조절 임계값(비율 = 0~1). 기준선 = 시작 단계 이하로 보낸 날(최근 28일, 표본 충분한 날) 평균 — 단계를 올리며 기준선이 같이 올라가는 것을 막음.
const TUNE = Object.freeze({
  minReach: 200, minBaseDays: 7, baseWindowDays: 28,
  down2: { optout: [2.0, 0.010], uninstall: [2.0, 0.005] },                    // 배수·절대 증가 둘 다 넘으면 두 단계 내림
  down1: { optout: [1.5, 0.003], uninstall: [1.5, 0.002], perm_off: [1.5, 0.003] }, // 하나라도 넘으면 한 단계 내림
  hardOptout: 0.01,             // 하루 거부율 1% 이상이면 기준선과 상관없이 한 단계 내림
  good: { ratio: 1.1, openKeep: 0.9 }, upAfterGoodDays: 7,
});
const COHORTS = Object.freeze(['c0', 'c1', 'c2', 'c3']);
const TITLE_MAX = 30, BODY_MAX = 90;
const OPT_OUT = '수신거부: 앱 나 탭 > 알림 > 광고 끄기';
const DEAD = new Set(['messaging/registration-token-not-registered', 'messaging/invalid-registration-token', 'messaging/invalid-argument']);
const AD_WORDS = /\(광고\)|할인|쿠폰|특가|이벤트|프로모션|세일|적립|혜택|무료\s*체험|%\s*off|\bsale\b|coupon|promo/i;

const ms = (t) => (t == null ? null : typeof t === 'number' ? t : t.toMillis ? t.toMillis() : t instanceof Date ? t.getTime() : null);
function localHour(now, tzOff) { return new Date(now + tzOff * 60e3).getUTCHours(); }
function localDate(now, tzOff) { return new Date(now + tzOff * 60e3).toISOString().slice(0, 10); }
function localDow(now, tzOff) { return new Date(now + tzOff * 60e3).getUTCDay(); }
function dayType(now, tzOff) { const d = localDow(now, tzOff); return d === 0 || d === 6 ? 'we' : 'wk'; }
function hash32(x) { let h = 2166136261; for (const ch of String(x)) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619) >>> 0; } return h; }
/** 이 사람의 오늘 슬롯. 10명 중 1명(날마다 바뀜)은 맨 끝 슬롯을 후보 하나로 바꿔 시험(A/B 탐색). */
function slotsFor(uid, slots, n, date) {
  const base = (slots && slots.length ? slots : SLOT_POOL).slice(0, Math.max(1, n));
  if (hash32(uid + '|' + date) % EXPLORE.every !== 0) return { hours: base, explore: null };
  const cand = SLOT_POOL.filter((h) => !base.includes(h));
  if (!cand.length) return { hours: base, explore: null };
  const e = cand[hash32(date + '|' + uid) % cand.length];
  return { hours: base.slice(0, -1).concat([e]).sort((a, b) => a - b), explore: e };
}
function cohortOf(uid) { let h = 2166136261; for (const ch of String(uid)) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619) >>> 0; } return COHORTS[h % COHORTS.length]; }
/** 서비스 통지 글에 광고 낱말이 섞였는지 — 섞였으면 false(보내지 않음). */
function serviceTextOk(m) { return !!m && !AD_WORDS.test(`${m.title || ''} ${m.body || ''}`); }

function campaignOk(c) {
  if (!c || typeof c.title !== 'string' || typeof c.body !== 'string') return 'shape';
  const t = c.title.trim(), b = c.body.trim();
  if (!t || !b || t.length > TITLE_MAX || b.length > BODY_MAX) return 'length';
  if (/\(광고\)|수신거부/.test(t + b)) return 'dup_label';
  if (c.cities != null && (!Array.isArray(c.cities) || c.cities.length > 30)) return 'cities';
  return null;
}
function buildAd(c, contact) {
  const who = contact ? `${SENDER}(${contact})` : SENDER;
  return { title: `(광고) ${who} · ${c.title.trim()}`, body: `${c.body.trim()}\n${OPT_OUT}` };
}

/** 개인 단계 = 묶음 단계 − floor(연속 무시/3), 0 아래로 안 감. */
function personalLevel(cohortLvl, ign) { return Math.max(0, Math.min(LEVELS.length - 1, cohortLvl) - Math.floor((ign || 0) / POLICY.ignPerStep)); }

/** 지금 보내도 되나 — 이유(안 됨) 또는 null. 'later' = 시각·간격만 안 맞음. */
function eligible({ user, state, trip, c, now, cohortLvl = START, slots = SLOTS_DEFAULT, uid = '' }) {
  const m = (user && user.mkt) || {};
  if (m.push !== true) return 'no_consent';
  const at = ms(m.at);
  if (at == null) return 'no_consent_time';
  if (now - at < POLICY.quietAfterConsentH * H) return 'later';
  const tz = user.tz_off;
  if (typeof tz !== 'number' || Math.abs(tz) > 14 * 60) return 'no_tz';
  const h = localHour(now, tz);
  const st0 = state || {};
  const L0 = LEVELS[personalLevel(cohortLvl, st0.ign)];
  const my = slotsFor(uid, slots[dayType(now, tz)], L0.day, localDate(now, tz));
  const dayOk = h >= POLICY.dayFrom && h < POLICY.dayTo && my.hours.includes(h);
  const nightOk = m.night === true && c.night === true && NIGHT_SLOTS.includes(h) && h >= POLICY.dayTo && h < POLICY.nightTo;
  if (!dayOk && !nightOk) return 'later';
  if (trip) { if (!c.for_trip || !Array.isArray(c.cities) || !c.cities.includes(trip.city)) return 'on_trip_mismatch'; }
  else if (c.for_trip) return 'not_on_trip';
  const st = state || {};
  if (ms(st.rest_until) != null && now < ms(st.rest_until)) return 'rest';
  const L = LEVELS[personalLevel(cohortLvl, st.ign)];
  const r = (st.recent || []).map(ms).filter((x) => x != null);
  if (r.some((x) => now - x < POLICY.gapH * H)) return 'later';
  if (r.filter((x) => now - x < DAY).length >= L.day) return 'cap_day';
  if (r.filter((x) => now - x < 7 * DAY).length >= L.week) return 'cap_week';
  return null;
}

function activeTrip(trips, now, tzOff) {
  const today = localDate(now, tzOff || 0);
  const d = (v) => (typeof v === 'string' ? v.slice(0, 10) : ms(v) != null ? localDate(ms(v), tzOff || 0) : null);
  for (const t of trips || []) { const a = d(t.start_date), b = d(t.end_date); if (a && b && a <= today && today <= b) return { city: String(t.city || '') }; }
  return null;
}

// ---------------------------------------------------------------- 묶음 자동 조절(순수)
const rate = (n, d) => (d > 0 ? n / d : 0);
/** 하루 지표 한 줄. reach = 그날 광고를 1건 이상 받은 사람 수(거부·끄기·삭제율의 분모), sent = 보낸 건수(열람률 분모). */
function dayRates(x) {
  return { reach: x.reach || 0, sent: x.sent || 0, optout: rate(x.optout || 0, x.reach), perm_off: rate(x.perm_off || 0, x.reach),
    uninstall: rate(x.uninstall || 0, x.reach), open: rate(x.opens || 0, x.sent) };
}
/** 오늘 지표·지난 날들·지금 단계 → 새 단계와 까닭. */
function decideLevel(today, hist, lv) {
  const cur = { lvl: lv && Number.isInteger(lv.lvl) ? lv.lvl : START, good: (lv && lv.good) || 0 };
  const t = dayRates(today);
  if (t.reach < TUNE.minReach) return { ...cur, why: 'small' };
  if (t.optout >= TUNE.hardOptout) return { lvl: Math.max(0, cur.lvl - 1), good: 0, why: 'hard_optout' };
  let base = (hist || []).filter((h) => (h.reach || 0) >= TUNE.minReach && (h.lvl == null || h.lvl <= START)).map(dayRates);
  if (base.length < TUNE.minBaseDays) return { ...cur, why: 'no_base' };
  const avg = (k) => base.reduce((a, b) => a + b[k], 0) / base.length;
  const b = { optout: avg('optout'), perm_off: avg('perm_off'), uninstall: avg('uninstall'), open: avg('open') };
  const over = (k, [mul, add]) => t[k] >= Math.max(b[k] * mul, b[k] + add);
  if (Object.entries(TUNE.down2).every(([k, v]) => over(k, v)) || over('optout', TUNE.down2.optout)) return { lvl: Math.max(0, cur.lvl - 2), good: 0, why: 'down2' };
  if (Object.entries(TUNE.down1).some(([k, v]) => over(k, v))) return { lvl: Math.max(0, cur.lvl - 1), good: 0, why: 'down1' };
  const fine = ['optout', 'perm_off', 'uninstall'].every((k) => t[k] <= b[k] * TUNE.good.ratio + 1e-4) && t.open >= b.open * TUNE.good.openKeep;
  if (!fine) return { lvl: cur.lvl, good: 0, why: 'hold' };
  const good = cur.good + 1;
  if (good >= TUNE.upAfterGoodDays && cur.lvl < LEVELS.length - 1) return { lvl: cur.lvl + 1, good: 0, why: 'up' };
  return { lvl: cur.lvl, good, why: 'good' };
}

/** 슬롯 점수 = 열람률 − 5×거부율 − 5×삭제율 − 3×알림 끄기율(분모: 열람 = 보낸 건수, 나머지 = 받은 사람). */
function slotScore(r) { const x = dayRates(r); return x.open - EXPLORE.w.optout * x.optout - EXPLORE.w.uninstall * x.uninstall - EXPLORE.w.perm_off * x.perm_off; }
/** 14일 묶음 → 새 슬롯 순서. 보낸 건수 300 넘는 슬롯만 점수, 서로 2시간 미만이면 뒤엣것 건너뜀, 점수 슬롯이 3개 안 되면 그대로. */
function reselectSlots(rows, cur) {
  const ok = Object.entries(rows || {}).filter(([, r]) => (r.sent || 0) >= EXPLORE.minSent).map(([h, r]) => ({ h: +h, sc: slotScore(r) })).sort((a, b) => b.sc - a.sc);
  if (ok.length < 3) return { order: cur, why: 'few' };
  const pick = [];
  for (const { h } of ok.concat((cur || []).map((h) => ({ h })))) if (!pick.includes(h) && pick.every((p) => Math.abs(p - h) >= POLICY.gapH)) pick.push(h);
  return { order: pick.slice(0, LEVELS[LEVELS.length - 1].day), why: 'score' };
}

// ---------------------------------------------------------------- 서비스 통지(광고 아님)
function fmtDate(t, tzOff) { const s = localDate(t, tzOff == null ? 540 : tzOff); return `${s.slice(0, 4)}년 ${+s.slice(5, 7)}월 ${+s.slice(8, 10)}일`; }
function consentNote(items, at, tzOff) {
  const it = (id) => (items || []).find((x) => x && x.id === id);
  const p = it('ad_push'), n = it('ad_night');
  if (!p && !n) return null;
  const part = [];
  if (p) part.push(`광고성 정보 수신 ${p.ok ? '동의' : '거부'}`);
  if (n) part.push(`야간(21~8시) 광고성 정보 수신 ${n.ok ? '동의' : '거부'}`);
  return { title: `${SENDER} 수신 설정 처리 결과`, body: `${fmtDate(at, tzOff)} ${part.join(' · ')}가 처리됐어요. 바꾸기: 나 탭 > 알림` };
}
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
  async function ev(id, kind, uid, extra) {
    try { await db.doc(`mkt_ev/${id}`).create({ kind, uid, cohort: cohortOf(uid), at: now(), ...(extra || {}) }); return true; } catch (_) { return false; }
  }
  async function pushTo(uid, msg, data) {
    const toks = (await db.collection(`users/${uid}/fcm_tokens`).limit(POLICY.maxTokens).get()).docs;
    let sent = 0, dead = 0;
    for (const td of toks) {
      const token = String((td.data() || {}).fcm_token || '');
      if (!token) continue;
      try { await messaging.send({ token, notification: msg, data }); sent++; } catch (e) {
        if (DEAD.has(e && e.code)) { dead++; try { await td.ref.delete(); } catch (_) { /* 다음에 */ } }
        log('mkt_send_err', { code: e && e.code });
      }
    }
    // 토큰이 전부 죽음 = 앱 삭제(또는 재설치 전) 추정 지표 — 대시보드 '삭제율'의 근거(정의는 NOTIF_292.md 4절)
    if (dead && !sent) await ev(`${uid}_u_${localDate(now(), 540)}`, 'uninstall', uid);
    return { sent, tokens: toks.length };
  }
  async function servicePush(uid, msg) {
    if (!serviceTextOk(msg)) { log('mkt_service_blocked', { uid }); return { sent: 0, tokens: 0, blocked: true }; }
    return pushTo(uid, msg, { type: 'mkt_notice' });
  }

  async function sendOne(cid, c, uid, user, levels) {
    const t = now();
    const trips = (await db.collection('trips').where('owner_uid', '==', uid).limit(20).get()).docs.map((d) => d.data());
    const trip = activeTrip(trips, t, user.tz_off);
    const sendRef = db.doc(`mkt_sends/${cid}_${uid}`), stRef = db.doc(`mkt_state/${uid}`);
    const cohort = cohortOf(uid);
    let slotInfo = {};
    const why = await db.runTransaction(async (tx) => {
      const [s, st] = await Promise.all([tx.get(sendRef), tx.get(stRef)]);
      if (s.exists) return 'dup';
      const state = st.exists ? st.data() : {};
      const r = eligible({ user, state, trip, c, now: t, cohortLvl: levels[cohort], slots: deps.slots || SLOTS_DEFAULT, uid });
      if (r) return r;
      const tz = user.tz_off, h = localHour(t, tz), dt = dayType(t, tz);
      const my = slotsFor(uid, (deps.slots || SLOTS_DEFAULT)[dt], LEVELS[personalLevel(levels[cohort], state.ign)].day, localDate(t, tz));
      slotInfo = { slot: h, dt, explore: my.explore === h };
      const recent = (state.recent || []).map(ms).filter((x) => x != null && t - x < 7 * DAY).concat([t]);
      tx.create(sendRef, { uid, cid, cohort, at: t, status: 'claimed', ...slotInfo, variant: String(c.variant || cid) });
      tx.set(stRef, { recent, cohort }, { merge: true });
      return null;
    });
    if (why) return why;
    const r = await pushTo(uid, buildAd(c, deps.contact && deps.contact()), { type: 'mkt', cid: String(cid) });
    await sendRef.update({ status: r.sent ? 'sent' : 'no_token', sent: r.sent });
    return r.sent ? 'sent' : 'no_token';
  }
  async function levelsNow() {
    const out = {};
    for (const k of COHORTS) { const d = await db.doc(`mkt_level/${k}`).get(); out[k] = d.exists && Number.isInteger(d.data().lvl) ? d.data().lvl : START; }
    return out;
  }
  async function dispatch() {
    const t = now();
    const out = { campaigns: 0, sent: 0, skipped: {} };
    const levels = await levelsNow();
    const sd = await db.doc('mkt_slots/current').get();
    deps.slots = sd.exists && sd.data().wk && sd.data().we ? { wk: sd.data().wk, we: sd.data().we } : SLOTS_DEFAULT;
    const cs = (await db.collection('mkt_campaigns').where('status', '==', 'ready').limit(10).get()).docs;
    for (const cd of cs) {
      const c = cd.data();
      const bad = campaignOk(c);
      if (bad) { await cd.ref.update({ status: 'rejected', reason: bad }); continue; }
      if (ms(c.send_after) != null && t < ms(c.send_after)) continue;
      if (ms(c.expires_at) == null || t > ms(c.expires_at)) { await cd.ref.update({ status: 'done' }); continue; }
      out.campaigns++;
      const q = db.collection('users').where('mkt.push', '==', true).orderBy('__name__').limit(300);
      let last = null;
      for (;;) {
        const page = (await (last ? q.startAfter(last) : q).get()).docs;
        for (const u of page) {
          const why = await sendOne(cd.id, c, u.id, u.data(), levels);
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
    const items = (d && d.items) || [];
    const p = items.find((x) => x && x.id === 'ad_push');
    if (p) await ev(`${uid}_c_${cid}`, p.ok ? 'optin' : 'optout', uid);
    const note = consentNote(items, ms(d && d.at) || now(), d && d.tz_off);
    if (!note) return 'not_mkt';
    try { await db.doc(`mkt_notices/${uid}_c_${cid}`).create({ uid, kind: 'consent', at: now() }); } catch (_) { return 'dup'; }
    const r = await servicePush(uid, note);
    return r.sent ? 'sent' : 'no_token';
  }
  async function onOpen(uid, cid0) {
    // 앱이 쓰는 id: '{cid}' = 알림을 눌러 엶 · '{cid}_k' = 열린 화면에서 캠페인 버튼을 누름(클릭)
    const click = String(cid0).endsWith('_k'), cid = click ? String(cid0).slice(0, -2) : String(cid0);
    const snd = await db.doc(`mkt_sends/${cid}_${uid}`).get();
    const tag = snd.exists ? { cid, slot: snd.data().slot, dt: snd.data().dt, variant: snd.data().variant } : { cid };
    if (click) return (await ev(`${uid}_k_${cid}`, 'click', uid, tag)) ? 'click' : 'dup';
    const fresh = await ev(`${uid}_o_${cid}`, 'open', uid, tag);
    if (!fresh) return 'dup';
    const sendRef = db.doc(`mkt_sends/${cid}_${uid}`);
    await db.runTransaction(async (tx) => {
      const s = await tx.get(sendRef);
      if (s.exists && ['sent', 'ignored'].includes(s.data().status)) tx.update(sendRef, { status: 'opened' });
      tx.set(db.doc(`mkt_state/${uid}`), { ign: 0, rest_until: null }, { merge: true });
    });
    return 'open';
  }
  async function onUserChange(uid, before, after) {
    if ((before && before.perm_on) === true && after && after.perm_on === false) return (await ev(`${uid}_p_${localDate(now(), 540)}`, 'perm_off', uid)) ? 'perm_off' : 'dup';
    return 'none';
  }

  async function markIgnored() {
    const t = now();
    const old = (await db.collection('mkt_sends').where('at', '<=', t - POLICY.ignoreAfterH * H).limit(2000).get()).docs
      .filter((d) => d.data().status === 'sent' && t - d.data().at < 7 * DAY);
    let n = 0;
    for (const d of old) {
      const uid = d.data().uid, stRef = db.doc(`mkt_state/${uid}`);
      const did = await db.runTransaction(async (tx) => {
        const [s, st] = await Promise.all([tx.get(d.ref), tx.get(stRef)]);
        if (!s.exists || s.data().status !== 'sent') return false; // 이미 처리 = 다시 감점 안 함
        const ign = ((st.exists && st.data().ign) || 0) + 1;
        tx.update(d.ref, { status: 'ignored' });
        tx.set(stRef, ign >= POLICY.ignRest ? { ign, rest_until: t + POLICY.restDays * DAY } : { ign }, { merge: true });
        return true;
      });
      if (did) n++;
    }
    return n;
  }
  /** 어제(KST) 지표 → mkt_metrics, 묶음 단계 조절 → mkt_level. 같은 날 두 번 돌면 같은 문서를 같은 값으로 덮고, 단계는 그날 한 번만 바꾼다(mkt_level.day). */
  async function metrics() {
    const t = now();
    const ignored = await markIgnored();
    const day = localDate(t - DAY, 540);
    const start = Date.parse(day + 'T00:00:00+09:00'), end = start + DAY;
    const agg = {};
    for (const k of COHORTS.concat(['all'])) agg[k] = { reach: 0, sent: 0, opens: 0, clicks: 0, optout: 0, perm_off: 0, uninstall: 0, _u: new Set() };
    const sends0 = (await db.collection('mkt_sends').where('at', '>=', start - DAY).limit(100000).get()).docs.map((d) => d.data()).filter((s) => s.at < end && s.status !== 'claimed' && s.status !== 'no_token');
    const sends = sends0.filter((s) => s.at >= start);
    // 거부·끄기·삭제 사건은 그 사람이 사건 전 24시간 안에 마지막으로 받은 광고의 슬롯·문구로 돌린다
    const lastBefore = (uid, at) => sends0.filter((s) => s.uid === uid && s.at <= at && at - s.at < DAY).sort((a, b) => b.at - a.at)[0];
    const grp = {};
    const g = (k) => grp[k] || (grp[k] = { sent: 0, opens: 0, clicks: 0, optout: 0, perm_off: 0, uninstall: 0, _u: new Set() });
    for (const s of sends) for (const k of [`s_${s.dt || 'wk'}_${s.slot}`, `k_${s.variant || s.cid}`]) { g(k).sent++; g(k)._u.add(s.uid); }
    for (const s of sends) for (const k of [s.cohort || cohortOf(s.uid), 'all']) { agg[k].sent++; agg[k]._u.add(s.uid); }
    const evs = (await db.collection('mkt_ev').where('at', '>=', start).limit(100000).get()).docs.map((d) => d.data()).filter((e) => e.at < end);
    for (const e of evs) {
      const f = e.kind === 'open' ? 'opens' : e.kind === 'click' ? 'clicks' : e.kind;
      if (f in agg.all) for (const k of [e.cohort, 'all']) if (agg[k]) agg[k][f]++;
      const src = e.kind === 'open' || e.kind === 'click' ? e : ['optout', 'perm_off', 'uninstall'].includes(e.kind) ? lastBefore(e.uid, e.at) : null;
      if (src && src.slot != null) g(`s_${src.dt || 'wk'}_${src.slot}`)[f]++;
      if (src && (src.variant || src.cid)) g(`k_${src.variant || src.cid}`)[f]++;
    }
    for (const [k, a] of Object.entries(grp)) { a.reach = a._u.size; delete a._u; await db.doc(`mkt_metrics/${day}_${k}`).set({ day, key: k, ...a, rates: dayRates(a), click: rate(a.clicks, a.sent) }); }
    const out = {};
    for (const [k, a] of Object.entries(agg)) {
      a.reach = a._u.size; delete a._u;
      const doc = { day, cohort: k, ...a, rates: dayRates(a) };
      if (k !== 'all') {
        const lvRef = db.doc(`mkt_level/${k}`);
        const lvSnap = await lvRef.get();
        const lv = lvSnap.exists ? lvSnap.data() : { lvl: START, good: 0 };
        doc.lvl = Number.isInteger(lv.lvl) ? lv.lvl : START;
        if (lv.day !== day) {
          const hist = [];
          for (let i = 1; i <= TUNE.baseWindowDays; i++) {
            const h = await db.doc(`mkt_metrics/${localDate(start - i * DAY + 12 * H, 0)}_${k}`).get();
            if (h.exists) hist.push(h.data());
          }
          const dec = decideLevel(a, hist, lv);
          await lvRef.set({ lvl: dec.lvl, good: dec.good, why: dec.why, day, at: t });
          doc.decision = dec;
        } else doc.decision = { lvl: lv.lvl, why: 'already' };
      }
      await db.doc(`mkt_metrics/${day}_${k}`).set(doc);
      out[k] = doc;
    }
    out.slots = await maybeReselect(day, t);
    log('mkt_metrics', { day, ignored, all: out.all && out.all.rates, slots: out.slots && out.slots.why });
    return out;
  }
  /** 14일마다 슬롯 다시 고르기(평일·주말 따로). 지난 14일 슬롯 지표 문서를 더해 reselectSlots. */
  async function maybeReselect(day, t) {
    const ref = db.doc('mkt_slots/current');
    const cur = await ref.get();
    const c = cur.exists ? cur.data() : { wk: SLOTS_DEFAULT.wk.slice(), we: SLOTS_DEFAULT.we.slice(), day: null };
    if (!cur.exists) { await ref.set({ ...c, day, why: 'init' }); return { why: 'init' }; }
    if (c.day && Date.parse(day) - Date.parse(c.day) < EXPLORE.reselectDays * DAY) return { why: 'wait' };
    const next = { wk: c.wk, we: c.we };
    const why = {};
    for (const dt of ['wk', 'we']) {
      const rows = {};
      for (const h of SLOT_POOL) {
        const sum = { sent: 0, reach: 0, opens: 0, optout: 0, perm_off: 0, uninstall: 0 };
        for (let i = 0; i < EXPLORE.reselectDays; i++) {
          const d = await db.doc(`mkt_metrics/${localDate(Date.parse(day) - i * DAY + 12 * H, 0)}_s_${dt}_${h}`).get();
          if (d.exists) for (const k of Object.keys(sum)) sum[k] += d.data()[k] || 0;
        }
        rows[h] = sum;
      }
      const r = reselectSlots(rows, c[dt]);
      next[dt] = r.order; why[dt] = r.why;
    }
    await ref.set({ ...next, day, why, prev: { wk: c.wk, we: c.we }, at: t });
    return { why, ...next };
  }

  async function reconfirm() {
    const t = now();
    const cutoff = new Date(t - (POLICY.reconfirmEveryDays - POLICY.reconfirmLeadDays) * DAY);
    const res = { sent: 0, none: 0, skip: 0 };
    const q = db.collection('users').where('mkt.at', '<=', cutoff).orderBy('mkt.at').limit(300);
    let last = null;
    for (;;) {
      const page = (await (last ? q.startAfter(last) : q).get()).docs;
      for (const u of page) {
        const ud = u.data() || {}, m = ud.mkt;
        const stRef = db.doc(`mkt_state/${u.id}`);
        const st = await stRef.get();
        const due = reconfirmDue(m, st.exists ? st.data() : {}, t);
        if (!due) { res.skip++; continue; }
        try { await db.doc(`mkt_notices/${u.id}_${due.key}`).create({ uid: u.id, kind: 'reconfirm', at: t }); } catch (_) { res.skip++; continue; }
        const r = await servicePush(u.id, reconfirmNote(m, ud.tz_off));
        await stRef.set({ rc_at: t, rc_channel: r.sent ? 'push' : 'app' }, { merge: true });
        if (r.sent) res.sent++; else res.none++;
      }
      if (page.length < 300) break;
      last = page[page.length - 1];
    }
    log('mkt_reconfirm', res);
    return res;
  }
  return { dispatch, onConsent, onOpen, onUserChange, metrics, markIgnored, reconfirm, sendOne };
}

function makeMktFunctions({ region = REGION } = {}) {
  const { onSchedule } = require('firebase-functions/v2/scheduler');
  const { onDocumentCreated, onDocumentUpdated } = require('firebase-functions/v2/firestore');
  const { defineString } = require('firebase-functions/params');
  const { logger } = require('firebase-functions');
  const { getFirestore } = require('firebase-admin/firestore');
  const { getMessaging } = require('firebase-admin/messaging');
  const CONTACT = defineString('MKT_SENDER_CONTACT', { default: '' });
  let core;
  const C = () => core || (core = makeMktCore({ db: getFirestore(), messaging: { send: (m) => getMessaging().send(m) }, now: () => Date.now(),
    contact: () => CONTACT.value(), log: (n, o) => logger.info(n, o) }));
  const base = { region, maxInstances: 1, timeoutSeconds: 540, memory: '512MiB' };
  return {
    mktDispatch: onSchedule({ ...base, schedule: 'every 60 minutes', timeZone: 'Asia/Seoul' }, async () => { await C().dispatch(); }),
    mktMetrics: onSchedule({ ...base, schedule: '7 4 * * *', timeZone: 'Asia/Seoul' }, async () => { await C().metrics(); }),
    mktReconfirm: onSchedule({ ...base, schedule: '17 11 * * *', timeZone: 'Asia/Seoul' }, async () => { await C().reconfirm(); }),
    mktConsentNote: onDocumentCreated({ region, document: 'users/{uid}/consents/{cid}', maxInstances: 2 }, async (e) => { await C().onConsent(e.params.uid, e.params.cid, e.data && e.data.data()); }),
    mktOpen: onDocumentCreated({ region, document: 'users/{uid}/mkt_opens/{cid}', maxInstances: 2 }, async (e) => { await C().onOpen(e.params.uid, e.params.cid); }),
    mktPermWatch: onDocumentUpdated({ region, document: 'users/{uid}', maxInstances: 2 }, async (e) => {
      await C().onUserChange(e.params.uid, e.data && e.data.before.data(), e.data && e.data.after.data());
    }),
  };
}

module.exports = { makeMktFunctions, makeMktCore, eligible, campaignOk, buildAd, activeTrip, consentNote, reconfirmDue, reconfirmNote,
  decideLevel, dayRates, personalLevel, cohortOf, serviceTextOk, slotsFor, reselectSlots, slotScore, dayType, LEVELS, START, POLICY, TUNE, OPT_OUT, SLOTS_DEFAULT, SLOT_POOL };
