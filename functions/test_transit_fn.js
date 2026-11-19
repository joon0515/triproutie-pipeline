'use strict';
// node test_transit_fn.js — 외부 호출 0(가짜 fetch·인증). 257기 transitLive* 5개.
const zlib = require('zlib');
const T = require('./transit_fn');
let pass = 0, fail = 0;
const ok = (c, m) => { if (c) pass++; else { fail++; console.log('FAIL', m); } };

// ---- 작은 protobuf 쓰기(시험용) ----
const vint = (n) => { const b = []; while (n > 127) { b.push((n % 128) | 128); n = Math.floor(n / 128); } b.push(n); return Buffer.from(b); };
const fld = (f, w, payload) => Buffer.concat([vint(f * 8 + w), w === 2 ? Buffer.concat([vint(payload.length), payload]) : vint(payload)]);
const s = (x) => Buffer.from(x, 'utf8');
const NOW = Date.parse('2026-11-20T03:00:00Z');
function feed(trips) {
  const ents = trips.map((t, i) => {
    const td = Buffer.concat([fld(1, 2, s(`trip${i}`)), fld(5, 2, s(t.route))]);
    const stus = t.stops.map(([stop, sec]) => fld(2, 2, Buffer.concat([fld(4, 2, s(stop)), fld(2, 2, fld(2, 0, Math.floor(NOW / 1000) + sec))])));
    const tu = Buffer.concat([fld(1, 2, td), ...stus]);
    return fld(2, 2, Buffer.concat([fld(1, 2, s(`e${i}`)), fld(3, 2, tu)]));
  });
  return Buffer.concat([fld(1, 2, fld(1, 2, s('2.0'))), ...ents]);
}
function zip(name, text) {
  const data = zlib.deflateRawSync(Buffer.from(text)), nm = Buffer.from(name);
  const lh = Buffer.alloc(30); lh.writeUInt32LE(0x04034b50, 0); lh.writeUInt16LE(8, 8); lh.writeUInt32LE(data.length, 18); lh.writeUInt16LE(nm.length, 26);
  const cd = Buffer.alloc(46); cd.writeUInt32LE(0x02014b50, 0); cd.writeUInt16LE(8, 10); cd.writeUInt32LE(data.length, 20); cd.writeUInt16LE(nm.length, 28); cd.writeUInt32LE(0, 42);
  const local = Buffer.concat([lh, nm, data]);
  const eocd = Buffer.alloc(22); eocd.writeUInt32LE(0x06054b50, 0); eocd.writeUInt16LE(1, 8); eocd.writeUInt16LE(1, 10); eocd.writeUInt32LE(cd.length + nm.length, 12); eocd.writeUInt32LE(local.length, 16);
  return Buffer.concat([local, cd, nm, eocd]);
}
const STOPS = 'stop_id,stop_name,stop_lat,stop_lon,location_type,parent_station\n631,Grand Central-42 St,40.751776,-73.976848,1,\n631N,Grand Central-42 St,40.751776,-73.976848,0,631\n901,Times Sq-42 St,40.755477,-73.987691,1,\n201,Wakefield-241 St,40.903125,-73.85062,1,\n';

function core(routes, log = []) {
  return T.makeTransitCore({
    verify: async (req) => { if (!req.get('authorization')) throw Object.assign(new Error('auth'), { reason: 'auth' }); return { uid: 'u1' }; },
    now: () => NOW, key: (n) => `FAKE_${n}`, log: () => {},
    fetch: async (url, init) => {
      log.push({ url: String(url), h: (init && init.headers) || {} });
      for (const [re, v] of routes) if (re.test(String(url))) {
        if (v && v.status) return { ok: false, status: v.status, json: async () => ({}) };
        return { ok: true, status: 200, json: async () => v, arrayBuffer: async () => (Buffer.isBuffer(v) ? v.buffer.slice(v.byteOffset, v.byteOffset + v.length) : new ArrayBuffer(0)) };
      }
      return { ok: false, status: 404, json: async () => ({}) };
    },
  });
}
const req = (body, auth = true) => ({ method: 'POST', body, get: (h) => (h === 'authorization' && auth ? 'Bearer t' : '') });

