'use strict';
// 257기(11/20): 대중교통 실시간 도착 — 공식 개방 API 만, 정류장 단위 공유 캐시.
// index.js 끝 한 줄: Object.assign(exports, require('./transit_fn').makeTransitFunctions());
//
// 함수 5개(출처마다 하나 — 키(Secret)가 들어온 것만 이름을 지정해 배포한다. Secret 이 없으면 그 함수는 배포 자체가 안 됨):
//   transitLiveMta  (뉴욕 지하철 GTFS-realtime, 키 없음)          — MTA 약관: "앱이 MTA 서버에서 바로 받게 하지 말고 자기 서버에 받아 두고 내줄 것" → 이 프록시가 그 조건
//   transitLiveTfl  (런던 TfL Unified API, Secret TFL_APP_KEY)   — 피드당 분당 500회 이하, 'Powered by TfL Open Data' 표기
//   transitLiveLta  (싱가포르 LTA DataMall Bus Arrival v3, Secret LTA_ACCOUNT_KEY) — 하루 1,000만 회 이하
//   transitLiveOba  (시애틀 OneBusAway Puget Sound, Secret OBA_KEY)
//   transitLiveIdfm (파리 IDFM PRIM 다음 통과 단위 요청, Secret IDFM_APIKEY) — 새 토큰 하루 1,000회 → 정류장마다 60초 캐시(데이터 갱신 1분)
// 공통: 로그인 사용자만(makeVerify). 앱이 보내는 것은 정류장 근처 좌표(역·정류장 위치)와 노선 이름뿐 — 사용자 위치·집 주소 아님.
// 응답: {ok, src, stop:{id,name,lat,lon,m}, arrivals:[{route,dest,min,at,live}], attr, asof}. 실패는 {ok:false, reason}.
// 키 값은 요청에만 쓰고 로그·응답에 넣지 않는다.
const { makeVerify } = require('./google');
const zlib = require('zlib');

const REGION = 'asia-northeast3';
const isNum = (x) => typeof x === 'number' && Number.isFinite(x);
const str = (v) => (typeof v === 'string' ? v : v == null ? '' : String(v));

function distM(a, b, c, d) {
  const R = 6371000, r = Math.PI / 180;
  const x = (d - b) * r * Math.cos(((a + c) / 2) * r), y = (c - a) * r;
  return Math.round(Math.sqrt(x * x + y * y) * R);
}

// 출처마다 받는 범위(이 상자 밖 좌표는 거절 — 다른 도시 호출로 한도를 쓰지 않게)
const BOX = Object.freeze({
  mta: [40.49, -74.27, 40.92, -73.68],
  tfl: [51.28, -0.52, 51.70, 0.34],
  lta: [1.15, 103.6, 1.48, 104.1],
  oba: [47.0, -122.6, 48.1, -121.9],
  idfm: [48.1, 1.4, 49.25, 3.6],
});
// 268기: 사용자별 바깥 호출 상한(캐시에 없는 요청만 셈) — 좌표를 조금씩 바꿔 캐시를 피하면 한 사람이 출처 하루 한도(IDFM 1,000 등)를 다 쓸 수 있었음.
const USER_MISS_PER_10M = 30;
const TTL = Object.freeze({ mta: 30e3, tfl: 30e3, lta: 30e3, oba: 30e3, idfm: 60e3 });
const ATTR = Object.freeze({
  mta: 'Data: MTA (not endorsed by MTA)',
  tfl: 'Powered by TfL Open Data',
  lta: 'Data: Land Transport Authority DataMall',
  oba: 'Data: OneBusAway Puget Sound / Sound Transit',
  idfm: 'Données: Île-de-France Mobilités (PRIM), ODbL',
});

function vBody(src, b) {
  if (!b || typeof b !== 'object') return { bad: 'body' };
  const lat = Number(b.lat), lon = Number(b.lon);
  if (!isNum(lat) || !isNum(lon)) return { bad: 'coord' };
  const x = BOX[src];
  if (!x || lat < x[0] || lat > x[2] || lon < x[1] || lon > x[3]) return { bad: 'outside' };
  const line = str(b.line).slice(0, 40), mode = str(b.mode).slice(0, 10);
  return { v: { lat: Math.round(lat * 1e4) / 1e4, lon: Math.round(lon * 1e4) / 1e4, line, mode } };
}

