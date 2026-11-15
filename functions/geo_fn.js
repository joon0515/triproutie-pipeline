'use strict';
// 132기(11/15): geoLookup — 출발지 주소 검색(엔터·검색 버튼 때만). index.js 끝 한 줄:
//   Object.assign(exports, require('./geo_fn').makeGeoFunctions());
// 경로: 한글 주소 → 행정안전부 도로명주소 검색 API(business.juso.go.kr, Secret JUSO_KEY) + 좌표제공 API(Secret JUSO_COORD_KEY, UTM-K→WGS84)
//       해외·동네 수준·좌표키 없음 → OSM Nominatim 공개 서버(정책: 앱 전체 초당 1회, 자동완성 금지, 대량 금지, 식별 User-Agent, 결과 캐시, ODbL 출처 표시).
// 지키는 방법: 이 함수만 Nominatim 을 부름(maxInstances 1 + 직렬 대기열 1.1초 간격 = 앱 전체 초당 1회 이하), 하루 상한, 사용자 분당·하루 상한,
//   같은 검색어는 서버 캐시 30일(메모리 + Firestore geo_cache). 저장 = 좌표·한 줄 주소·정확도(addr 번지 / road 도로 / area 동네)만 + 문서 출처 1글자.
// 키 값은 요청 주소에만 쓰고 로그·응답·저장 어디에도 안 나감. 카카오·네이버·Google 은 쓰지 않음.
const crypto = require('crypto');
// 132기 2차: 좌표제공 키(JUSO_COORD_KEY) 바인딩 여부 = 같은 폴더 geo_cfg.json {"coord":true|false} (s132_deploy.sh 가 Secret 유무로 써 줌).
// 파일 없으면 coord:false(바인딩 안 함 → Secret 이 없어도 배포 성공). 키가 생기면 s132_deploy.sh 재실행만으로 coord:true 판이 배포됨.
const CFG = (() => { try { const c = require('./geo_cfg.json'); return { coord: c && c.coord === true }; } catch (_) { return { coord: false }; } })();

const REGION = 'asia-northeast3';
const TTL_MS = 30 * 864e5;
const TTL_NOCOORD_MS = 864e5; // '좌표 없음' 결과는 하루만(키·Nominatim 이 나중에 채울 수 있게)
const JUSO = { search: 'https://business.juso.go.kr/addrlink/addrLinkApi.do', coord: 'https://business.juso.go.kr/addrlink/addrCoordApi.do' };
const NOMI = 'https://nominatim.openstreetmap.org/search';
const LIM = { userPerMin: 10, userPerDay: 120, nomiGapMs: 1100, nomiMaxWaitMs: 4500, nomiPerDay: 2500, jusoPerDay: 5000, timeoutMs: 5000, memMax: 2000, top: 5 };
const CREDIT = { j: '행정안전부 도로명주소 (business.juso.go.kr)', o: '© OpenStreetMap contributors (ODbL)' };

const s = (v) => (v === undefined || v === null ? '' : String(v).trim());
const realKey = (k) => (k && k !== 'pending' ? k : ''); // 승인 전 자리값 'pending' = 키 없음(해외·동네는 Nominatim 으로)
const r5 = (x) => Math.round(x * 1e5) / 1e5;
const normQ = (q) => s(q).normalize('NFC').replace(/\s+/g, ' ').slice(0, 120);
const hasHangul = (q) => /[가-힣]/.test(q);
// 정확도 짐작(검색어 기준): 건물번호·번지 → addr, 도로명(로·길)만 → road, 동·읍·면·리만 → area
function qLevel(q) {
  if (/(로|길)\s*\d+(-\d+)?/.test(q) || /\d+(-\d+)?\s*(번지)?\s*$/.test(q) && /(동|리|가)\s*\d/.test(q)) return 'addr';
  if (/\S(로|길)(\s|$)/.test(q) || /\S(대로|번길)/.test(q)) return 'road';
  return 'area';
}

