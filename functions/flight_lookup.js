// flight_lookup.js — 편명 → 출발/도착 공항·시각 조회 서버 함수 초안 (11/14 준비팀, 미배포)
// 비밀값 이름만: AERODATABOX_KEY(RapidAPI 키) · AEROAPI_KEY(FlightAware). 값은 코드·로그·응답 어디에도 나오지 않는다.
// 키가 없거나 켜는 스위치가 없으면 조회하지 않고 {ok:false, reason:'off'} → 앱은 기존 대표 공항 방식으로 조용히 넘어간다.
//
// ★ 약관 확인 결과(README 참고): 두 서비스의 '무료 등급'은 상업 이용 불가. 그래서 스위치를 둔다.
//   FLIGHT_PAID=1       AeroDataBox 유료 등급(RapidAPI Pro 이상)일 때만 켠다. 안 켜면 AeroDataBox 는 부르지 않는다.
//   FLIGHT_AEROAPI_OK=1 FlightAware Standard 이상(월 $100 최소)일 때만 켠다. Personal 은 상업 불가라 기본 꺼짐.
//   FLIGHT_CACHE_DAYS   기본 7 (AeroDataBox 약관: 보관 7일 상한). 약관이 허용하는 플랜일 때만 늘린다.
// 저장: Firestore flight_cache/{편명}_{시즌} 에 공항·시각·요일·받은 시각만. 모든 사용자 공유.
'use strict';

const NO_RE = /^[A-Z0-9]{2}\d{1,4}[A-Z]?$/;
const DAY_CAP_DEFAULT = 120;       // 하루(KST) 전체 외부 호출 상한
const USER_PER_DAY = 20;           // 사용자당 하루 조회(캐시 적중 포함 아님, 외부 호출만)
const CACHE_DAYS_DEFAULT = 7;

function normNo(s) { return String(s || '').toUpperCase().replace(/[\s\-]/g, ''); }
function validDate(d) { return /^\d{4}-\d{2}-\d{2}$/.test(d || '') && !isNaN(Date.parse(d + 'T00:00:00Z')); }
function weekday(d) { return new Date(d + 'T00:00:00Z').getUTCDay(); } // 0=일
function kstDay(ms) { return new Date(ms + 9 * 3600e3).toISOString().slice(0, 10); }

// IATA 시즌: S = 3월 마지막 일요일 ~ 10월 마지막 토요일, W = 10월 마지막 일요일 ~ 이듬해 3월 마지막 토요일. 'W26' = 2026 겨울 시작.
function lastSunday(y, m) { const d = new Date(Date.UTC(y, m + 1, 0)); d.setUTCDate(d.getUTCDate() - d.getUTCDay()); return d.getTime(); }
function iataSeason(date) {
  const t = Date.parse(date + 'T00:00:00Z'); const y = +date.slice(0, 4);
  const sStart = lastSunday(y, 2), wStart = lastSunday(y, 9);
  if (t >= wStart) return 'W' + String(y).slice(2);
  if (t >= sStart) return 'S' + String(y).slice(2);
  return 'W' + String(y - 1).slice(2);
}

function hhmm(local) { const m = /(\d{2}):(\d{2})/.exec(String(local || '').replace('T', ' ').split(' ')[1] || ''); return m ? m[1] + ':' + m[2] : null; }
function dpart(local) { return String(local || '').replace('T', ' ').slice(0, 10); }
function shift(depLocal, arrLocal) { const a = Date.parse(dpart(depLocal) + 'T00:00:00Z'), b = Date.parse(dpart(arrLocal) + 'T00:00:00Z'); return isNaN(a) || isNaN(b) ? 0 : Math.max(0, Math.round((b - a) / 864e5)); }
function iata(x) { x = String(x || '').toUpperCase(); return /^[A-Z]{3}$/.test(x) ? x : null; }

