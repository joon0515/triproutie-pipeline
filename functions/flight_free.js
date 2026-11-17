// flight_free.js — 편명 → 공항·시각, '완전 무료·합법' 조회 사슬 + 매일 수집 (11/14 준비팀, 미배포)
// 비밀값 이름만: DATA_GO_KR_KEY (값 읽기·출력 금지. 로그·응답·저장 어디에도 안 나감). 키 없으면 공공데이터 단계는 조용히 건너뜀.
// 조회 순서: ①우리 누적표 flight_sched ②한국공항공사 스케줄 ③인천/공사 실시간 ④adsbdb(기본 꺼짐, 공항만) ⑤(유료 스위치 켠 때만 flight_lookup.js) ⑥사용자 확인값 공유 캐시
//          → 못 찾으면 {ok:false} → 앱이 ⑦붙여넣기(E_alt/flight_paste) → ⑧대표 공항.
// 응답 필드명·주소: 활용가이드(.docx)를 읽지 못해 '미확인' → 필드는 후보 이름 목록으로 찾고, 주소(base URL)는 환경 변수로 받는다(없으면 그 단계 건너뜀).
//   FLIGHT_URL_ICN_FLT  인천 상세 조회(편명) / FLIGHT_URL_ICN_DEP, FLIGHT_URL_ICN_ARR  인천 출발·도착 목록(수집용)
//   FLIGHT_URL_KAC_SCHED 공사 스케줄(편명) / FLIGHT_URL_KAC_RT 공사 실시간(schFln)
//   FLIGHT_ADSBDB_OK=1  adsbdb 켬(기본 꺼짐: 노선 자료 복사·DB 편입 금지 문구 → 저장·공유 안 함, 요청 안에서만 사용)
'use strict';
const crypto = require('crypto');
const { makeFlightCore, iataSeason, normNo } = require('./flight_lookup');
const NO_RE = /^[A-Z0-9]{2,3}\d{1,4}[A-Z]?$/; // IATA(KE703)·ICAO 콜사인(KAL703) 둘 다

const ICN_DEV_LIMIT = 500, ICN_COLLECT_BUDGET = 400, ICN_ONDEMAND_CAP = 90; // 400+90=490 < 500
const KAC_ONDEMAND_CAP = 300, KAC_RT_CAP = 300, ADSB_CAP = 200;              // 공사 개발 한도 5,000/일 각각
// 약관·상업 사용 허락을 못 찾은 조회처(adsbdb·hexdb — 2026-11-17 출처 점검 31·32번). 운영자의 서면 허락을 받아 이 값을 true 로 바꾸기 전에는
// 환경 변수(FLIGHT_ADSBDB_OK·FLIGHT_HEXDB_OK)를 켜도 호출하지 않는다. 시험: test_noterms.js
const NOTERMS_OK = false;
const USER_PER_DAY = 20, WINDOW_BACK = 3, WINDOW_FWD = 6, PRUNE_DAYS = 120, CONFIRM_MIN = 2;

const kstDay = (ms) => new Date(ms + 9 * 3600e3).toISOString().slice(0, 10);
const validDate = (d) => /^\d{4}-\d{2}-\d{2}$/.test(d || '') && !isNaN(Date.parse(d + 'T00:00:00Z'));
const weekday = (d) => new Date(d + 'T00:00:00Z').getUTCDay();
const addDays = (d, n) => new Date(Date.parse(d + 'T00:00:00Z') + n * 864e5).toISOString().slice(0, 10);
const dayDiff = (a, b) => Math.round((Date.parse(a + 'T00:00:00Z') - Date.parse(b + 'T00:00:00Z')) / 864e5);
const pick = (o, names) => { for (const n of names) if (o && o[n] !== undefined && o[n] !== null && o[n] !== '') return o[n]; return undefined; };
const iata = (x) => { x = String(x || '').toUpperCase().trim(); return /^[A-Z]{3}$/.test(x) ? x : null; };
// '0935' '09:35' '202612030935' '2026-12-03 09:35' → '09:35'
function hhmm(v) {
  if (v === undefined || v === null) return null; const s = String(v).trim();
  let m = /(\d{2}):(\d{2})/.exec(s); if (m) return +m[1] < 24 && +m[2] < 60 ? m[1] + ':' + m[2] : null;
  m = /^(?:\d{8})?(\d{2})(\d{2})(?:\d{2})?$/.exec(s); if (m && +m[1] < 24 && +m[2] < 60) return m[1] + ':' + m[2];
  return null;
}
// '202612030935' → '2026-12-03'
const ymd = (v) => { const m = /^(\d{4})-?(\d{2})-?(\d{2})/.exec(String(v || '')); return m ? `${m[1]}-${m[2]}-${m[3]}` : null; };