// ---- UTM-K(EPSG:5179, GRS80, 중앙자오선 127.5, 원점위도 38, k0 0.9996, FE 1,000,000, FN 2,000,000) ↔ WGS84
const GRS = { a: 6378137, f: 1 / 298.257222101 };
const UTMK = { lon0: 127.5, lat0: 38, k0: 0.9996, fe: 1e6, fn: 2e6 };
const rad = (d) => (d * Math.PI) / 180, deg = (r) => (r * 180) / Math.PI;
function _m(phi, e2) {
  const e4 = e2 * e2, e6 = e4 * e2;
  return GRS.a * ((1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * Math.sin(2 * phi)
    + (15 * e4 / 256 + 45 * e6 / 1024) * Math.sin(4 * phi) - (35 * e6 / 3072) * Math.sin(6 * phi));
}
function wgsToUtmk(lat, lon) {
  const e2 = GRS.f * (2 - GRS.f), ep2 = e2 / (1 - e2), phi = rad(lat), lam = rad(lon - UTMK.lon0);
  const N = GRS.a / Math.sqrt(1 - e2 * Math.sin(phi) ** 2), T = Math.tan(phi) ** 2, C = ep2 * Math.cos(phi) ** 2, A = lam * Math.cos(phi);
  const M = _m(phi, e2), M0 = _m(rad(UTMK.lat0), e2);
  const x = UTMK.fe + UTMK.k0 * N * (A + (1 - T + C) * A ** 3 / 6 + (5 - 18 * T + T * T + 72 * C - 58 * ep2) * A ** 5 / 120);
  const y = UTMK.fn + UTMK.k0 * (M - M0 + N * Math.tan(phi) * (A * A / 2 + (5 - T + 9 * C + 4 * C * C) * A ** 4 / 24 + (61 - 58 * T + T * T + 600 * C - 330 * ep2) * A ** 6 / 720));
  return { x, y };
}
function utmkToWgs(x, y) {
  const e2 = GRS.f * (2 - GRS.f), ep2 = e2 / (1 - e2), e1 = (1 - Math.sqrt(1 - e2)) / (1 + Math.sqrt(1 - e2));
  const M = _m(rad(UTMK.lat0), e2) + (y - UTMK.fn) / UTMK.k0;
  const mu = M / (GRS.a * (1 - e2 / 4 - 3 * e2 * e2 / 64 - 5 * e2 ** 3 / 256));
  const p1 = mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * Math.sin(2 * mu) + (21 * e1 * e1 / 16 - 55 * e1 ** 4 / 32) * Math.sin(4 * mu)
    + (151 * e1 ** 3 / 96) * Math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * Math.sin(8 * mu);
  const N1 = GRS.a / Math.sqrt(1 - e2 * Math.sin(p1) ** 2), T1 = Math.tan(p1) ** 2, C1 = ep2 * Math.cos(p1) ** 2;
  const R1 = GRS.a * (1 - e2) / (1 - e2 * Math.sin(p1) ** 2) ** 1.5, D = (x - UTMK.fe) / (N1 * UTMK.k0);
  const lat = p1 - (N1 * Math.tan(p1) / R1) * (D * D / 2 - (5 + 3 * T1 + 10 * C1 - 4 * C1 * C1 - 9 * ep2) * D ** 4 / 24
    + (61 + 90 * T1 + 298 * C1 + 45 * T1 * T1 - 252 * ep2 - 3 * C1 * C1) * D ** 6 / 720);
  const lon = (D - (1 + 2 * T1 + C1) * D ** 3 / 6 + (5 - 2 * C1 + 28 * T1 - 3 * C1 * C1 + 8 * ep2 + 24 * T1 * T1) * D ** 5 / 120) / Math.cos(p1);
  return { lat: deg(lat), lon: UTMK.lon0 + deg(lon) };
}

// ---- 한도
function makeGate(now) {
  const min = new Map(), day = new Map();
  return (uid) => {
    const t = now(), m = Math.floor(t / 60000), d = Math.floor((t + 9 * 3600e3) / 864e5);
    if (min.size > 5000) min.clear(); if (day.size > 20000) day.clear();
    const a = min.get(uid), b = day.get(uid);
    if (a && a.m === m && a.n >= LIM.userPerMin) return 'minute';
    if (b && b.d === d && b.n >= LIM.userPerDay) return 'day';
    min.set(uid, a && a.m === m ? { m, n: a.n + 1 } : { m, n: 1 });
    day.set(uid, b && b.d === d ? { d, n: b.n + 1 } : { d, n: 1 });
    return '';
  };
}
function makeDayCounter(now, cap) {
  let d = -1, n = 0;
  return () => { const k = Math.floor((now() + 9 * 3600e3) / 864e5); if (k !== d) { d = k; n = 0; } if (n >= cap) return false; n++; return true; };
}
// 앱 전체 초당 1회: 직렬 대기열(이 인스턴스 하나뿐 — maxInstances 1). 기다림이 길면 'busy'.
function makeSerialGate(now, sleep, gapMs = LIM.nomiGapMs, maxWaitMs = LIM.nomiMaxWaitMs) {
  let next = 0;
  return async () => {
    const t = now(), at = Math.max(t, next);
    if (at - t > maxWaitMs) return false;
    next = at + gapMs;
    if (at > t) await sleep(at - t);
    return true;
  };
}

// 136기: 실패 이유(err 객체에 http 상태·오류 이름·원인 코드)를 남김 — 키·URL 은 남기지 않음
async function getJson(fetch, url, headers, timeoutMs, err) {
  const ac = typeof AbortController === 'function' ? new AbortController() : null;
  const to = ac ? setTimeout(() => ac.abort(), timeoutMs) : null;
  try {
    const r = await fetch(url, { headers, signal: ac ? ac.signal : undefined });
    if (!r || !r.ok) { if (err) err.http = r ? r.status : 0; return null; }
    return await r.json();
  } catch (e) { if (err) { err.name = String((e && e.name) || 'Error').slice(0, 24); err.cause = String((e && e.cause && e.cause.code) || '').slice(0, 32); } return null; } finally { if (to) clearTimeout(to); }
}

// deps: { verify, fetch, now, sleep, log, jusoKey(), coordKey(), userAgent(), cache:{get(k),set(k,v)}? }
function makeGeoHandler(deps) {
  const gate = makeGate(deps.now), nomiDay = makeDayCounter(deps.now, LIM.nomiPerDay), jusoDay = makeDayCounter(deps.now, LIM.jusoPerDay);
  const serial = makeSerialGate(deps.now, deps.sleep || ((ms) => new Promise((r) => setTimeout(r, ms))));
  const mem = new Map(), inflight = new Map();
  const T = LIM.timeoutMs;
  const ua = () => s(deps.userAgent && deps.userAgent()) || 'TripRoutie-geoLookup/1.0';

  async function juso(q) {
    const key = realKey(s(deps.jusoKey && deps.jusoKey()));
    if (!key || !jusoDay()) return null;
    const u = `${JUSO.search}?${new URLSearchParams({ confmKey: key, currentPage: '1', countPerPage: String(LIM.top), keyword: q, resultType: 'json' })}`;
    const err = {};
    const j = await getJson(deps.fetch, u, {}, T, err);
    const c = j && j.results && j.results.common;
    if (!c || s(c.errorCode) !== '0') { deps.log && deps.log('geoJuso', { code: s(c && c.errorCode).slice(0, 8), http: err.http || 0, err: err.name || '', cause: err.cause || '' }); return null; }
    return Array.isArray(j.results.juso) ? j.results.juso : [];
  }
  async function jusoCoord(row) {
    const key = realKey(s(deps.coordKey && deps.coordKey()));
    if (!key || !jusoDay()) return null;
    const p = { confmKey: key, admCd: s(row.admCd), rnMgtSn: s(row.rnMgtSn), udrtYn: s(row.udrtYn) || '0', buldMnnm: s(row.buldMnnm), buldSlno: s(row.buldSlno) || '0', resultType: 'json' };
    const j = await getJson(deps.fetch, `${JUSO.coord}?${new URLSearchParams(p)}`, {}, T);
    const it = j && j.results && Array.isArray(j.results.juso) && j.results.juso[0];
    const x = it && +it.entX, y = it && +it.entY;
    if (!(x > 7e5 && x < 1.4e6 && y > 1.4e6 && y < 2.3e6)) return null;
    const w = utmkToWgs(x, y);
    return w.lat > 32 && w.lat < 39.5 && w.lon > 124 && w.lon < 132 ? w : null;
  }
  async function nomi(q, lang, kr) {
    if (!nomiDay()) return null;
    if (!(await serial())) return 'busy';
    const p = { q, format: 'jsonv2', limit: String(LIM.top), addressdetails: '0', 'accept-language': lang || 'en' };
    if (kr) p.countrycodes = 'kr';
    const j = await getJson(deps.fetch, `${NOMI}?${new URLSearchParams(p)}`, { 'User-Agent': ua(), Accept: 'application/json' }, T);
    if (!Array.isArray(j)) return null;
    return j.map((h) => {
      const lat = +h.lat, lon = +h.lon; if (!isFinite(lat) || !isFinite(lon)) return null;
      const t = s(h.addresstype || h.type), cat = s(h.category || h.class);
      const acc = /^(house|building|house_number)$/.test(t) || cat === 'building' ? 'addr' : (cat === 'highway' || /^(road|street|residential)$/.test(t)) ? 'road' : 'area';
      const line = s(h.display_name).split(',').map(s).filter(Boolean).slice(0, 5).join(', ');
      return line ? { lat: r5(lat), lon: r5(lon), line, acc } : null;
    }).filter(Boolean);
  }

  async function resolve(q, lang) {
    if (hasHangul(q)) {
      const lv = qLevel(q);
      let rows = null;
      if (lv !== 'area') rows = await juso(q);
      if (rows && rows.length) {
        const out = [];
        for (const row of rows.slice(0, 3)) {
          const w = await jusoCoord(row);
          if (w) out.push({ lat: r5(w.lat), lon: r5(w.lon), line: s(row.roadAddrPart1 || row.roadAddr), acc: lv });
        }
        if (out.length) return { src: 'j', r: out };
        // 좌표키 없음·실패 → 정리된 도로명주소로 Nominatim 1회
        const n = await nomi(s(rows[0].roadAddrPart1 || rows[0].roadAddr), lang, true);
        if (n === 'busy') return { busy: true };
        if (n && n.length) return { src: 'o', r: n.slice(0, 1).map((x) => ({ ...x, line: s(rows[0].roadAddrPart1 || rows[0].roadAddr), acc: x.acc === 'addr' ? 'addr' : lv === 'addr' ? 'road' : x.acc })) };
        // 그것도 실패 → 도로명주소(법정동 포함) 줄만, 좌표 없음(lat/lon null) → 앱이 지도 핀 확인 단계로. 지명 사전 수준 좌표로 대신하지 않음.
        return { src: 'j', r: rows.slice(0, 3).map((row) => ({ lat: null, lon: null, line: s(row.roadAddrPart1 || row.roadAddr), acc: lv })).filter((x) => x.line) };
      }
      const n = await nomi(q, lang, true);
      if (n === 'busy') return { busy: true };
      if (n && n.length) return { src: 'o', r: n };
      // 동네 수준인데 Nominatim 이 없으면 도로명주소 첫 건(그 동네 안 건물) 좌표를 '동네'로
      if (lv === 'area') {
        const rr = await juso(q); const w = rr && rr[0] && (await jusoCoord(rr[0]));
        if (w) return { src: 'j', r: [{ lat: r5(w.lat), lon: r5(w.lon), line: [s(rr[0].siNm), s(rr[0].sggNm), s(rr[0].emdNm)].filter(Boolean).join(' '), acc: 'area' }] };
      }
      return { src: '', r: [] };
    }
    const n = await nomi(q, lang, false);
    if (n === 'busy') return { busy: true };
    return { src: n && n.length ? 'o' : '', r: n || [] };
  }

  return async (req) => {
    if (req.method && req.method !== 'POST') return { status: 405, body: { ok: false, reason: 'method' } };
    let user; try { user = await deps.verify(req); } catch (_) { return { status: 401, body: { ok: false, reason: 'auth' } }; }
    if (!user || !user.uid) return { status: 401, body: { ok: false, reason: 'auth' } };
    const b = req.body || {}, q = normQ(b.q), lang = /^(ko|en|ja|zh|zh-TW|zh_Hant)$/.test(s(b.lang)) ? s(b.lang).replace('zh_Hant', 'zh-TW') : 'en';
    if (q.length < 2) return { status: 400, body: { ok: false, reason: 'q' } };
    const lim = gate(user.uid);
    if (lim) return { status: 429, body: { ok: false, reason: 'quota', scope: lim } };
    const coordOn = !!realKey(s(deps.coordKey && deps.coordKey()));
    const ck = crypto.createHash('sha256').update(q.toLowerCase() + '|' + lang + '|c' + (coordOn ? 1 : 0)).digest('hex').slice(0, 40); // 좌표키가 생기면 새 캐시
    const t = deps.now(), hit = mem.get(ck);
    const reply = (v, cached) => ({ status: 200, body: { ok: true, results: v.r, credit: v.src ? CREDIT[v.src] : '', cached } });
    const ttl = (v) => (v.r.some((x) => x.lat === null) ? TTL_NOCOORD_MS : TTL_MS);
    if (hit && t - hit.at < ttl(hit.v)) return reply(hit.v, true);
    if (deps.cache) { try { const d = await deps.cache.get(ck); if (d && d.at && Array.isArray(d.r) && t - d.at < ttl(d)) { mem.set(ck, { at: d.at, v: d }); return reply(d, true); } } catch (_) {} }
    let p = inflight.get(ck);
    if (!p) { p = resolve(q, lang).finally(() => inflight.delete(ck)); inflight.set(ck, p); }
    let v; try { v = await p; } catch (_) { v = { src: '', r: [] }; }
    if (v.busy) return { status: 200, body: { ok: false, reason: 'busy', retryMs: LIM.nomiGapMs } };
    const keep = { at: t, src: v.src, r: v.r.map((x) => ({ lat: x.lat, lon: x.lon, line: x.line, acc: x.acc })) };
    if (keep.r.length) {
      if (mem.size >= LIM.memMax) mem.delete(mem.keys().next().value);
      mem.set(ck, { at: t, v: keep });
      if (deps.cache) { try { await deps.cache.set(ck, keep); } catch (_) {} }
    }
    deps.log && deps.log('geoLookup', { n: keep.r.length, src: keep.src, kr: hasHangul(q) });
    return reply(keep, false);
  };
}

function makeGeoFunctions() {
  const { onRequest } = require('firebase-functions/v2/https');
  const { defineBoolean, defineSecret, defineString } = require('firebase-functions/params');
  const { logger } = require('firebase-functions');
  const { getFirestore } = require('firebase-admin/firestore');
  const { makeVerify } = require('./google');
  const REQUIRE_APP_CHECK = defineBoolean('REQUIRE_APP_CHECK', { default: false });
  const JUSO_KEY = defineSecret('JUSO_KEY');
  const JUSO_COORD_KEY = CFG.coord ? defineSecret('JUSO_COORD_KEY') : null; // 없으면 바인딩 안 함
  // Nominatim 정책: 앱을 알아볼 수 있는 User-Agent(연락처 URL 포함). 대표 이메일은 넣지 않음 — 앱 소개/문의 페이지 URL.
  const GEO_UA = defineString('GEO_UA', { default: 'TripRoutie-geoLookup/1.0' });
  const sv = (x) => { try { return x.value() || ''; } catch (_) { return ''; } };
  let handler = null;
  const opts = { region: REGION, maxInstances: 1, concurrency: 20, timeoutSeconds: 20, memory: '256MiB', cors: true, invoker: 'public', secrets: [JUSO_KEY, ...(JUSO_COORD_KEY ? [JUSO_COORD_KEY] : [])] };
  return {
    geoLookup: onRequest(opts, async (req, res) => {
      if (!handler) {
        const db = getFirestore();
        handler = makeGeoHandler({
          verify: makeVerify(REQUIRE_APP_CHECK), fetch: globalThis.fetch, now: () => Date.now(), log: (n, o) => logger.info(n, o),
          jusoKey: () => sv(JUSO_KEY), coordKey: () => (JUSO_COORD_KEY ? sv(JUSO_COORD_KEY) : ''), userAgent: () => sv(GEO_UA),
          cache: { get: async (k) => { const d = await db.doc(`geo_cache/${k}`).get(); return d.exists ? d.data() : null; }, set: (k, v) => db.doc(`geo_cache/${k}`).set({ ...v, exp: new Date(v.at + (v.r.some((x) => x.lat === null) ? TTL_NOCOORD_MS : TTL_MS)) }) },
        });
      }
      const r = await handler(req);
      res.status(r.status).json(r.body);
    }),
  };
}

module.exports = { CFG, makeGeoFunctions, makeGeoHandler, makeSerialGate, utmkToWgs, wgsToUtmk, qLevel, LIM, JUSO, NOMI };