// ---------- 아주 작은 protobuf 읽기(GTFS-realtime 에서 쓰는 칸만) ----------
function pbFields(buf) {
  const out = [];
  let i = 0;
  const varint = () => { let r = 0, s = 0, b; do { b = buf[i++]; r += (b & 0x7f) * Math.pow(2, s); s += 7; } while (b & 0x80 && i < buf.length); return r; };
  while (i < buf.length) {
    const key = varint(), f = Math.floor(key / 8), w = key & 7;
    if (w === 0) out.push([f, varint()]);
    else if (w === 2) { const n = varint(); out.push([f, buf.subarray(i, i + n)]); i += n; }
    else if (w === 1) { out.push([f, null]); i += 8; }
    else if (w === 5) { out.push([f, null]); i += 4; }
    else break;
  }
  return out;
}
const pbStr = (b) => Buffer.from(b).toString('utf8');
/** FeedMessage → [{route, trip, stops:[{stop, t}]}] (trip_update 만). */
function gtfsRtTrips(buf) {
  const trips = [];
  for (const [f, ent] of pbFields(buf)) {
    if (f !== 2 || !ent) continue; // entity
    for (const [ef, tu] of pbFields(ent)) {
      if (ef !== 3 || !tu) continue; // trip_update
      const t = { route: '', trip: '', stops: [] };
      for (const [tf, v] of pbFields(tu)) {
        if (tf === 1 && v) for (const [df, dv] of pbFields(v)) { if (df === 5 && dv) t.route = pbStr(dv); if (df === 1 && dv) t.trip = pbStr(dv); }
        if (tf === 2 && v) {
          const s = { stop: '', t: 0 };
          for (const [sf, sv] of pbFields(v)) {
            if (sf === 4 && sv) s.stop = pbStr(sv);
            if ((sf === 2 || sf === 3) && sv) for (const [xf, xv] of pbFields(sv)) if (xf === 2 && typeof xv === 'number' && !s.t) s.t = xv; // arrival/departure.time
          }
          if (s.stop) t.stops.push(s);
        }
      }
      trips.push(t);
    }
  }
  return trips;
}

// ---------- zip 에서 파일 하나 꺼내기(정적 GTFS stops.txt) ----------
function unzipOne(buf, name) {
  let e = buf.length - 22;
  while (e >= 0 && buf.readUInt32LE(e) !== 0x06054b50) e--;
  if (e < 0) return null;
  let p = buf.readUInt32LE(e + 16);
  const n = buf.readUInt16LE(e + 10);
  for (let k = 0; k < n; k++) {
    const method = buf.readUInt16LE(p + 10), csize = buf.readUInt32LE(p + 20), nl = buf.readUInt16LE(p + 28), xl = buf.readUInt16LE(p + 30), cl = buf.readUInt16LE(p + 32), off = buf.readUInt32LE(p + 42);
    const fn = buf.subarray(p + 46, p + 46 + nl).toString('utf8');
    if (fn === name) {
      const lnl = buf.readUInt16LE(off + 26), lxl = buf.readUInt16LE(off + 28);
      const data = buf.subarray(off + 30 + lnl + lxl, off + 30 + lnl + lxl + csize);
      return method === 0 ? data : zlib.inflateRawSync(data);
    }
    p += 46 + nl + xl + cl;
  }
  return null;
}
function csvRows(text) {
  const lines = text.replace(/^﻿/, '').split(/\r?\n/).filter((l) => l.length);
  const split = (l) => { const r = []; let cur = '', q = false; for (const ch of l) { if (ch === '"') q = !q; else if (ch === ',' && !q) { r.push(cur); cur = ''; } else cur += ch; } r.push(cur); return r; };
  const head = split(lines[0]);
  return lines.slice(1).map((l) => { const c = split(l); const o = {}; head.forEach((h, j) => { o[h] = c[j] || ''; }); return o; });
}

const MTA_FEEDS = ['', '-ace', '-bdfm', '-g', '-jz', '-nqrw', '-l', '-si'].map((s) => `https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs${s}`);
const MTA_STATIC = 'https://rrgtfsfeeds.s3.amazonaws.com/gtfs_subway.zip';

