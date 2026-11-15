'use strict';
// W_airport 서버 (116기, 11/15) — flightLookup 응답에 flight.airport 를 붙인다. 명세는 data.go.kr 각 데이터 페이지 본문(swagger)으로 확정.
//   depCongestion  인천 출국장 혼잡도  T1 15148225 /getDepartureCongestion  ·  T2 15161098 /getDepartureCongestionT2
//   arrHall        인천 입국장현황 15095061 /getArrivalsCongestion (현재 ±2시간 편, 편명별 입국장·대기 인원)
//   checkin/gate   인천 수하물 적재대(출발) 15128793 /getDeparturesBaggage — 출발편 chkinRange·gateNumber (107기 live 에 없을 때만 보충)
//   carousel       도착 수취대 = 107기 live.carousel 을 그대로 옮김(적재대 API 는 출발용이라 수취대 정보 없음)
//   steps[]        한국공항공사 공항 소요시간 15158950 /v1(GMP·CJU) /v2(PUS·TAE·CJJ), 구간 평균(초→분)
// 규칙: 오늘(KST) 편만 · 키 없음/실패/한도/모양 다름 → 그 칸 생략(throw 없음) · 캐시 혼잡 5분, 시설(카운터·게이트) 1일 · API별 하루 한도(인스턴스당).
// 키(Secret DATA_GO_KR_KEY)는 요청 주소에만. 로그에는 svc 이름·오류 종류만.

const GOV_AP = {
  hallT1: 'https://apis.data.go.kr/B551177/statusOfDepartureCongestion/getDepartureCongestion',
  hallT2: 'https://apis.data.go.kr/B551177/statusOfDepartureCongestionT2/getDepartureCongestionT2',
  arrHall: 'https://apis.data.go.kr/B551177/StatusOfArrivals/getArrivalsCongestion',
  lateral: 'https://apis.data.go.kr/B551177/StatusOfBaggageLateral/getDeparturesBaggage',
  kacV1: 'https://apis.data.go.kr/B551178/airport-process-time/v1',
  kacV2: 'https://apis.data.go.kr/B551178/airport-process-time/v2',
};
const TTL = { live: 5 * 60e3, facility: 864e5 };
// 개발계정 한도: 출국장·적재대 1,000/일, 입국장 500/일. maxInstances 2 → 인스턴스당 절반 아래.
const PER_DAY = { hallT1: 400, hallT2: 400, arrHall: 200, lateral: 400, kacV1: 400, kacV2: 400 };
const KAC_V1 = new Set(['GMP', 'CJU']), KAC_V2 = new Set(['PUS', 'TAE', 'CJJ']);
const STEP_KEYS = [['A', 'checkin'], ['B', 'id'], ['C', 'security'], ['D', 'boarding']]; // 1구간 체크인→신분확인, 2 신분확인→보안검색, 3 보안검색→탑승, 4 탑승→출발
const CREDIT = { icn: '인천국제공항공사 (공공데이터포털)', kac: '한국공항공사 (공공데이터포털)' };

const s = (v) => (v === undefined || v === null ? '' : String(v).trim());
const int = (v) => { const n = Number(s(v)); return s(v) !== '' && Number.isFinite(n) && n >= 0 ? Math.round(n) : null; };
const canon = (no) => s(no).toUpperCase().replace(/\s/g, '').replace(/^([A-Z0-9]{2})0+(?=\d)/, '$1');
const kstDay = (ms) => new Date(ms + 9 * 3600e3).toISOString().slice(0, 10);
function itemsOf(j) {
  const b = j && j.response && j.response.body; let it = b ? b.items : (j && j.items);
  if (it && it.item !== undefined) it = it.item;
  if (!it || it === '') return [];
  return Array.isArray(it) ? it : [it];
}