// ── 후보 필드명(미확인) ──
const F = {
  no: ['flightId', 'flightNo', 'fln', 'flightNumber', 'FLIGHT_ID', '편명'],
  sched: ['scheduleDateTime', 'scheduledDateTime', 'schDateTime', 'std', 'sta', '예정일시'],
  depCode: ['departureAirportCode', 'depAirportCode', 'depAirport', 'depIata', 'boardingAirportCode', 'depAirportIata', 'schDepAirportCode'],
  arrCode: ['arrivalAirportCode', 'arrAirportCode', 'arrAirport', 'arrIata', 'arrivedAirportCode', 'arrAirportIata', 'schArrAirportCode'],
  apCode: ['airportCode', 'airport_code', '공항코드'],
  depTime: ['depTime', 'departureTime', 'schDepTime', 'std', 'etd', 'depPlandTime'],
  arrTime: ['arrTime', 'arrivalTime', 'schArrTime', 'sta', 'eta', 'arrPlandTime'],
  gubun: ['io', 'type', 'ioType', 'operationType', 'arrdep'],
};
const DAYK = [['sun', 'sunday', 'SUN'], ['mon', 'monday', 'MON'], ['tue', 'tuesday', 'TUE'], ['wed', 'wednesday', 'WED'], ['thu', 'thursday', 'THU'], ['fri', 'friday', 'FRI'], ['sat', 'saturday', 'SAT']];
const yes = (v) => v === true || v === 1 || v === '1' || /^(Y|y|O|o|TRUE|true)$/.test(String(v || ''));

function itemsOf(json) { // data.go.kr 표준 틀 response.body.items(.item) 또는 배열 변형
  if (!json) return [];
  let it = json.response && json.response.body ? json.response.body.items : (json.body ? json.body.items : json.items || json.data);
  if (it && it.item !== undefined) it = it.item;
  if (Array.isArray(json)) it = json;
  return Array.isArray(it) ? it : (it && typeof it === 'object' ? [it] : []);
}

// 인천 상세(편명 하나) 한 줄 → {no,date,from,to,dep|arr} (출발이면 ICN→공항 dep, 도착이면 공항→ICN arr)
function parseIcnRow(r, kind /* 'dep'|'arr'|undefined */) {
  const no = normNo(pick(r, F.no)); if (!NO_RE.test(no)) return null;
  const sd = pick(r, F.sched), t = hhmm(sd), date = ymd(sd); if (!t) return null;
  const other = iata(pick(r, F.apCode) || pick(r, kind === 'arr' ? F.depCode : F.arrCode));
  const dc = iata(pick(r, F.depCode)), ac = iata(pick(r, F.arrCode));
  let k = kind; if (!k) { if (dc === 'ICN' || (!dc && ac && ac !== 'ICN')) k = 'dep'; else if (ac === 'ICN' || (!ac && dc)) k = 'arr'; }
  if (k === 'dep') { const to = ac || other; return to && to !== 'ICN' ? { no, date, from: 'ICN', to, dep: t, arr: null } : null; }
  if (k === 'arr') { const from = dc || other; return from && from !== 'ICN' ? { no, date, from, to: 'ICN', dep: null, arr: t } : null; }
  return null;
}
// 공사 스케줄 한 줄 → {no,from,to,dep,arr,days[]}
function parseKacSched(r) {
  const no = normNo(pick(r, F.no)); if (!NO_RE.test(no)) return null;
  const from = iata(pick(r, F.depCode)), to = iata(pick(r, F.arrCode)), dep = hhmm(pick(r, F.depTime)), arr = hhmm(pick(r, F.arrTime));
  if (!from || !to || !dep) return null;
  const days = []; DAYK.forEach((ks, i) => { if (yes(pick(r, ks))) days.push(i); });
  return { no, from, to, dep, arr, days };
}
const arrShiftOf = (dep, arr) => (dep && arr && arr < dep ? 1 : 0); // 같은 나라 안 근사. 국제선은 서버가 표의 값을 그대로 둠

