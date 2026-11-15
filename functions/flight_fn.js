'use strict';
// 88기(11/15): flightLookup 배포 진입. index.js 끝에 한 줄 `Object.assign(exports, require('./flight_fn').makeFlightFunctions());`
// - 노선표(CC0 standing-data, IATA 편명만)는 번들에 넣지 않고 GCS 에서 처음 한 번 읽어 메모리에 둔다(읽기 전용).
// - 인증 필수(roadRoute 와 같은 makeVerify), 사용자당 분당 상한(인스턴스 메모리), maxInstances 2.
// 116기(11/15): flight.airport 덧붙이기(airport_live.js, 공공데이터 5종). 키·한도 규칙은 107기와 같음.
// 107기(11/15): 실시간 덧붙이기(flight.live). 출발일이 오늘(KST) -3~+6 이고 출발/도착 한쪽이 한국 공항일 때만.
// - 인천(ICN) = 인천국제공항공사 운항 현황 상세(15140153). 그 외 국내공항 = 한국공항공사 실시간(15160195, 오늘만) / 운항 스케줄(15158949).
// - 키: Secret DATA_GO_KR_KEY (defineSecret). 값은 요청 주소에만 쓰고 로그·응답·저장 어디에도 안 나감.
// - 키 없음·실패·한도초과·시간초과 → live 없이 노선표 결과 그대로(조용히).
// - 하루 500건 보호(메모리만, Firestore 없음): 같은 편명+날짜 10분 캐시, 동시 요청 합침, 인스턴스 분당 상한, 인스턴스 하루 상한, 한도초과 응답이면 그날 끔.
// - 앱은 flight.live 를 읽지 않음(TlFlightFill.fromServer 는 이름 붙은 칸만 읽음) → 기존 칸(dep/arr/source/needConfirm)은 바꾸지 않는다.
const zlib = require('zlib');

const REGION = 'asia-northeast3';
const BUCKET = 'trip-mate-0vum9k.firebasestorage.app';
const ROUTES_PATH = 'flight/v1/routes_iata.json.gz';
const USER_PER_MINUTE = 20;

const KR_AIRPORTS = new Set(['ICN', 'GMP', 'PUS', 'CJU', 'TAE', 'CJJ', 'KWJ', 'RSU', 'USN', 'KPX', 'KUV', 'WJU', 'YNY', 'MWX', 'HIN']);
const GOV = {
  icnDep: 'https://apis.data.go.kr/B551177/statusOfAllFltDeOdp/getFltDeparturesDeOdp',
  icnArr: 'https://apis.data.go.kr/B551177/statusOfAllFltDeOdp/getFltArrivalsDeOdp',
  kacRt: 'https://apis.data.go.kr/B551178/flight-search/info',
  kacDom: 'https://apis.data.go.kr/B551178/flight-schedule/dom',
  kacInt: 'https://apis.data.go.kr/B551178/flight-schedule/int',
};
const LIVE = { ttlMs: 10 * 60e3, failTtlMs: 3 * 60e3, perMinute: 8, perDay: { icn: 200, kac: 400 }, timeoutMs: 4000, maxCache: 800, windowBack: 3, windowFwd: 6 };
const TERMINAL = { P01: 'T1', P02: 'T1-탑승동', P03: 'T2' };
const CREDIT = { icn: '인천국제공항공사 (공공데이터포털)', kac: '한국공항공사 (공공데이터포털)' };