function makeTransitCore(deps) {
  const cache = new Map(); // `${src}:${stop}` → {at, body}
  const misses = new Map(); // uid → [시각…] (인스턴스 메모리)
  const big = new Map(); // 정류장 목록·피드 원본 → {at, v}
  const now = () => deps.now();
  const cget = (m, k, ttl) => { const e = m.get(k); return e && now() - e.at < ttl ? e.v : undefined; };
  const cput = (m, k, v) => { m.set(k, { at: now(), v }); if (m.size > 2000) m.delete(m.keys().next().value); };
  const getJson = async (url, headers = {}) => {
    const r = await deps.fetch(url, { headers: { accept: 'application/json', ...headers } });
    if (r.status === 429) throw Object.assign(new Error('quota'), { reason: 'quota' });
    if (!r.ok) throw Object.assign(new Error('upstream'), { reason: 'upstream', status: r.status });
    return r.json();
  };
  const getBuf = async (url, headers = {}) => {
    const r = await deps.fetch(url, { headers });
    if (!r.ok) throw Object.assign(new Error('upstream'), { reason: 'upstream', status: r.status });
    return Buffer.from(await r.arrayBuffer());
  };
  const minOf = (ms) => Math.max(0, Math.round((ms - now()) / 60000));
  const nearest = (list, lat, lon, maxM = 400) => {
    let best = null;
    for (const s of list) { const m = distM(lat, lon, s.lat, s.lon); if (m <= maxM && (!best || m < best.m)) best = { ...s, m }; }
    return best;
  };

  // ---- 출처별 ----
  const S = {
    async mta(v) {
      let stops = cget(big, 'mta:stops', 864e5);
      if (!stops) {
        const z = await getBuf(MTA_STATIC);
        const t = unzipOne(z, 'stops.txt');
        if (!t) throw Object.assign(new Error('static'), { reason: 'upstream' });
        const rows = csvRows(t.toString('utf8'));
        const names = {};
        for (const r of rows) names[r.stop_id] = r.stop_name;
        stops = { list: rows.filter((r) => r.location_type === '1').map((r) => ({ id: r.stop_id, name: r.stop_name, lat: +r.stop_lat, lon: +r.stop_lon })), names };
        cput(big, 'mta:stops', stops);
      }
      const st = nearest(stops.list, v.lat, v.lon);
      if (!st) return { stop: null, arrivals: [] };
      let trips = cget(big, 'mta:rt', 30e3);
      if (!trips) {
        trips = [];
        for (const u of MTA_FEEDS) { try { trips.push(...gtfsRtTrips(await getBuf(u))); } catch (_) { /* 피드 하나 실패는 건너뜀 */ } }
        cput(big, 'mta:rt', trips);
      }
      const arr = [];
      for (const t of trips) {
        const k = t.stops.findIndex((s) => s.stop.slice(0, -1) === st.id || s.stop === st.id);
        if (k < 0 || !t.stops[k].t) continue;
        const ms = t.stops[k].t * 1000;
        if (ms < now() - 30e3) continue;
        const last = t.stops[t.stops.length - 1];
        arr.push({ route: t.route, dest: stops.names[last.stop] || stops.names[last.stop.slice(0, -1)] || '', min: minOf(ms), at: new Date(ms).toISOString(), live: true });
      }
      return { stop: st, arrivals: arr };
    },
    async tfl(v) {
      const k = encodeURIComponent(deps.key('TFL_APP_KEY'));
      const types = v.mode === 'bus' ? 'NaptanPublicBusCoachTram' : v.mode === 'rail' ? 'NaptanMetroStation,NaptanRailStation' : 'NaptanMetroStation,NaptanRailStation,NaptanPublicBusCoachTram';
      const sp = await getJson(`https://api.tfl.gov.uk/StopPoint/?lat=${v.lat}&lon=${v.lon}&stopTypes=${types}&radius=300&app_key=${k}`);
      const list = (sp && sp.stopPoints) || [];
      const s0 = list[0];
      if (!s0) return { stop: null, arrivals: [] };
      const st = { id: str(s0.naptanId), name: str(s0.commonName), lat: s0.lat, lon: s0.lon, m: Math.round(s0.distance || 0) };
      const a = await getJson(`https://api.tfl.gov.uk/StopPoint/${encodeURIComponent(st.id)}/Arrivals?app_key=${k}`);
      return { stop: st, arrivals: (Array.isArray(a) ? a : []).map((x) => ({ route: str(x.lineName), dest: str(x.destinationName), plat: str(x.platformName), min: Math.max(0, Math.round((x.timeToStation || 0) / 60)), at: str(x.expectedArrival), live: true })) };
    },
    async lta(v) {
      const h = { AccountKey: deps.key('LTA_ACCOUNT_KEY') };
      let stops = cget(big, 'lta:stops', 864e5);
      if (!stops) {
        stops = [];
        for (let skip = 0; skip < 20000; skip += 500) {
          const j = await getJson(`https://datamall2.mytransport.sg/ltaodataservice/BusStops?$skip=${skip}`, h);
          const vals = (j && j.value) || [];
          for (const s of vals) stops.push({ id: str(s.BusStopCode), name: str(s.Description), lat: +s.Latitude, lon: +s.Longitude });
          if (vals.length < 500) break;
        }
        cput(big, 'lta:stops', stops);
      }
      const st = nearest(stops, v.lat, v.lon, 300);
      if (!st) return { stop: null, arrivals: [] };
      const names = Object.fromEntries(stops.map((s) => [s.id, s.name]));
      const j = await getJson(`https://datamall2.mytransport.sg/ltaodataservice/v3/BusArrival?BusStopCode=${encodeURIComponent(st.id)}`, h);
      const arr = [];
      for (const s of (j && j.Services) || []) for (const nb of [s.NextBus, s.NextBus2, s.NextBus3]) {
        const at = nb && Date.parse(nb.EstimatedArrival);
        if (at) arr.push({ route: str(s.ServiceNo), dest: names[str(nb.DestinationCode)] || '', min: minOf(at), at: new Date(at).toISOString(), live: str(nb.Monitored) === '1' || nb.Monitored === 1 });
      }
      return { stop: st, arrivals: arr };
    },
    async oba(v) {
      const k = encodeURIComponent(deps.key('OBA_KEY'));
      const base = 'https://api.pugetsound.onebusaway.org/api/where';
      const sl = await getJson(`${base}/stops-for-location.json?key=${k}&lat=${v.lat}&lon=${v.lon}&radius=300`);
      const list = ((sl && sl.data && (sl.data.list || (sl.data.stops))) || []).map((s) => ({ id: str(s.id), name: str(s.name), lat: +s.lat, lon: +s.lon }));
      const st = nearest(list, v.lat, v.lon, 300);
      if (!st) return { stop: null, arrivals: [] };
      const a = await getJson(`${base}/arrivals-and-departures-for-stop/${encodeURIComponent(st.id)}.json?key=${k}&minutesBefore=0&minutesAfter=60`);
      const ad = (a && a.data && a.data.entry && a.data.entry.arrivalsAndDepartures) || [];
      return { stop: st, arrivals: ad.map((x) => {
        const live = !!x.predicted && x.predictedDepartureTime > 0;
        const ms = live ? x.predictedDepartureTime : x.scheduledDepartureTime;
        return { route: str(x.routeShortName), dest: str(x.tripHeadsign), min: minOf(ms), at: new Date(ms).toISOString(), live };
      }) };
    },
    async idfm(v) {
      const h = { apikey: deps.key('IDFM_APIKEY') };
      const base = 'https://prim.iledefrance-mobilites.fr/marketplace';
      const pn = await getJson(`${base}/v2/navitia/coord/${v.lon};${v.lat}/places_nearby?type[]=stop_area&distance=300&count=1`, h);
      const p = pn && pn.places_nearby && pn.places_nearby[0];
      const sa = p && p.stop_area;
      if (!sa) return { stop: null, arrivals: [] };
      const num = str(sa.id).split(':').pop();
      const st = { id: str(sa.id), name: str(sa.name), lat: +(sa.coord && sa.coord.lat), lon: +(sa.coord && sa.coord.lon), m: Math.round(+p.distance || 0) };
      const sm = await getJson(`${base}/stop-monitoring?MonitoringRef=${encodeURIComponent(`STIF:StopArea:SP:${num}:`)}`, h);
      const del = sm && sm.Siri && sm.Siri.ServiceDelivery && sm.Siri.ServiceDelivery.StopMonitoringDelivery;
      const visits = (del && del[0] && del[0].MonitoredStopVisit) || [];
      const vv = (x) => (Array.isArray(x) ? str(x[0] && x[0].value) : str(x && x.value));
      return { stop: st, arrivals: visits.map((x) => {
        const j = x.MonitoredVehicleJourney || {}, c = j.MonitoredCall || {};
        const exp = Date.parse(c.ExpectedDepartureTime || c.ExpectedArrivalTime || ''), aim = Date.parse(c.AimedDepartureTime || c.AimedArrivalTime || '');
        const ms = exp || aim;
        return ms ? { route: vv(j.PublishedLineName) || vv(j.LineRef), dest: vv(j.DestinationName), min: minOf(ms), at: new Date(ms).toISOString(), live: !!exp } : null;
      }).filter(Boolean) };
    },
  };

  async function handle(src, req) {
    if (req.method !== 'POST') return { status: 405, body: { ok: false, reason: 'method' } };
    let user;
    try { user = await deps.verify(req); } catch (e) { return { status: 401, body: { ok: false, reason: (e && e.reason) || 'auth' } }; }
    const uid = (user && user.uid) || '';
    if (!uid) return { status: 401, body: { ok: false, reason: 'auth' } };
    const { v, bad } = vBody(src, req.body);
    if (bad) return { status: 400, body: { ok: false, reason: bad } };
    const ck = `${src}:${v.lat},${v.lon}:${v.mode}`;
    let body = cget(cache, ck, TTL[src]);
    if (!body) {
      const h = (misses.get(uid) || []).filter((t) => now() - t < 600e3);
      if (h.length >= USER_MISS_PER_10M) return { status: 429, body: { ok: false, src, reason: 'rate' } };
      h.push(now()); misses.set(uid, h);
      if (misses.size > 5000) misses.delete(misses.keys().next().value);
      try {
        const r = await S[src](v);
        body = { ok: !!r.stop, src, ...(r.stop ? { stop: r.stop } : { reason: 'no_stop' }), arrivals: r.arrivals, attr: ATTR[src], asof: new Date(now()).toISOString() };
      } catch (e) {
        deps.log('transit_upstream', { src, reason: e && e.reason, status: e && e.status });
        return { status: 200, body: { ok: false, src, reason: (e && e.reason) || 'upstream' } };
      }
      cput(cache, ck, body);
    }
    const line = v.line.toUpperCase();
    const arrivals = body.arrivals
      .filter((a) => !line || str(a.route).toUpperCase() === line)
      .sort((a, b) => a.min - b.min).slice(0, 12);
    return { status: 200, body: { ...body, arrivals } };
  }
  return { handle, S };
}