// AeroDataBox: GET /flights/number/{no}/{date} → [{departure:{airport:{iata},scheduledTime:{local}}, arrival:{…}}]
function parseAero(json) {
  const f = Array.isArray(json) ? json[0] : null; if (!f || !f.departure || !f.arrival) return null;
  const dl = f.departure.scheduledTime && f.departure.scheduledTime.local, al = f.arrival.scheduledTime && f.arrival.scheduledTime.local;
  const from = iata(f.departure.airport && f.departure.airport.iata), to = iata(f.arrival.airport && f.arrival.airport.iata);
  const dep = hhmm(dl), arr = hhmm(al); if (!from || !to || !dep || !arr) return null;
  return { from, to, dep, arr, arrShift: shift(dl, al) };
}
// FlightAware AeroAPI: GET /flights/{ident}?start&end → {flights:[{origin:{code_iata,timezone},destination:{…},scheduled_out|scheduled_off,scheduled_on|scheduled_in}]} (UTC → 현지로 바꿈)
function localOf(iso, tz) {
  try { const p = new Intl.DateTimeFormat('sv-SE', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(iso)); return p; } catch (_) { return null; }
}
function parseAeroApi(json, date) {
  const list = json && Array.isArray(json.flights) ? json.flights : []; if (!list.length) return null;
  for (const f of list) {
    const o = f.origin || {}, d = f.destination || {};
    const dl = localOf(f.scheduled_out || f.scheduled_off, o.timezone), al = localOf(f.scheduled_in || f.scheduled_on, d.timezone);
    if (!dl || !al || dpart(dl) !== date) continue;     // 요청한 날(현지 출발일)과 맞는 것만
    const from = iata(o.code_iata), to = iata(d.code_iata), dep = hhmm(dl), arr = hhmm(al);
    if (from && to && dep && arr) return { from, to, dep, arr, arrShift: shift(dl, al) };
  }
  return null;
}