const kstDay = (ms) => new Date(ms + 9 * 3600e3).toISOString().slice(0, 10);
const dayDiff = (a, b) => Math.round((Date.parse(a + 'T00:00:00Z') - Date.parse(b + 'T00:00:00Z')) / 864e5);
const s = (v) => (v === undefined || v === null ? '' : String(v).trim());
const canonNo = (x) => { const m = /^([A-Z0-9]{2})0*(\d{1,4}[A-Z]?)$/.exec(s(x).toUpperCase().replace(/\s+/g, '')); return m ? m[1] + m[2] : s(x).toUpperCase(); };
// 'YYYYMMDDHHMM' | 'HHMM' | 'HH:MM' → { d: 'YYYY-MM-DD'|null, t: 'HH:MM' } | null
function dt(v) {
  const x = s(v).replace(/[^0-9]/g, '');
  let d = null, t = null;
  if (x.length >= 12) { d = `${x.slice(0, 4)}-${x.slice(4, 6)}-${x.slice(6, 8)}`; t = x.slice(8, 12); } else if (x.length === 4) t = x; else return null;
  const h = +t.slice(0, 2), m = +t.slice(2, 4);
  if (!(h >= 0 && h <= 24 && m >= 0 && m < 60)) return null;
  return { d, t: `${t.slice(0, 2)}:${t.slice(2, 4)}` };
}
const mins = (p) => (p ? (p.d ? Date.parse(p.d + 'T00:00:00Z') / 60e3 : 0) + (+p.t.slice(0, 2)) * 60 + (+p.t.slice(3, 5)) : null);
function delayOf(a, b) { // b - a (분). 날짜 없으면 자정 넘김 보정(±12시간 안)
  if (!a || !b) return null; let d = mins(b) - mins(a);
  if (!(a.d && b.d)) { if (d < -720) d += 1440; if (d > 720) d -= 1440; }
  return Math.abs(d) <= 2880 ? d : null;
}
function itemsOf(json) {
  const b = json && json.response && json.response.body; let it = b ? b.items : (json && json.items);
  if (it && it.item !== undefined) it = it.item;
  if (!it || it === '') return [];
  return Array.isArray(it) ? it : [it];
}
const headerCode = (json) => s(json && json.response && json.response.header && json.response.header.resultCode);
const yes = (v) => /^(Y|y|1|O|o)$/.test(s(v));

// 한 건 → live. kind 'dep'|'arr'. 공통 모양만 돌려줌(값 없는 칸은 빼기)
function liveOf(o) {
  const out = {}; for (const [k, v] of Object.entries(o)) if (v !== null && v !== undefined && v !== '') out[k] = v; return out;
}
function statusOf(remark, delay) {
  const r = s(remark);
  const cancelled = /결항|취소|CANCEL/i.test(r), diverted = /회항|DIVERT/i.test(r);
  const delayed = !cancelled && (/지연|DELAY/i.test(r) || (delay !== null && delay >= 15));
  return { cancelled, delayed, diverted };
}

function parseIcn(rows, no, date, kind, other) {
  const want = canonNo(no);
  const cand = rows.filter((r) => canonNo(r.flightId) === want).map((r) => ({ r, sc: dt(r.scheduleDatetime), es: dt(r.estimatedDatetime) }))
    .filter((x) => x.sc && (!x.sc.d || x.sc.d === date) && (!other || !s(x.r.airportCode) || s(x.r.airportCode).toUpperCase() === other));
  if (!cand.length) return null;
  const { r, sc, es } = cand[0], dl = delayOf(sc, es), st = statusOf(r.remark, dl);
  return liveOf({ src: 'icn', kind, airport: 'ICN', terminal: TERMINAL[s(r.terminalId)] || s(r.terminalId) || null, gate: s(r.gateNumber) || null,
    checkin: kind === 'dep' ? s(r.chkinRange) || null : null, carousel: kind === 'arr' ? s(r.carousel) || null : null, exit: kind === 'arr' ? s(r.exitNumber) || null : null,
    sched: sc.t, schedDate: sc.d, est: es && es.t !== sc.t || (es && es.d && es.d !== sc.d) ? es.t : null, estDate: es && es.d && es.d !== sc.d ? es.d : null,
    delayMin: dl && dl > 0 ? dl : null, status: s(r.remark) || null, ...st,
    codeshareOf: /slave/i.test(s(r.codeshare)) && s(r.masterFlightId) ? s(r.masterFlightId) : null, credit: CREDIT.icn });
}
function parseKacRt(rows, no, kind, airport) {
  const want = canonNo(no);
  const r = rows.find((x) => canonNo(x.airFln) === want && (!s(x.airport) || s(x.airport).toUpperCase() === airport)); if (!r) return null;
  const sc = dt(r.std), es = dt(r.etd); if (!sc) return null;
  const dl = delayOf(sc, es), remark = s(r.rmkKor) || s(r.rmkEng), st = statusOf(remark, dl);
  return liveOf({ src: 'kac_rt', kind, airport, gate: s(r.gate) || null, sched: sc.t, est: es && es.t !== sc.t ? es.t : null,
    delayMin: dl && dl > 0 ? dl : null, status: remark || null, ...st, credit: CREDIT.kac });
}
const WD = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
function inRange(date, a, b) { const x = date.replace(/-/g, ''), p = s(a).replace(/[^0-9]/g, '').slice(0, 8), q = s(b).replace(/[^0-9]/g, '').slice(0, 8); return (!p || x >= p) && (!q || x <= q); }
function parseKacSched(rows, no, date, kind, airport, dom) {
  const want = canonNo(no), wd = WD[new Date(date + 'T00:00:00Z').getUTCDay()];
  for (const r of rows) {
    if (dom) {
      if (canonNo(r.domesticNum) !== want) continue;
      if (s(r[`domestic${wd}`]) && !yes(r[`domestic${wd}`])) continue;
      if (!inRange(date, r.domesticStdate, r.domesticEddate)) continue;
      const dep = dt(r.domesticStartTime), arr = dt(r.domesticArrivalTime); if (!dep) continue;
      const t = kind === 'dep' ? dep : (arr || null); if (!t) continue;
      return liveOf({ src: 'kac_sched', kind, airport, sched: t.t, schedArr: kind === 'dep' && arr ? arr.t : null, credit: CREDIT.kac });
    } else {
      if (canonNo(r.internationalNum) !== want) continue;
      if (s(r[`international${wd}`]) && !yes(r[`international${wd}`])) continue;
      if (!inRange(date, r.internationalStdate, r.internationalEddate)) continue;
      const io = s(r.internationalIoType).toUpperCase(), k = /^(I|IN)/.test(io) ? 'arr' : /^(O|OUT)/.test(io) ? 'dep' : kind;
      if (k !== kind) continue;
      const t = dt(r.internationalTime); if (!t) continue;
      return liveOf({ src: 'kac_sched', kind, airport, sched: t.t, credit: CREDIT.kac });
    }
  }
  return null;
}

