'use strict';
// 264기(11/20): 대중교통 공식 운행 공지 — ODPT(公共交通オープンデータセンター) 회원 API 의 odpt:TrainInformation 만.
// 앱 tl_tr264_ui.dart TlTr264Notices 가 FF 변수 transitLiveOn 에 'odpt' 가 있을 때만 부른다(도영은 공개 API 라 앱이 직접, 여기 안 거침).
// 함수 1개: transitNotices (GET ?op=odpt.Operator:TokyoMetro) — Secret ODPT_CONSUMER_KEY(ODPT 개발자 등록 무료 키). 대표가 이름 지정 배포.
// 공통: 로그인 사용자만(makeVerify). 운영사마다 60초 공유 캐시(ODPT 이용 규약: 과도한 요청 금지). 응답은 공지 칸만 골라 그대로(번역·가공 없음).
// 키 값은 요청에만 쓰고 로그·응답에 넣지 않는다.
const { makeVerify } = require('./google');

const REGION = 'asia-northeast3';
const OPS = Object.freeze(['odpt.Operator:TokyoMetro', 'odpt.Operator:JR-East', 'odpt.Operator:Keikyu', 'odpt.Operator:Keio', 'odpt.Operator:Keisei',
  'odpt.Operator:Odakyu', 'odpt.Operator:Seibu', 'odpt.Operator:Tobu', 'odpt.Operator:Tokyu', 'odpt.Operator:TWR', 'odpt.Operator:Yurikamome', 'odpt.Operator:MIR']);
const KEEP = Object.freeze(['odpt:operator', 'odpt:railway', 'odpt:trainInformationStatus', 'odpt:trainInformationText', 'dc:date', 'odpt:timeOfOrigin']);
const TTL = 60e3;
const ATTR = '公共交通オープンデータセンター(ODPT)';

function makeNoticeCore(deps) {
  const cache = new Map();
  async function handle(req) {
    if (req.method !== 'GET') return { status: 405, body: { ok: false, reason: 'method' } };
    try { await deps.verify(req); } catch (e) { return { status: 401, body: { ok: false, reason: (e && e.reason) || 'auth' } }; }
    const op = String((req.query && req.query.op) || '');
    if (!OPS.includes(op)) return { status: 400, body: { ok: false, reason: 'operator' } };
    const c = cache.get(op);
    if (c && deps.now() - c.at < TTL) return { status: 200, body: c.v };
    let v;
    try {
      const r = await deps.fetch(`https://api.odpt.org/api/v4/odpt:TrainInformation?odpt:operator=${encodeURIComponent(op)}&acl:consumerKey=${encodeURIComponent(deps.key())}`, { headers: { accept: 'application/json' } });
      if (!r.ok) throw Object.assign(new Error('upstream'), { status: r.status });
      const arr = await r.json();
      if (!Array.isArray(arr)) throw new Error('shape');
      v = arr.slice(0, 80).map((x) => Object.fromEntries(KEEP.filter((k) => x && x[k] != null).map((k) => [k, x[k]])));
    } catch (e) {
      deps.log('notice_upstream', { op, status: e && e.status });
      return { status: 200, body: [] };
    }
    cache.set(op, { at: deps.now(), v });
    return { status: 200, body: v };
  }
  return { handle };
}

function makeNoticeFunctions({ region = REGION } = {}) {
  const { onRequest } = require('firebase-functions/v2/https');
  const { defineSecret, defineBoolean } = require('firebase-functions/params');
  const { logger } = require('firebase-functions');
  const REQUIRE_APP_CHECK = defineBoolean('REQUIRE_APP_CHECK', { default: false });
  const KEY = defineSecret('ODPT_CONSUMER_KEY');
  const core = makeNoticeCore({ verify: makeVerify(REQUIRE_APP_CHECK), fetch: (u, i) => fetch(u, i), now: () => Date.now(), key: () => KEY.value(), log: (n, d) => logger.warn(n, d) });
  const opts = { region, maxInstances: 2, concurrency: 40, timeoutSeconds: 15, memory: '256MiB', cors: true, secrets: [KEY] };
  return {
    transitNotices: onRequest(opts, async (req, res) => {
      res.set('cache-control', 'no-store');
      try { const r = await core.handle(req); res.status(r.status).json(r.body); } catch (_) { res.status(500).json({ ok: false, reason: 'server' }); }
    }),
  };
}

module.exports = { makeNoticeFunctions, makeNoticeCore, OPS, KEEP, ATTR };