// ── 거리 → 표준 비행시간(분). adsbdb 위경도 이용 ──
function km(a, b) { const R = 6371, rad = Math.PI / 180, dl = (b.lat - a.lat) * rad, dn = (b.lon - a.lon) * rad; const h = Math.sin(dl / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dn / 2) ** 2; return 2 * R * Math.asin(Math.sqrt(h)); }
const blockMin = (d) => Math.round((25 + d / 13.3) / 5) * 5; // 이착륙 25분 + 순항 800km/h 근사. 5분 단위. 근사값(approx)

// ── 합치기: variants [{from,to,dep,arr,arrShift,days,last}] ──
function mergeVariant(list, v, wd, nowMs) {
  const k = (x) => [x.from, x.to, x.dep || '', x.arr || ''].join('|');
  const same = list.find((x) => k(x) === k(v));
  if (same) { same.days = Array.from(new Set([...same.days, ...(v.days || [wd])])).sort(); same.last = nowMs; return list; }
  // 같은 구간의 부분 레그(출발만/도착만)는 채워 넣는다
  const half = list.find((x) => x.from === v.from && x.to === v.to && ((!x.arr && v.arr && x.dep === (v.dep || x.dep)) || (!x.dep && v.dep && x.arr === (v.arr || x.arr))));
  if (half) { half.dep = half.dep || v.dep; half.arr = half.arr || v.arr; half.days = Array.from(new Set([...half.days, ...(v.days || [wd])])).sort(); half.last = nowMs; return list; }
  list.push({ from: v.from, to: v.to, dep: v.dep || null, arr: v.arr || null, arrShift: v.arrShift || 0, days: (v.days || [wd]).slice().sort(), last: nowMs });
  return list;
}
const prune = (list, nowMs) => list.filter((x) => nowMs - (x.last || 0) <= PRUNE_DAYS * 864e5);

