'use strict';
// node test_geo_fn.js — 외부 호출 0(가짜 fetch). 가짜 키만 씀.
const { makeGeoHandler, makeSerialGate, utmkToWgs, wgsToUtmk, qLevel, LIM } = require('./geo_fn');
let pass = 0, fail = 0;
const ok = (c, m) => { if (c) pass++; else { fail++; console.log('FAIL', m); } };

(async () => {
  // 좌표 변환: 왕복 + 알려진 점(서울시청 WGS84 37.5663,126.9779 → UTM-K 약 953,900 / 1,952,000)
  for (const [la, lo] of [[37.5663, 126.9779], [35.1631, 129.1636], [33.4996, 126.5312], [38.2, 128.6]]) {
    const u = wgsToUtmk(la, lo), w = utmkToWgs(u.x, u.y);
    ok(Math.abs(w.lat - la) < 1e-6 && Math.abs(w.lon - lo) < 1e-6, `roundtrip ${la},${lo} → ${JSON.stringify(w)}`);
  }
  const ch = wgsToUtmk(37.5663, 126.9779);
  ok(Math.abs(ch.x - 953900) < 600 && Math.abs(ch.y - 1952000) < 600, 'cityhall utmk ' + JSON.stringify(ch));
  const c0 = utmkToWgs(1e6, 2e6); ok(Math.abs(c0.lat - 38) < 1e-9 && Math.abs(c0.lon - 127.5) < 1e-9, 'origin');

  ok(qLevel('부산 해운대구 우동') === 'area', 'level area');
  ok(qLevel('해운대구 해운대해변로 264') === 'addr', 'level addr road no');
  ok(qLevel('부산 해운대구 우동 1408') === 'addr', 'level jibun');
  ok(qLevel('세종대로') === 'road', 'level road');

  // 가짜 외부
  let t = Date.parse('2026-11-15T03:00:00Z');
  const calls = [];
  let nomiAt = [], nomiEmpty = false;
  const resp = (j) => ({ ok: true, json: async () => j });
  const fakeFetch = async (url, opt) => {
    calls.push(url);
    if (url.startsWith('https://business.juso.go.kr/addrlink/addrLinkApi.do')) {
      ok(/confmKey=FAKE_J/.test(url), 'juso key in url');
      return resp({ results: { common: { errorCode: '0', totalCount: '1' }, juso: [{ roadAddr: '부산광역시 해운대구 해운대해변로 264 (우동)', roadAddrPart1: '부산광역시 해운대구 해운대해변로 264', admCd: '2635010500', rnMgtSn: '263504163024', udrtYn: '0', buldMnnm: '264', buldSlno: '0', siNm: '부산광역시', sggNm: '해운대구', emdNm: '우동' }] } });
    }
    if (url.startsWith('https://business.juso.go.kr/addrlink/addrCoordApi.do')) {
      ok(/confmKey=FAKE_C/.test(url) && /buldMnnm=264/.test(url), 'coord params');
      const u = wgsToUtmk(35.15964, 129.16034);
      return resp({ results: { common: { errorCode: '0' }, juso: [{ entX: String(u.x), entY: String(u.y) }] } });
    }
    if (url.startsWith('https://nominatim.openstreetmap.org/search')) {
      nomiAt.push(t);
      if (nomiEmpty) return resp([]);
      ok(opt && opt.headers && /TripRoutie/.test(opt.headers['User-Agent']), 'nominatim UA');
      if (/countrycodes=kr/.test(url)) return resp([{ lat: '35.1631', lon: '129.1636', display_name: '우동, 해운대구, 부산광역시, 48094, 대한민국', addresstype: 'quarter', category: 'boundary' }]);
      return resp([{ lat: '48.8584', lon: '2.2945', display_name: '5, Avenue Anatole France, Gros-Caillou, Paris 7e, Paris, France', addresstype: 'building', category: 'building' }]);
    }
    throw new Error('unexpected ' + url);
  };
  const store = new Map();
  const sleep = async (ms) => { t += ms; };
  const mk = (keys = true, coord = keys) => makeGeoHandler({
    verify: async (req) => { const a = req.headers && req.headers.authorization; if (!a) throw new Error('x'); return { uid: a.slice(7) }; },
    fetch: fakeFetch, now: () => t, sleep, log: () => {},
    jusoKey: () => (keys ? 'FAKE_J' : 'pending'), coordKey: () => (coord ? 'FAKE_C' : (keys ? '' : 'pending')), userAgent: () => 'TripRoutie-geoLookup/1.0 (+https://example.invalid/app)',
    cache: { get: async (k) => store.get(k) || null, set: async (k, v) => { store.set(k, v); } },
  });
  let h = mk();
  const call = (q, uid = 'u1', lang = 'ko') => h({ method: 'POST', headers: uid ? { authorization: 'Bearer ' + uid } : {}, body: { q, lang } });

  let r = await call('부산 해운대구 우동', null); ok(r.status === 401, 'no auth 401');
  r = await h({ method: 'GET', headers: {}, body: {} }); ok(r.status === 405, 'GET 405');
  r = await call('a'); ok(r.status === 400, 'short q 400');

  // ① 문제 재현 검색어: 동네 수준 → Nominatim(kr), 정확도 area, 도시만 나오지 않음
  r = await call('부산 해운대구 우동');
  ok(r.status === 200 && r.body.ok && r.body.results.length === 1, 'udong ' + JSON.stringify(r.body));
  ok(r.body.results[0].acc === 'area' && /우동/.test(r.body.results[0].line) && Math.abs(r.body.results[0].lat - 35.1631) < 1e-4, 'udong area');
  ok(/OpenStreetMap/.test(r.body.credit), 'osm credit');
  ok(Object.keys(r.body.results[0]).sort().join() === 'acc,lat,line,lon', 'only 4 fields');
  const n1 = calls.length;
  r = await call('부산  해운대구 우동'); ok(r.body.cached === true && calls.length === n1, 'cache (space-normalized) no new call');

  // 번지 → 도로명주소 + 좌표 API, 정확도 addr
  r = await call('해운대구 해운대해변로 264');
  ok(r.body.ok && r.body.results[0].acc === 'addr' && r.body.results[0].line === '부산광역시 해운대구 해운대해변로 264', 'juso addr ' + JSON.stringify(r.body));
  ok(Math.abs(r.body.results[0].lat - 35.15964) < 2e-5 && Math.abs(r.body.results[0].lon - 129.16034) < 2e-5, 'juso coord');
  ok(/도로명주소/.test(r.body.credit), 'juso credit');
  ok(!JSON.stringify(r.body).includes('FAKE'), 'no key in body');
  // 저장 모양: 좌표·한 줄 주소·정확도 + at/src 만
  const saved = [...store.values()].pop();
  ok(Object.keys(saved).sort().join() === 'at,r,src' && saved.r.every((x) => Object.keys(x).sort().join() === 'acc,lat,line,lon'), 'stored shape ' + JSON.stringify(saved));

  // 해외 주소
  r = await call('5 Avenue Anatole France Paris', 'u1', 'en');
  ok(r.body.ok && r.body.results[0].acc === 'addr' && /Paris/.test(r.body.results[0].line), 'paris');

  // 키 없음 → 한국 번지 주소도 Nominatim 으로(조용히)
  h = mk(false);
  const c0n = calls.length;
  r = await call('부산 해운대구 우동 1408');
  ok(r.body.ok && r.body.results.length >= 1 && /OpenStreetMap/.test(r.body.credit), 'no key fallback ' + JSON.stringify(r.body));
  ok(!calls.slice(c0n).some((u) => u.includes('juso.go.kr')), 'pending key → juso not called');

  // 앱 전체 초당 1회: 연달아 다른 검색어 3개 → Nominatim 호출 간격 ≥ 1.1초
  nomiAt = [];
  for (const q of ['Rue de Rivoli 10 Paris', 'Kurfürstendamm 21 Berlin', '1 Infinite Loop Cupertino']) await call(q, 'u2', 'en');
  ok(nomiAt.length === 3 && nomiAt[1] - nomiAt[0] >= 1100 && nomiAt[2] - nomiAt[1] >= 1100, 'nominatim spacing ' + nomiAt.map((x) => x - nomiAt[0]));
  // 대기열 길면 busy
  const g = makeSerialGate(() => 0, async () => {}, 1100, 4500);
  const res = []; for (let i = 0; i < 7; i++) res.push(await g());
  ok(res.filter(Boolean).length === 5 && res[5] === false, 'serial busy ' + res);

  // 사용자 분당 상한
  h = mk();
  let last; for (let i = 0; i < LIM.userPerMin + 1; i++) last = await call('Paris ' + i, 'u9', 'en');
  ok(last.status === 429 && last.body.scope === 'minute', 'minute gate');

  // 132기 2차: 검색키만 있고 좌표키 없음 → 도로명주소를 Nominatim 으로 좌표화
  h = mk(true, false);
  let c1 = calls.length;
  r = await call('해운대구 해운대해변로 264', 'u3');
  ok(r.body.ok && r.body.results.length === 1 && r.body.results[0].line === '부산광역시 해운대구 해운대해변로 264' && typeof r.body.results[0].lat === 'number', 'nocoordkey → nominatim ' + JSON.stringify(r.body));
  ok(calls.slice(c1).some((u) => u.includes('addrLinkApi')) && !calls.slice(c1).some((u) => u.includes('addrCoordApi')), 'no coord api call');
  ok(calls.slice(c1).some((u) => u.includes('nominatim') && decodeURIComponent(u.replace(/\+/g, ' ')).includes('해운대해변로 264')), 'nominatim got roadAddr');
  // Nominatim 도 실패 → 좌표 없음(null) + 도로명주소 줄 → 앱이 핀 단계로
  nomiEmpty = true;
  r = await call('해운대구 해운대해변로 265', 'u3');
  ok(r.body.ok && r.body.results.length === 1 && r.body.results[0].lat === null && r.body.results[0].lon === null && /해운대해변로/.test(r.body.results[0].line) && r.body.results[0].acc === 'addr', 'nocoord result ' + JSON.stringify(r.body));
  const sv = [...store.values()].pop(); ok(sv.r[0].lat === null && Object.keys(sv.r[0]).sort().join() === 'acc,lat,line,lon', 'nocoord stored shape');
  c1 = calls.length; r = await call('해운대구 해운대해변로 265', 'u3'); ok(r.body.cached && calls.length === c1, 'nocoord cached same day');
  t += 864e5 + 1000; c1 = calls.length; r = await call('해운대구 해운대해변로 265', 'u3'); ok(!r.body.cached && calls.length > c1, 'nocoord cache expires after 1 day');
  // 좌표키가 생기면(재배포) 캐시 키가 달라져 새로 찾음
  nomiEmpty = false; h = mk(true, true); c1 = calls.length;
  r = await call('해운대구 해운대해변로 265', 'u3');
  ok(!r.body.cached && typeof r.body.results[0].lat === 'number' && calls.slice(c1).some((u) => u.includes('addrCoordApi')), 'coord key added → fresh lookup');
  // geo_cfg.json 분기
  const { execFileSync } = require('child_process'), fs = require('fs'), os = require('os'), path = require('path');
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'g132-')); fs.copyFileSync(path.join(__dirname, 'geo_fn.js'), path.join(tmp, 'geo_fn.js'));
  const cfg = () => execFileSync(process.execPath, ['-e', `process.stdout.write(JSON.stringify(require(${JSON.stringify(path.join(tmp, 'geo_fn.js'))}).CFG))`]).toString();
  ok(cfg() === '{"coord":false}', 'no cfg file → coord false');
  fs.writeFileSync(path.join(tmp, 'geo_cfg.json'), '{"coord":true}'); ok(cfg() === '{"coord":true}', 'cfg coord true');
  fs.rmSync(tmp, { recursive: true, force: true });
  console.log(`geo_fn: ${pass}/${fail}`);
})();