// deps: { fetch, key: () => string, now, log }
function makeLiveEnricher(deps, opt = {}) {
  const L = { ...LIVE, ...opt, perDay: { ...LIVE.perDay, ...(opt.perDay || {}) } };
  const cache = new Map(), inflight = new Map();
  let minute = { m: -1, n: 0 }, day = { d: '', icn: 0, kac: 0 }; const off = { icn: 0, kac: 0 }; // off = 그 시각까지 끔
  const log = (n, o) => { try { deps.log && deps.log(n, o); } catch (_) {} };
  function allow(svc, nowMs) {
    if (off[svc] > nowMs) return false;
    const m = Math.floor(nowMs / 60e3), d = kstDay(nowMs);
    if (minute.m !== m) minute = { m, n: 0 };
    if (day.d !== d) day = { d, icn: 0, kac: 0 };
    if (minute.n >= L.perMinute || day[svc] >= L.perDay[svc]) return false;
    minute.n++; day[svc]++; return true;
  }
  const nextKstMidnight = (nowMs) => Date.parse(kstDay(nowMs) + 'T00:00:00Z') - 9 * 3600e3 + 864e5;
  async function gov(svc, base, params, nowMs) {
    let k = ''; try { k = s(deps.key()); } catch (_) { k = ''; }
    if (!k) return null;
    if (!allow(svc, nowMs)) return null;
    const u = new URL(base); u.searchParams.set('serviceKey', k);
    for (const [a, b] of Object.entries(params)) u.searchParams.set(a, b);
    let text = '', status = 0;
    try {
      const r = await deps.fetch(u.toString(), { headers: { accept: 'application/json' }, signal: AbortSignal.timeout(L.timeoutMs) });
      status = r.status; text = await r.text();
    } catch (_) { log('flightLive', { svc, err: 'net' }); return null; } // 주소(키 포함)는 로그에 담지 않음
    if (/LIMITED_NUMBER_OF_SERVICE_REQUESTS|<returnReasonCode>22</.test(text) || status === 429) { off[svc] = nextKstMidnight(nowMs); log('flightLive', { svc, err: 'limit' }); return null; }
    if (/SERVICE_KEY_IS_NOT_REGISTERED|SERVICE_ACCESS_DENIED|<returnReasonCode>(20|30|31|32)</.test(text) || status === 401 || status === 403) { off[svc] = nowMs + 3600e3; log('flightLive', { svc, err: 'key' }); return null; }
    if (status < 200 || status >= 300) { log('flightLive', { svc, err: 'http' + status }); return null; }
    let j; try { j = JSON.parse(text); } catch (_) { log('flightLive', { svc, err: 'shape' }); return null; }
    const c = headerCode(j); if (c && c !== '00' && c !== '0' && c !== '03') { if (c === '22') off[svc] = nextKstMidnight(nowMs); log('flightLive', { svc, err: 'code' + c.slice(0, 4) }); return null; }
    return itemsOf(j);
  }
  async function fetchLive(f, date, nowMs) {
    const { no, from, to } = f, today = kstDay(nowMs), ymd = date.replace(/-/g, '');
    if (from === 'ICN' || to === 'ICN') {
      const kind = from === 'ICN' ? 'dep' : 'arr', other = kind === 'dep' ? to : from;
      const rows = await gov('icn', kind === 'dep' ? GOV.icnDep : GOV.icnArr, { pageNo: '1', numOfRows: '20', searchdtCode: 'S', searchDate: ymd, flightId: no, passengerOrCargo: 'P', type: 'json' }, nowMs);
      return rows ? parseIcn(rows, no, date, kind, other) : null;
    }
    const kind = KR_AIRPORTS.has(from) ? 'dep' : 'arr', airport = kind === 'dep' ? from : to, other = kind === 'dep' ? to : from;
    const dom = KR_AIRPORTS.has(from) && KR_AIRPORTS.has(to);
    if (date === today) { // 실시간은 날짜 매개변수가 없음 → 오늘 편만
      const rows = await gov('kac', GOV.kacRt, { schFln: no, schAirCode: airport, schIOType: kind === 'dep' ? 'O' : 'I', schLineType: dom ? 'D' : 'I', type: 'json' }, nowMs);
      const v = rows ? parseKacRt(rows, no, kind, airport) : null; if (v) return v;
    }
    const p = { pageNo: '1', numOfRows: '20', schDate: ymd, schDeptCityCode: from, schArrvCityCode: to, schAirLine: no.slice(0, 2), schFlightNum: dom ? no.slice(2).replace(/^0+(?=\d)/, '') : no, type: 'json' };
    const rows = await gov('kac', dom ? GOV.kacDom : GOV.kacInt, p, nowMs);
    return rows ? parseKacSched(rows, no, date, kind, airport, dom) : null;
  }
  // f = 응답 flight(no, from, to, date). 결과 live 또는 null. 절대 throw 하지 않음
  return async function enrich(f, dateIn) {
    try {
      const nowMs = deps.now(), date = s(dateIn || f && f.date);
      if (!f || !/^[A-Z]{3}$/.test(f.from || '') || !/^[A-Z]{3}$/.test(f.to || '') || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return null;
      if (!KR_AIRPORTS.has(f.from) && !KR_AIRPORTS.has(f.to)) return null;
      const dd = dayDiff(date, kstDay(nowMs)); if (!(dd >= -L.windowBack && dd <= L.windowFwd)) return null;
      const no = canonNo(f.no), ck = `${no}|${date}|${f.from}${f.to}`;
      const c = cache.get(ck); if (c && nowMs - c.at < (c.v ? L.ttlMs : L.failTtlMs)) return c.v;
      if (inflight.has(ck)) return await inflight.get(ck);
      const p = fetchLive({ no: s(f.no).toUpperCase(), from: f.from, to: f.to }, date, nowMs).catch(() => null).then((v) => {
        if (v) v.at = nowMs;
        if (cache.size >= L.maxCache) cache.delete(cache.keys().next().value);
        // 키 없음·상한으로 건너뛴 경우도 짧게 캐시(같은 편명 연타 방지)
        cache.set(ck, { at: nowMs, v }); inflight.delete(ck); return v;
      });
      inflight.set(ck, p); return await p;
    } catch (_) { return null; }
  };
}

// 노선표 읽기: 처음 한 번만(동시 요청은 같은 Promise 를 기다림). 실패하면 다음 요청 때 다시 시도.
function makeRoutesLoader(download) {
  let p = null, val = null;
  return async () => {
    if (val) return val;
    if (!p) p = (async () => { const buf = await download(); const j = JSON.parse(zlib.gunzipSync(buf).toString('utf8')); if (!j || !j.routes) throw new Error('routes shape'); val = j; return j; })()
      .catch((e) => { p = null; throw e; });
    return p;
  };
}

function makeMinuteGate(now, limit = USER_PER_MINUTE) {
  const m = new Map();
  return (uid) => {
    const min = Math.floor(now() / 60000), k = uid || '-', c = m.get(k);
    if (m.size > 5000) m.clear();
    if (!c || c.min !== min) { m.set(k, { min, n: 1 }); return true; }
    if (c.n >= limit) return false;
    c.n++; return true;
  };
}

// deps: { verify, db, fetch, env, log, now, loadRoutes, liveKey?, liveOpt? } — 시험에서 가짜로 주입
function makeFlightHandler(deps) {
  const { makeFreeFlight } = require('./flight_free');
  const gate = makeMinuteGate(deps.now);
  // 안쪽(flight_free)의 옛 공공 단계는 계속 끈 채로 둔다: 키 이름을 안쪽에는 보이지 않게(호출 이중 계산 방지)
  const coreEnv = (n) => (n === 'DATA_GO_KR_KEY' ? '' : (deps.env ? deps.env(n) : ''));
  const enrich = makeLiveEnricher({ fetch: deps.fetch, key: deps.liveKey || (() => ''), now: deps.now, log: deps.log }, deps.liveOpt);
  const airportLive = require('./airport_live').makeAirportLive({ fetch: deps.fetch, key: deps.liveKey || (() => ''), now: deps.now, log: deps.log }, deps.airportOpt);
  let core = null;
  return async (req) => {
    if (req.method && req.method !== 'POST') return { status: 405, body: { ok: false, reason: 'method' } };
    let user; try { user = await deps.verify(req); } catch (_) { return { status: 401, body: { ok: false, reason: 'auth' } }; }
    if (!user || !user.uid) return { status: 401, body: { ok: false, reason: 'auth' } };
    if (!gate(user.uid)) return { status: 429, body: { ok: false, reason: 'quota', scope: 'minute', resetAt: (Math.floor(deps.now() / 60000) + 1) * 60000 } };
    let routes = null; try { routes = await deps.loadRoutes(); } catch (e) { deps.log && deps.log('flightRoutesLoad', { err: String(e && e.message || e).slice(0, 80) }); }
    if (!core || (routes && core.__routes !== routes)) { core = makeFreeFlight({ now: deps.now, fetch: deps.fetch, db: deps.db, env: coreEnv, verify: async (r) => r.__user, log: deps.log, routes }); core.__routes = routes; }
    // 인증은 위에서 끝냄 → 안쪽 handle 은 그 사용자를 그대로 씀
    const out = await core.handle({ body: req.body, __user: user }).then((r) => r, () => ({ status: 200, body: { ok: false, reason: 'upstream' } }));
    // 앱이 처음 보는 공항도 지도에 놓을 수 있게 좌표를 붙인다(노선표 coords, CC0).
    const f = out.body && out.body.flight, C = routes && routes.coords;
    if (f && C) { if (C[f.from]) f.fromLL = C[f.from]; if (C[f.to]) f.toLL = C[f.to]; }
    // 107기: 한국 공항 + 출발 D-3~D+6 이면 게이트·터미널·예정/변경시각·지연·결항(flight.live). 실패는 조용히.
    if (out.status === 200 && out.body && out.body.ok && f) {
      const live = await enrich(f, f.date || (req.body && req.body.date));
      if (live) f.live = live;
      // 116기: 공항 안내(flight.airport = depCongestion·arrHall·checkin·gate·carousel·steps[]). 오늘 편만, 실패는 조용히 생략.
      const ap = await airportLive(f, live); if (ap) f.airport = ap;
    }
    return out;
  };
}

function makeFlightFunctions() {
  const { onRequest } = require('firebase-functions/v2/https');
  const { defineBoolean, defineSecret } = require('firebase-functions/params');
  const { logger } = require('firebase-functions');
  const { getFirestore } = require('firebase-admin/firestore');
  const { getStorage } = require('firebase-admin/storage');
  const { makeVerify } = require('./google');
  const REQUIRE_APP_CHECK = defineBoolean('REQUIRE_APP_CHECK', { default: false });
  const DATA_GO_KR_KEY = defineSecret('DATA_GO_KR_KEY');
  let verify = null;
  const loadRoutes = makeRoutesLoader(async () => (await getStorage().bucket(BUCKET).file(ROUTES_PATH).download())[0]);
  let handler = null;
  const opts = { region: REGION, maxInstances: 2, concurrency: 40, timeoutSeconds: 30, memory: '512MiB', cors: true, invoker: 'public', secrets: [DATA_GO_KR_KEY] };
  return {
    flightLookup: onRequest(opts, async (req, res) => {
      if (!handler) {
        verify = verify || makeVerify(REQUIRE_APP_CHECK);
        handler = makeFlightHandler({ verify, db: getFirestore(), fetch: globalThis.fetch, env: (n) => process.env[n] || '', log: (n, o) => logger.info(n, o), now: () => Date.now(), loadRoutes,
          liveKey: () => { try { return DATA_GO_KR_KEY.value() || ''; } catch (_) { return ''; } } });
      }
      const r = await handler(req);
      res.status(r.status).json(r.body);
    }),
  };
}

module.exports = { makeFlightFunctions, makeFlightHandler, makeRoutesLoader, makeMinuteGate, makeLiveEnricher, KR_AIRPORTS, GOV, BUCKET, ROUTES_PATH };