function makeFlightCore(deps) {
  // deps: now(), fetch, db(.doc(path).get()/set(), .runTransaction), env(name)->값, log(name, obj), verify(req)->{uid}
  const env = (n) => (deps.env ? deps.env(n) : '') || '';
  const cacheMs = () => 864e5 * (Number(env('FLIGHT_CACHE_DAYS')) > 0 ? Number(env('FLIGHT_CACHE_DAYS')) : CACHE_DAYS_DEFAULT);
  const cap = () => (Number(env('FLIGHT_DAY_CAP')) > 0 ? Number(env('FLIGHT_DAY_CAP')) : DAY_CAP_DEFAULT);

  async function incr(path, field, limit, nowMs) { // 한도 안이면 1 올리고 true
    return deps.db.runTransaction(async (tx) => {
      const ref = deps.db.doc(path), s = await tx.get(ref), n = s.exists ? (s.data()[field] || 0) : 0;
      if (n >= limit) return false;
      tx.set(ref, { [field]: n + 1, at: nowMs }, { merge: true }); return true;
    });
  }
  async function blocked(prov, nowMs) { const s = await deps.db.doc('flight_cache/_cool_' + prov).get(); return s.exists && (s.data().until || 0) > nowMs; }
  async function cool(prov, nowMs, retryAfterSec) { // 429 → 다음 KST 자정까지(Retry-After 가 더 길면 그만큼) 그 제공자 쉼
    const mid = Date.parse(kstDay(nowMs) + 'T00:00:00Z') - 9 * 3600e3 + 864e5;
    await deps.db.doc('flight_cache/_cool_' + prov).set({ until: Math.max(mid, nowMs + (retryAfterSec || 0) * 1000) });
  }

  async function callProvider(prov, no, date, nowMs) {
    const t = AbortSignal.timeout(6000); let r, parsed;
    try {
      if (prov === 'aerodatabox') {
        r = await deps.fetch(`https://aerodatabox.p.rapidapi.com/flights/number/${encodeURIComponent(no)}/${date}?withAircraftImage=false&withLocation=false`,
          { headers: { 'X-RapidAPI-Key': env('AERODATABOX_KEY'), 'X-RapidAPI-Host': 'aerodatabox.p.rapidapi.com', accept: 'application/json' }, signal: t });
      } else {
        const s = date + 'T00:00:00Z', e = new Date(Date.parse(date + 'T00:00:00Z') + 2 * 864e5).toISOString().slice(0, 19) + 'Z';
        r = await deps.fetch(`https://aeroapi.flightaware.com/aeroapi/flights/${encodeURIComponent(no)}?start=${s}&end=${e}`,
          { headers: { 'x-apikey': env('AEROAPI_KEY'), accept: 'application/json' }, signal: t });
      }
    } catch (_) { return { err: 'upstream' }; }
    if (r.status === 429) { await cool(prov, nowMs, Number(r.headers && r.headers.get && r.headers.get('retry-after')) || 0); return { err: 'limit' }; }
    if (r.status === 404) return { none: true };
    if (!r.ok) return { err: 'upstream' };
    let j; try { j = await r.json(); } catch (_) { return { err: 'upstream' }; }
    parsed = prov === 'aerodatabox' ? parseAero(j) : parseAeroApi(j, date);
    return parsed ? { v: parsed } : { none: true };
  }

  async function lookup(noRaw, date, user) {
    const no = normNo(noRaw); if (!NO_RE.test(no) || !validDate(date)) return { ok: false, reason: 'input' };
    const nowMs = deps.now(), season = iataSeason(date), wd = weekday(date), path = `flight_cache/${no}_${season}`;
    const snap = await deps.db.doc(path).get(); let doc = snap.exists ? snap.data() : null;
    if (doc && nowMs - (doc.fetchedAt || 0) > cacheMs()) doc = null;                       // 약관 보관 기한 지남 → 다시 받음
    const hit = doc && (doc.variants || []).find((v) => (v.days || []).includes(wd));
    if (hit) return { ok: true, flight: pub(no, date, hit, 'cache') };
    // 외부 호출 준비: 키·스위치 없으면 조용히 끝
    const provs = [];
    if (env('AERODATABOX_KEY') && env('FLIGHT_PAID') === '1') provs.push('aerodatabox');
    if (env('AEROAPI_KEY') && env('FLIGHT_AEROAPI_OK') === '1') provs.push('aeroapi');
    if (!provs.length) return { ok: false, reason: 'off' };
    if (user && user.uid && !(await incr(`flight_cache/_u_${kstDay(nowMs)}_${user.uid}`, 'n', USER_PER_DAY, nowMs))) return { ok: false, reason: 'limit' };
    let last = 'not_found';
    for (const p of provs) {
      if (await blocked(p, nowMs)) { last = 'limit'; continue; }
      if (!(await incr(`flight_cache/_day_${kstDay(nowMs)}_${p}`, 'n', cap(), nowMs))) { last = 'limit'; continue; }
      const r = await callProvider(p, no, date, nowMs);
      if (r.v) {
        const base = doc && doc.variants ? doc.variants.filter((v) => v.from !== r.v.from || v.to !== r.v.to || v.dep !== r.v.dep || v.arr !== r.v.arr) : [];
        const same = doc && doc.variants ? doc.variants.find((v) => v.from === r.v.from && v.to === r.v.to && v.dep === r.v.dep && v.arr === r.v.arr) : null;
        const variant = { ...r.v, days: Array.from(new Set([...(same ? same.days : []), wd])).sort() };
        await deps.db.doc(path).set({ no, season, variants: [...base, variant], fetchedAt: doc ? doc.fetchedAt : nowMs, src: p });
        return { ok: true, flight: pub(no, date, variant, p) };
      }
      last = r.err || 'not_found'; if (last === 'not_found' || r.none) last = 'not_found';
    }
    return { ok: false, reason: last };
  }
  function pub(no, date, v, src) { return { no, date, from: v.from, to: v.to, dep: v.dep, arr: v.arr, arrShift: v.arrShift || 0, source: src, credit: src === 'aerodatabox' ? 'AeroDataBox' : (src === 'aeroapi' ? 'FlightAware' : undefined) }; }

  async function handle(req) { // 요청 {no, date}
    let user; try { user = await deps.verify(req); } catch (_) { return { status: 401, body: { ok: false, reason: 'auth' } }; }
    const b = (req && req.body) || {};
    const r = await lookup(b.no, b.date, user);
    deps.log && deps.log('flightLookup', { ok: r.ok, reason: r.reason, src: r.flight && r.flight.source }); // 편명·사용자 값은 남기지 않음
    return { status: 200, body: r };
  }
  return { lookup, handle };
}

module.exports = { makeFlightCore, iataSeason, normNo, parseAero, parseAeroApi, NO_RE };