(async () => {
  // 입력 검사
  ok(T.vBody('mta', { lat: 40.75, lon: -73.97 }).v, 'NY 안 좌표 OK');
  ok(T.vBody('mta', { lat: 37.5, lon: 127 }).bad === 'outside', '상자 밖 거절');
  ok(T.vBody('tfl', { lat: 'x', lon: 0 }).bad === 'coord', '숫자 아님 거절');
  ok(T.vBody('lta', null).bad === 'body', '본문 없음');
  ok(T.vBody('tfl', { lat: 51.50123456, lon: -0.12345678 }).v.lat === 51.5012, '좌표 4자리로 자름(캐시 묶음)');
  ok(Object.keys(T.SECRETS).length === 5 && T.SECRETS.mta === null, 'MTA 만 키 없음');
  ok(Object.values(T.ATTR).every((a) => a.length > 5) && T.ATTR.tfl === 'Powered by TfL Open Data', '출처 문구(TfL 약관 문장 그대로)');
  ok(!/추정/.test(require('fs').readFileSync(require('path').join(__dirname, 'transit_fn.js'), 'utf8')), "'추정' 없음");

  // protobuf·zip
  const fb = feed([{ route: '6', stops: [['631N', 120], ['640N', 600]] }, { route: '4', stops: [['631S', 300], ['250S', 900]] }, { route: '7', stops: [['901N', 60]] }]);
  const tr = T.gtfsRtTrips(fb);
  ok(tr.length === 3 && tr[0].route === '6' && tr[0].stops[0].stop === '631N', 'GTFS-rt 읽기');
  ok(tr[0].stops[0].t === Math.floor(NOW / 1000) + 120, '도착 시각 읽기');
  const z = zip('stops.txt', STOPS);
  ok(T.unzipOne(z, 'stops.txt').toString().startsWith('stop_id'), 'zip 에서 stops.txt');
  ok(T.unzipOne(z, 'nope.txt') === null, '없는 파일 null');
  ok(T.csvRows('a,b\n"x,y",2\n')[0].a === 'x,y', 'CSV 따옴표');

  // MTA
  {
    const log = [];
    const c = core([[/gtfs_subway\.zip/, z], [/nyct%2Fgtfs$/, fb], [/mtagtfsfeeds/, feed([])]], log);
    const r = await c.handle('mta', req({ lat: 40.7527, lon: -73.9772 }));
    ok(r.status === 200 && r.body.ok && r.body.stop.id === '631', 'MTA 가까운 역 = Grand Central');
    ok(r.body.arrivals.length === 2 && r.body.arrivals[0].route === '6' && r.body.arrivals[0].min === 2, 'MTA 다음 열차 6호선 2분');
    ok(r.body.arrivals[1].route === '4' && r.body.arrivals[1].min === 5, 'MTA 그다음 4호선 5분');
    ok(r.body.arrivals[0].live === true && r.body.attr.includes('MTA'), 'MTA 실시간·출처');
    const n = log.length;
    await c.handle('mta', req({ lat: 40.7527, lon: -73.9772 }));
    ok(log.length === n, 'MTA 30초 안 두 번째 = 캐시(호출 0)');
    const f = await c.handle('mta', req({ lat: 40.7527, lon: -73.9772, line: '4' }));
    ok(f.body.arrivals.length === 1 && f.body.arrivals[0].route === '4', '노선 거르기');
    const far = await c.handle('mta', req({ lat: 40.60, lon: -74.20 }));
    ok(far.body.ok === false && far.body.reason === 'no_stop', '400m 안 역 없음');
    ok((await c.handle('mta', req({ lat: 40.75, lon: -73.97 }, false))).status === 401, '로그인 없으면 401');
    ok((await c.handle('mta', { method: 'GET', body: {}, get: () => '' })).status === 405, 'GET 거절');
  }
  // TfL
  {
    const log = [];
    const c = core([[/StopPoint\/\?lat/, { stopPoints: [{ naptanId: '940GZZLUOXC', commonName: 'Oxford Circus Underground Station', lat: 51.515, lon: -0.1415, distance: 40 }] }],
      [/Arrivals/, [{ lineName: 'Victoria', destinationName: 'Brixton Underground Station', platformName: 'Southbound - Platform 4', timeToStation: 125, expectedArrival: '2026-11-20T03:02:05Z' }, { lineName: 'Central', destinationName: 'Ealing Broadway', timeToStation: 40 }]]], log);
    const r = await c.handle('tfl', req({ lat: 51.5152, lon: -0.1418 }));
    ok(r.body.ok && r.body.stop.id === '940GZZLUOXC', 'TfL 역');
    ok(r.body.arrivals[0].route === 'Central' && r.body.arrivals[0].min === 1 && r.body.arrivals[1].min === 2, 'TfL 정렬·분');
    ok(log.every((x) => x.url.includes('app_key=FAKE_TFL_APP_KEY')), 'TfL 키는 요청에만');
    ok(!JSON.stringify(r.body).includes('FAKE'), '응답에 키 없음');
    ok(r.body.attr === 'Powered by TfL Open Data', 'TfL 표기');
  }
  // LTA
  {
    const log = [];
    const c = core([[/BusStops/, { value: [{ BusStopCode: '01012', Description: 'Hotel Grand Pacific', Latitude: 1.29684, Longitude: 103.85253 }, { BusStopCode: '01013', Description: "St. Joseph's Ch", Latitude: 1.29770, Longitude: 103.85362 }] }],
      [/v3\/BusArrival/, { Services: [{ ServiceNo: '7', NextBus: { EstimatedArrival: '2026-11-20T11:04:00+08:00', DestinationCode: '01013', Monitored: 1 }, NextBus2: { EstimatedArrival: '2026-11-20T11:12:00+08:00', DestinationCode: '01013', Monitored: 0 }, NextBus3: { EstimatedArrival: '' } }] }]], log);
    const r = await c.handle('lta', req({ lat: 1.2969, lon: 103.8526 }));
    ok(r.body.ok && r.body.stop.id === '01012', 'LTA 정류장');
    ok(r.body.arrivals.length === 2 && r.body.arrivals[0].min === 4 && r.body.arrivals[0].live && !r.body.arrivals[1].live, 'LTA 4분 실시간 · 12분 시간표(Monitored 0)');
    ok(r.body.arrivals[0].dest === "St. Joseph's Ch", 'LTA 행선 이름');
    ok(log.every((x) => x.h.AccountKey === 'FAKE_LTA_ACCOUNT_KEY'), 'LTA AccountKey 머리');
  }
  // OBA
  {
    const c = core([[/stops-for-location/, { data: { list: [{ id: '1_75403', name: 'Pine St & 4th Ave', lat: 47.6105, lon: -122.3379 }] } }],
      [/arrivals-and-departures/, { data: { entry: { arrivalsAndDepartures: [{ routeShortName: '1 Line', tripHeadsign: 'Angle Lake', predicted: true, predictedDepartureTime: NOW + 180e3, scheduledDepartureTime: NOW + 120e3 }, { routeShortName: '8', tripHeadsign: 'Rainier Beach', predicted: false, predictedDepartureTime: 0, scheduledDepartureTime: NOW + 600e3 }] } } }]]);
    const r = await c.handle('oba', req({ lat: 47.6106, lon: -122.338 }));
    ok(r.body.ok && r.body.arrivals[0].min === 3 && r.body.arrivals[0].live, 'OBA 예측 3분 = 실시간');
    ok(r.body.arrivals[1].min === 10 && r.body.arrivals[1].live === false, 'OBA 예측 없으면 시간표(live false)');
  }
  // IDFM
  {
    const log = [];
    const c = core([[/places_nearby/, { places_nearby: [{ distance: '85', stop_area: { id: 'stop_area:IDFM:71517', name: 'Châtelet', coord: { lat: '48.8584', lon: '2.3470' } } }] }],
      [/stop-monitoring/, { Siri: { ServiceDelivery: { StopMonitoringDelivery: [{ MonitoredStopVisit: [{ MonitoredVehicleJourney: { PublishedLineName: [{ value: '1' }], DestinationName: [{ value: 'La Défense' }], MonitoredCall: { ExpectedDepartureTime: '2026-11-20T03:03:00Z' } } }, { MonitoredVehicleJourney: { LineRef: { value: 'STIF:Line::C01371:' }, DestinationName: [{ value: 'Mairie des Lilas' }], MonitoredCall: { AimedDepartureTime: '2026-11-20T03:07:00Z' } } }] }] } } }]], log);
    const r = await c.handle('idfm', req({ lat: 48.8585, lon: 2.3471 }));
    ok(r.body.ok && r.body.stop.name === 'Châtelet', 'IDFM 정류장');
    ok(log.some((x) => x.url.includes(encodeURIComponent('STIF:StopArea:SP:71517:'))), 'IDFM MonitoringRef 만들기');
    ok(r.body.arrivals[0].route === '1' && r.body.arrivals[0].min === 3 && r.body.arrivals[0].live, 'IDFM 예상 시각 = 실시간');
    ok(r.body.arrivals[1].live === false, 'IDFM 예정 시각만 = 시간표');
    ok(log.every((x) => x.h.apikey === 'FAKE_IDFM_APIKEY'), 'IDFM apikey 머리');
    ok(T.TTL.idfm === 60e3, 'IDFM 캐시 60초(갱신 1분·하루 1,000)');
  }
  // 위쪽 실패
  {
    const c = core([[/StopPoint/, { status: 429 }]]);
    const r = await c.handle('tfl', req({ lat: 51.5, lon: -0.12 }));
    ok(r.status === 200 && r.body.ok === false && r.body.reason === 'quota', '한도 초과 → quota');
    const c2 = core([[/BusStops/, { status: 500 }]]);
    const r2 = await c2.handle('lta', req({ lat: 1.3, lon: 103.85 }));
    ok(r2.body.ok === false && r2.body.reason === 'upstream', '위쪽 오류 → upstream');
  }
  // 268기: 사용자별 캐시 미스 상한(좌표를 바꿔 캐시 피하기 차단) · 캐시 적중은 세지 않음
  {
    const c = core([[/StopPoint\/\?/, { stopPoints: [] }]]);
    let last;
    for (let i = 0; i <= T.USER_MISS_PER_10M; i++) last = await c.handle('tfl', req({ lat: 51.5 + i * 0.001, lon: -0.12 }));
    ok(last.status === 429 && last.body.reason === 'rate', '268 사용자별 미스 상한 → rate');
    const hit = await c.handle('tfl', req({ lat: 51.5, lon: -0.12 }));
    ok(hit.status === 200, '268 캐시 적중은 상한과 무관');
  }
  console.log(`transit_fn: ${pass}/${fail}`);
  process.exit(fail ? 1 : 0);
})();