const SECRETS = Object.freeze({ mta: null, tfl: 'TFL_APP_KEY', lta: 'LTA_ACCOUNT_KEY', oba: 'OBA_KEY', idfm: 'IDFM_APIKEY' });
const NAMES = Object.freeze({ mta: 'transitLiveMta', tfl: 'transitLiveTfl', lta: 'transitLiveLta', oba: 'transitLiveOba', idfm: 'transitLiveIdfm' });

function makeTransitFunctions({ region = REGION } = {}) {
  const { onRequest } = require('firebase-functions/v2/https');
  const { defineSecret, defineBoolean } = require('firebase-functions/params');
  const { logger } = require('firebase-functions');
  const REQUIRE_APP_CHECK = defineBoolean('REQUIRE_APP_CHECK', { default: false });
  const sec = {};
  for (const [src, n] of Object.entries(SECRETS)) if (n) sec[n] = defineSecret(n);
  const core = makeTransitCore({
    verify: makeVerify(REQUIRE_APP_CHECK), fetch: (u, i) => fetch(u, i), now: () => Date.now(),
    key: (n) => sec[n].value(), log: (n, d) => logger.warn(n, d),
  });
  const opts = { region, maxInstances: 2, concurrency: 40, timeoutSeconds: 20, memory: '512MiB', cors: true };
  const wrap = (src) => async (req, res) => { res.set('cache-control', 'no-store'); try { const r = await core.handle(src, req); res.status(r.status).json(r.body); } catch (_) { res.status(500).json({ ok: false, reason: 'server' }); } };
  const out = {};
  for (const [src, n] of Object.entries(SECRETS)) out[NAMES[src]] = onRequest(n ? { ...opts, secrets: [sec[n]] } : opts, wrap(src));
  return out;
}

module.exports = { USER_MISS_PER_10M, makeTransitFunctions, makeTransitCore, vBody, gtfsRtTrips, pbFields, unzipOne, csvRows, distM, BOX, TTL, ATTR, SECRETS, NAMES };