// deps: { fetch, key: () => string, now: () => ms, log? }
function makeAirportLive(deps, opt = {}) {
  const lim = { ...PER_DAY, ...(opt.perDay || {}) }, EP = { ...GOV_AP, ...(opt.endpoint || {}) };
  const cache = new Map(), inflight = new Map(); let day = { d: '' }; const off = {};
  const log = (n, o) => { try { deps.log && deps.log(n, o); } catch (_) {} };
  const nextMidnight = (ms) => Date.parse(kstDay(ms) + 'T00:00:00Z') - 9 * 3600e3 + 864e5;

  async function call(svc, params, nowMs) {
    let k = ''; try { k = s(deps.key()); } catch (_) { k = ''; }
    if (!k || (off[svc] || 0) > nowMs) return null;
    if (day.d !== kstDay(nowMs)) day = { d: kstDay(nowMs) };
    if ((day[svc] || 0) >= lim[svc]) return null;
    day[svc] = (day[svc] || 0) + 1;
    const u = new URL(EP[svc]); u.searchParams.set('serviceKey', k);
    for (const [a, b] of Object.entries({ pageNo: '1', type: 'json', ...params })) u.searchParams.set(a, b);
    let text = '', status = 0;
    try {
      const r = await deps.fetch(u.toString(), { headers: { accept: 'application/json' }, signal: AbortSignal.timeout(4000) });
      status = r.status; text = await r.text();
    } catch (_) { log('airportLive', { svc, err: 'net' }); return null; }
    if (/LIMITED_NUMBER_OF_SERVICE_REQUESTS|<returnReasonCode>22</.test(text) || status === 429) { off[svc] = nextMidnight(nowMs); log('airportLive', { svc, err: 'limit' }); return null; }
    if (/SERVICE_KEY_IS_NOT_REGISTERED|SERVICE_ACCESS_DENIED|<returnReasonCode>(20|30|31|32)</.test(text) || status === 401 || status === 403) { off[svc] = nowMs + 3600e3; log('airportLive', { svc, err: 'key' }); return null; }
    if (status < 200 || status >= 300) { log('airportLive', { svc, err: 'http' + status }); return null; }
    let j; try { j = JSON.parse(text); } catch (_) { log('airportLive', { svc, err: 'shape' }); return null; }
    const h = j && j.response && j.response.header, c = s(h && h.resultCode);
    if (c && c !== '00' && c !== '0' && c !== '03') { if (c === '22') off[svc] = nextMidnight(nowMs); log('airportLive', { svc, err: 'code' + c.slice(0, 4) }); return null; }
    return itemsOf(j);
  }
  // 같은 키는 ttl 동안 다시 부르지 않음(실패도 캐시 → 한도 보호). 동시에 같은 요청은 하나로.
  async function get(svc, params, ttl, nowMs, tag = '') {
    const ck = svc + tag + JSON.stringify(params), c = cache.get(ck);
    if (c && nowMs - c.at < ttl) return c.v;
    if (inflight.has(ck)) return inflight.get(ck);
    const p = call(svc, params, nowMs).catch(() => null).then((v) => {
      inflight.delete(ck);
      if (cache.size > 400) cache.delete(cache.keys().next().value);
      cache.set(ck, { at: nowMs, v }); return v;
    });
    inflight.set(ck, p); return p;
  }

  async function depCongestion(t2, nowMs) {
    const rows = await get(t2 ? 'hallT2' : 'hallT1', t2 ? { numOfRows: '50' } : { numOfRows: '50', terminalId: 'P01' }, TTL.live, nowMs);
    if (!rows || !rows.length) return null;
    const by = new Map(); let at = '';
    for (const r of rows) {
      const m = /^DG(\d)_?([A-Z]*)$/.exec(s(r.gateId).toUpperCase()); if (!m) continue;
      const people = int(r.waitLength), min = int(r.waitTime); if (people === null && min === null) continue;
      const h = by.get(m[1]) || { id: m[1], wait: 0, waitMin: null, parts: [] };
      if (people !== null) h.wait += people;
      if (min !== null) h.waitMin = Math.max(h.waitMin || 0, min);
      h.parts.push(m[2] || ''); by.set(m[1], h);
      if (s(r.occurtime) > at) at = s(r.occurtime);
    }
    const halls = [...by.values()].sort((a, b) => a.id.localeCompare(b.id)).map((h) => { const o = { id: h.id, wait: h.wait }; if (h.waitMin !== null) o.waitMin = h.waitMin; return o; });
    return halls.length ? { terminal: t2 ? 'T2' : 'T1', halls, ...(at ? { occurred: at } : {}) } : null;
  }
  async function arrHall(no, t2, nowMs) {
    const rows = await get('arrHall', { numOfRows: '500', terno: t2 ? 'T2' : 'T1' }, TTL.live, nowMs);
    const r = rows && rows.find((x) => canon(x.flightid) === canon(no));
    const gate = r && s(r.entrygate).toUpperCase();
    if (!gate) return null;
    const ko = int(r.korean), fo = int(r.foreigner);
    const o = { gate }; if (ko !== null || fo !== null) { o.wait = (ko || 0) + (fo || 0); if (ko !== null) o.korean = ko; if (fo !== null) o.foreigner = fo; }
    return o;
  }
  async function lateral(no, date, nowMs) {
    const rows = await get('lateral', { numOfRows: '10', flightId: canon(no), searchDay: '0', lang: 'K' }, TTL.facility, nowMs, date);
    const ymd = date.replace(/-/g, '');
    const r = rows && rows.find((x) => canon(x.flightId) === canon(no) && (!s(x.scheduleDatetime) || s(x.scheduleDatetime).startsWith(ymd)));
    if (!r) return null;
    const o = {}; if (s(r.chkinRange)) o.checkin = s(r.chkinRange); if (s(r.gateNumber)) o.gate = s(r.gateNumber);
    return Object.keys(o).length ? o : null;
  }
  async function steps(ap, nowMs) {
    const svc = KAC_V1.has(ap) ? 'kacV1' : 'kacV2';
    const rows = await get(svc, { numOfRows: '20' }, TTL.live, nowMs);
    const r = rows && rows.find((x) => s(x.IATA_APCD).toUpperCase() === ap && s(x.OPR_STS_CD) !== '0');
    if (!r) return null;
    const list = STEP_KEYS.map(([c, key], i) => { const sec = int(r['STY_TCT_AVG_' + c]); return sec === null ? null : { seg: i + 1, key, min: Math.round(sec / 60) }; }).filter(Boolean);
    if (!list.length) return null;
    const all = int(r.STY_TCT_AVG_ALL);
    return { steps: list, ...(all !== null ? { stepsTotalMin: Math.round(all / 60) } : {}) };
  }

  // f = flight{no, from, to, date}, live = 107기 flight.live(없어도 됨). 결과 flight.airport 또는 null. throw 하지 않음.
  return async function airport(f, live) {
    try {
      const nowMs = deps.now(), date = s(f && f.date);
      if (!f || !f.no || date !== kstDay(nowMs)) return null; // 실시간 자료라 오늘 편만
      const L = live || {}, t2 = /T2/.test(s(L.terminal)), out = {};
      let icn = false, kac = false;
      if (f.from === 'ICN') {
        const [dc, lt] = await Promise.all([depCongestion(t2, nowMs), (!s(L.checkin) || !s(L.gate)) ? lateral(f.no, date, nowMs) : null]);
        if (dc) { out.depCongestion = dc; icn = true; }
        if (lt) { if (!s(L.checkin) && lt.checkin) out.checkin = lt.checkin; if (!s(L.gate) && lt.gate) out.gate = lt.gate; if (out.checkin || out.gate) icn = true; }
      } else if (f.to === 'ICN') {
        const ah = await arrHall(f.no, t2, nowMs);
        if (ah) { out.arrHall = ah; icn = true; }
        if (s(L.carousel)) out.carousel = s(L.carousel);
      } else if (KAC_V1.has(f.from) || KAC_V2.has(f.from)) {
        const st = await steps(f.from, nowMs);
        if (st) { Object.assign(out, st); kac = true; }
      }
      if (f.to !== 'ICN' && L.kind === 'arr' && s(L.carousel)) out.carousel = s(L.carousel);
      if (!Object.keys(out).length) return null;
      out.at = nowMs; out.credit = icn ? CREDIT.icn : kac ? CREDIT.kac : s(L.credit) || undefined;
      if (!out.credit) delete out.credit;
      return out;
    } catch (_) { return null; }
  };
}

module.exports = { makeAirportLive, GOV_AP, PER_DAY, TTL };