function makeFreeFlight(deps) {
  const env = (n) => (deps.env ? deps.env(n) : '') || '';
  const key = () => env('DATA_GO_KR_KEY');
  const paid = makeFlightCore({ now: deps.now, fetch: deps.fetch, db: deps.db, env: deps.env, verify: deps.verify, log: deps.log });
  const num = (n, d) => (Number(env(n)) > 0 ? Number(env(n)) : d);

  async function incr(path, limit, nowMs) {
    return deps.db.runTransaction(async (tx) => {
      const ref = deps.db.doc(path), s = await tx.get(ref), n = s.exists ? (s.data().n || 0) : 0;
      if (n >= limit) return false; tx.set(ref, { n: n + 1, at: nowMs }, { merge: true }); return true;
    });
  }
  // 공공데이터 호출 1회. 키는 요청 주소에만(오류 문구·로그에 주소를 담지 않음).
  async function gov(base, params) {
    const u = new URL(base); u.searchParams.set('serviceKey', key());
    for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v);
    let r; try { r = await deps.fetch(u.toString(), { headers: { accept: 'application/json' }, signal: AbortSignal.timeout(6000) }); } catch (_) { return { err: 'upstream' }; }
    if (r.status === 429) return { err: 'limit' }; if (r.status === 404) return { none: true }; if (!r.ok) return { err: 'upstream' };
    try { return { json: await r.json() }; } catch (_) { return { err: 'upstream' }; } // XML 응답이면 실패 처리(type=json 을 요청함)
  }
  const pub = (no, date, v, src, extra) => ({ no, date, from: v.from, to: v.to, dep: v.dep || null, arr: v.arr || null, arrShift: v.arrShift || 0, source: src, ...(extra || {}) });

  async function loadSched(no) { const s = await deps.db.doc('flight_sched/' + no).get(); return s.exists ? s.data() : null; }
  async function saveVariants(no, vs, wd, src, nowMs) {
    const cur = (await loadSched(no)) || { no, variants: [] }; let list = prune(cur.variants || [], nowMs);
    for (const v of vs) list = mergeVariant(list, v, wd, nowMs);
    await deps.db.doc('flight_sched/' + no).set({ no, variants: list, updatedAt: nowMs, src });
    return list;
  }
  const hitOf = (list, wd) => (list || []).find((v) => (v.days || []).includes(wd) && v.dep);

  // ③a 인천 상세(편명): D-3~D+6 안의 날짜만
  async function icnByFlight(no, date, nowMs) {
    const base = env('FLIGHT_URL_ICN_FLT'); if (!key() || !base) return null;
    if (dayDiff(date, kstDay(nowMs)) < -WINDOW_BACK || dayDiff(date, kstDay(nowMs)) > WINDOW_FWD) return null;
    if (!(await incr(`flight_sched/_day_${kstDay(nowMs)}_icn_flt`, num('FLIGHT_ICN_ONDEMAND_CAP', ICN_ONDEMAND_CAP), nowMs))) return null;
    const r = await gov(base, { type: 'json', flight_id: no, searchday: date.replace(/-/g, ''), numOfRows: '20', pageNo: '1' }); // 매개변수 이름 미확인
    if (!r.json) return null;
    const rows = itemsOf(r.json).map((x) => parseIcnRow(x)).filter((x) => x && x.no === no && (!x.date || x.date === date));
    if (!rows.length) return null;
    const wd = weekday(date); await saveVariants(no, rows.map((x) => ({ from: x.from, to: x.to, dep: x.dep, arr: x.arr })), wd, 'icn', nowMs);
    const x = rows[0]; return pub(no, date, x, 'icn');
  }
  // ② 공사 스케줄(편명)
  async function kacSched(no, date, nowMs) {
    const base = env('FLIGHT_URL_KAC_SCHED'); if (!key() || !base) return null;
    if (!(await incr(`flight_sched/_day_${kstDay(nowMs)}_kac_sched`, num('FLIGHT_KAC_ONDEMAND_CAP', KAC_ONDEMAND_CAP), nowMs))) return null;
    const r = await gov(base, { type: 'json', fln: no, numOfRows: '50', pageNo: '1' });
    if (!r.json) return null;
    const rows = itemsOf(r.json).map(parseKacSched).filter((x) => x && x.no === no); if (!rows.length) return null;
    const vs = rows.map((x) => ({ from: x.from, to: x.to, dep: x.dep, arr: x.arr, arrShift: arrShiftOf(x.dep, x.arr), days: x.days }));
    const list = await saveVariants(no, vs, weekday(date), 'kac', nowMs);
    const h = hitOf(list, weekday(date)); return h ? pub(no, date, h, 'kac_sched') : null;
  }
  // ③b 공사 실시간(schFln) — 오늘 전후 편만 의미 있음
  async function kacRealtime(no, date, nowMs) {
    const base = env('FLIGHT_URL_KAC_RT'); if (!key() || !base) return null;
    if (Math.abs(dayDiff(date, kstDay(nowMs))) > 1) return null;
    if (!(await incr(`flight_sched/_day_${kstDay(nowMs)}_kac_rt`, num('FLIGHT_KAC_RT_CAP', KAC_RT_CAP), nowMs))) return null;
    const r = await gov(base, { type: 'json', schFln: no });
    if (!r.json) return null;
    const rows = itemsOf(r.json).map(parseKacSched).filter((x) => x && x.no === no); if (!rows.length) return null;
    const x = rows[0]; return pub(no, date, { ...x, arrShift: arrShiftOf(x.dep, x.arr) }, 'kac_rt');
  }
  // ④ adsbdb: 공항만(+거리로 표준 비행시간). 저장·공유 없음(노선 자료 복사·DB 편입 금지 문구).
  async function adsbdb(no, date, nowMs) {
    if (!NOTERMS_OK || env('FLIGHT_ADSBDB_OK') !== '1') return null;
    if (!(await incr(`flight_sched/_day_${kstDay(nowMs)}_adsbdb`, num('FLIGHT_ADSB_CAP', ADSB_CAP), nowMs))) return null;
    let r; try { r = await deps.fetch('https://api.adsbdb.com/v0/callsign/' + encodeURIComponent(no), { headers: { accept: 'application/json' }, signal: AbortSignal.timeout(6000) }); } catch (_) { return null; }
    if (!r.ok) return null; let j; try { j = await r.json(); } catch (_) { return null; }
    const fr = j && j.response && j.response.flightroute; if (!fr || !fr.origin || !fr.destination) return null;
    const from = iata(fr.origin.iata_code), to = iata(fr.destination.iata_code); if (!from || !to) return null;
    const a = { lat: +fr.origin.latitude, lon: +fr.origin.longitude }, b = { lat: +fr.destination.latitude, lon: +fr.destination.longitude };
    const ok = [a.lat, a.lon, b.lat, b.lon].every((x) => Number.isFinite(x));
    return { no, date, from, to, dep: null, arr: null, arrShift: 0, source: 'adsbdb', approx: true, ...(ok ? { blockMin: blockMin(km(a, b)) } : {}), credit: 'adsbdb (route data: D. Taylor, J. Mason)' };
  }
  // ④-0 정적 노선표(vradarserver/standing-data, CC0): 호출 0. 공항(시작·끝)만 알고 시각은 없음 → 앱은 '예약 메일 붙여넣기'로 확정하도록 안내(needConfirm).
  function routesStatic(no, date) {
    const T = deps.routes; if (!T || !T.routes) return null;
    // 노선표 키는 앞자리 0이 없음(KE81). 사용자가 KE081로 쳐도 찾도록 0을 떼고 한 번 더 찾음.
    let a = T.routes[no] || T.routes[String(no).replace(/^([A-Z0-9]{2})0+(?=\d)/, '$1')]; if (!a) return null;
    const from = a[0], to = a[a.length - 1]; if (!iata(from) || !iata(to) || from === to) return null;
    const c1 = T.coords && T.coords[from], c2 = T.coords && T.coords[to];
    const bm = c1 && c2 ? blockMin(km({ lat: c1[0], lon: c1[1] }, { lat: c2[0], lon: c2[1] })) : undefined;
    return { no, date, from, to, dep: null, arr: null, arrShift: 0, source: 'routes', needConfirm: true, ...(a.length > 2 ? { via: a.slice(1, -1) } : {}), ...(bm ? { blockMin: bm } : {}) };
  }
  // ④-1 hexdb.io: 약관 문구를 못 찾음(사용 허락 불명) → 기본 꺼짐(FLIGHT_HEXDB_OK=1). 저장·공유 없음. 응답 모양은 문서만 보고 짬(미확인).
  async function hexdb(no, date, nowMs) {
    if (!NOTERMS_OK || env('FLIGHT_HEXDB_OK') !== '1') return null;
    if (!(await incr(`flight_sched/_day_${kstDay(nowMs)}_hexdb`, num('FLIGHT_HEXDB_CAP', 200), nowMs))) return null;
    let r; try { r = await deps.fetch('https://hexdb.io/api/v2/routes/' + encodeURIComponent(no), { headers: { accept: 'application/json' }, signal: AbortSignal.timeout(6000) }); } catch (_) { return null; }
    if (!r.ok) return null; let j; try { j = await r.json(); } catch (_) { return null; }
    const g = (x) => iata(x && typeof x === 'object' ? (x.iata || x.iata_code || x.IATA) : x);
    const from = g(j && j.origin), to = g(j && j.destination); if (!from || !to) return null;
    return { no, date, from, to, dep: null, arr: null, arrShift: 0, source: 'hexdb', needConfirm: true, credit: 'hexdb.io' };
  }
  // ⑥ 사용자 확인값 공유 캐시: 서로 다른 사용자 2명 이상이 같은 값을 확인해야 사용
  const uidHash = (uid) => crypto.createHash('sha256').update('flt:' + uid).digest('hex').slice(0, 16);
  async function userShared(no, date) {
    const s = await deps.db.doc('flight_user/' + no + '_' + iataSeason(date)).get(); if (!s.exists) return null;
    const wd = weekday(date);
    const v = (s.data().variants || []).filter((x) => (x.days || []).includes(wd) && (x.by || []).length >= CONFIRM_MIN).sort((p, q) => q.by.length - p.by.length)[0];
    return v ? pub(no, date, v, 'user') : null;
  }
  async function confirm(noRaw, date, v, user, nowMs) {
    const no = normNo(noRaw); if (!NO_RE.test(no) || !validDate(date) || !user || !user.uid) return { ok: false, reason: 'input' };
    const from = iata(v && v.from), to = iata(v && v.to), dep = hhmm(v && v.dep), arr = hhmm(v && v.arr);
    if (!from || !to || !dep || !arr || from === to) return { ok: false, reason: 'input' };
    if (!(await incr(`flight_sched/_u_${kstDay(nowMs)}_${uidHash(user.uid)}_c`, 20, nowMs))) return { ok: false, reason: 'limit' };
    const path = 'flight_user/' + no + '_' + iataSeason(date), s = await deps.db.doc(path).get(), cur = s.exists ? s.data() : { no, variants: [] };
    const sh = arrShiftOf(dep, arr) === 1 && v.arrShift === 1 ? 1 : (Number(v.arrShift) >= 0 && Number(v.arrShift) <= 2 ? Number(v.arrShift) : 0), wd = weekday(date), h = uidHash(user.uid);
    let x = cur.variants.find((q) => q.from === from && q.to === to && q.dep === dep && q.arr === arr);
    if (!x) { x = { from, to, dep, arr, arrShift: sh, days: [], by: [] }; cur.variants.push(x); }
    if (!x.days.includes(wd)) x.days.push(wd); if (!x.by.includes(h)) x.by.push(h);
    cur.variants = cur.variants.slice(-12); await deps.db.doc(path).set({ no, variants: cur.variants, updatedAt: nowMs });
    return { ok: true };
  }

  async function lookup(noRaw, date, user) {
    const no = normNo(noRaw); if (!NO_RE.test(no) || !validDate(date)) return { ok: false, reason: 'input' };
    const nowMs = deps.now(), wd = weekday(date);
    // ① 우리 누적표(외부 0회)
    let h = hitOf(((await loadSched(no)) || {}).variants, wd);
    if (h) { const out = pub(no, date, h, 'sched'); return { ok: true, flight: await maybeEstimate(out) }; }
    // 키 없으면 아래 공공 단계는 각자 조용히 null. 사용자당 하루 상한은 외부 호출 단계 전에 한 번만.
    const wantExternal = !!(key() && (env('FLIGHT_URL_KAC_SCHED') || env('FLIGHT_URL_ICN_FLT') || env('FLIGHT_URL_KAC_RT'))) || (NOTERMS_OK && env('FLIGHT_ADSBDB_OK') === '1') || env('FLIGHT_PAID') === '1';
    if (wantExternal && user && user.uid && !(await incr(`flight_sched/_u_${kstDay(nowMs)}_${uidHash(user.uid)}`, USER_PER_DAY, nowMs))) return { ok: false, reason: 'limit' };
    for (const step of [kacSched, icnByFlight, kacRealtime]) {
      let f = null; try { f = await step(no, date, nowMs); } catch (_) { f = null; }
      if (f) return { ok: true, flight: await maybeEstimate(f) };
    }
    const rs = routesStatic(no, date);
    let ad = null; if (!rs) { try { ad = await adsbdb(no, date, nowMs); } catch (_) { ad = null; } if (!ad) { try { ad = await hexdb(no, date, nowMs); } catch (_) { ad = null; } } }
    // ⑤ 유료(스위치 켠 때만) — 시각이 있는 쪽이 우선이라 adsbdb(공항만)보다 먼저 쓴다
    if (env('FLIGHT_PAID') === '1') { const p = await paid.lookup(no, date, user); if (p.ok) return p; }
    const us = await userShared(no, date); if (us) return { ok: true, flight: us };   // 사용자 2명 확인값은 시각까지 있어 공항만 주는 것보다 우선
    if (rs) return { ok: true, flight: rs };
    if (ad) return { ok: true, flight: ad };
    return { ok: false, reason: key() || deps.routes || env('FLIGHT_ADSBDB_OK') === '1' || env('FLIGHT_HEXDB_OK') === '1' ? 'not_found' : 'off' };
  }
  // 출발만 있는 누적 레그: adsbdb 가 켜졌고 도착 시각이 없으면 표준 비행시간으로 도착 추정(approx). 꺼져 있으면 도착 칸은 빈 채로.
  async function maybeEstimate(f) {
    if (f.arr || !f.dep || env('FLIGHT_ADSBDB_OK') !== '1') return f;
    let ad = null; try { ad = await adsbdb(f.no, f.date, deps.now()); } catch (_) {}
    if (!ad || !ad.blockMin || ad.from !== f.from || ad.to !== f.to) return f;
    return { ...f, blockMin: ad.blockMin, approx: true, credit: ad.credit }; // 도착 시각은 앱이 dep+blockMin 으로 '약'으로 표시(시차는 앱 tl_airports 의 tz 사용)
  }

  // 매일 1회(Cloud Scheduler) 수집: 인천 D-3..D+6 창에서 아직 못 받은 날짜를 오래된 순이 아니라 '먼 날짜(D+6)부터' 받는다.
  // 첫날은 7일치(예산 400 안에서 가능한 만큼, 나머지는 다음 날), 이후엔 하루 1일치(= 출발·도착 각 페이지 수만큼 호출).
  async function collectDaily() {
    const keyOk = !!key(), du = env('FLIGHT_URL_ICN_DEP'), au = env('FLIGHT_URL_ICN_ARR');
    if (!keyOk || !du || !au) return { ok: false, reason: 'off', calls: 0 };
    const nowMs = deps.now(), today = kstDay(nowMs), budget = num('FLIGHT_ICN_BUDGET', ICN_COLLECT_BUDGET), rows = num('FLIGHT_ICN_ROWS', 100), maxPage = 30;
    const metaRef = deps.db.doc('flight_sched/_meta'), ms = await metaRef.get(), meta = ms.exists ? ms.data() : { done: {} }, done = meta.done || {};
    const want = []; for (let i = WINDOW_FWD; i >= 0; i--) { const d = addDays(today, i); if (!done[d]) want.push(d); } // 먼 날짜 먼저
    let calls = 0, stopped = false, saved = 0; const got = [];
    for (const d of want) {
      const byNo = new Map(); let dayCalls = 0, complete = true;
      for (const [kind, base] of [['dep', du], ['arr', au]]) {
        for (let p = 1; p <= maxPage; p++) {
          if (calls + dayCalls >= budget) { complete = false; break; }
          dayCalls++;
          const r = await gov(base, { type: 'json', searchday: d.replace(/-/g, ''), from_time: '0000', to_time: '2400', numOfRows: String(rows), pageNo: String(p) }); // 매개변수 이름 미확인
          if (!r.json) { complete = false; break; }
          const items = itemsOf(r.json);
          for (const it of items) { const x = parseIcnRow(it, kind); if (x) { const a = byNo.get(x.no) || []; a.push(x); byNo.set(x.no, a); } }
          if (items.length < rows) break;
        }
        if (!complete) break;
      }
      calls += dayCalls;
      if (!complete) { stopped = true; break; } // 예산 소진·오류: 이 날짜는 '못 받음'으로 두고 다음 실행에서 다시(부분 저장 안 함)
      const wd = weekday(d);
      for (const [no, xs] of byNo) { await saveVariants(no, xs.map((x) => ({ from: x.from, to: x.to, dep: x.dep, arr: x.arr })), wd, 'icn', nowMs); saved++; }
      done[d] = nowMs; got.push(d);
    }
    for (const d of Object.keys(done)) if (dayDiff(today, d) > 14) delete done[d];
    await metaRef.set({ done, lastRun: nowMs, lastCalls: calls });
    return { ok: true, calls, dates: got, flights: saved, stopped, limitNote: calls <= ICN_DEV_LIMIT };
  }

  // 홍콩공항 data.gov.hk 'flights/past'(전날까지, '상업·비상업 재사용 자유' 문구). 약관 전문 미확인이라 기본 꺼짐(FLIGHT_HKG_OK=1). 키 없음. 어제 하루 출발·도착 각 1회=2호출/일.
  async function collectHkg() {
    if (env('FLIGHT_HKG_OK') !== '1') return { ok: false, reason: 'off', calls: 0 };
    const nowMs = deps.now(), d = addDays(kstDay(nowMs), -1), wd = weekday(d), metaRef = deps.db.doc('flight_sched/_meta_hkg'), ms = await metaRef.get();
    if (ms.exists && ms.data().last === d) return { ok: true, calls: 0, flights: 0, skipped: true };
    let calls = 0, flights = 0; const byNo = new Map();
    for (const arrival of [false, true]) {
      calls++; let r; try { r = await deps.fetch(`https://www.hongkongairport.com/flightinfo-rest/rest/flights/past?date=${d}&lang=en&cargo=false&arrival=${arrival}`, { headers: { accept: 'application/json' }, signal: AbortSignal.timeout(8000) }); } catch (_) { return { ok: false, reason: 'upstream', calls }; }
      if (!r.ok) return { ok: false, reason: 'upstream', calls }; let j; try { j = await r.json(); } catch (_) { return { ok: false, reason: 'upstream', calls }; }
      const list = Array.isArray(j) ? (j[0] && j[0].list) || [] : (j && j.list) || (j && j.List) || []; // 모양 미확인: 배열 또는 {list}
      for (const rec of list) {
        const other = iata(arrival ? (Array.isArray(rec.origin) ? rec.origin[0] : rec.origin) : (Array.isArray(rec.destination) ? rec.destination[0] : rec.destination)), t = hhmm(rec.time);
        if (!other || !t || other === 'HKG') continue;
        for (const f of rec.flight || []) { const no = normNo(f && f.no); if (!NO_RE.test(no)) continue; const a = byNo.get(no) || []; a.push(arrival ? { from: other, to: 'HKG', dep: null, arr: t } : { from: 'HKG', to: other, dep: t, arr: null }); byNo.set(no, a); }
      }
    }
    for (const [no, vs] of byNo) { await saveVariants(no, vs, wd, 'hkg', nowMs); flights++; }
    await metaRef.set({ last: d, at: nowMs });
    return { ok: true, calls, flights };
  }

  async function handle(req) {
    let user; try { user = await deps.verify(req); } catch (_) { return { status: 401, body: { ok: false, reason: 'auth' } }; }
    const b = (req && req.body) || {};
    if (b.action === 'confirm') { const r = await confirm(b.no, b.date, b, user, deps.now()); return { status: 200, body: r }; }
    const r = await lookup(b.no, b.date, user);
    deps.log && deps.log('flightLookup', { ok: r.ok, reason: r.reason, src: r.flight && r.flight.source });
    return { status: 200, body: r };
  }
  return { lookup, handle, collectDaily, collectHkg, confirm };
}

module.exports = { makeFreeFlight, NOTERMS_OK, parseIcnRow, parseKacSched, itemsOf, hhmm, blockMin, km, mergeVariant, ICN_COLLECT_BUDGET, ICN_ONDEMAND_CAP, ICN_DEV_LIMIT };
