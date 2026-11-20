'use strict';
// 262기(11/20): 가입·설치 없는 링크 투표(앱 묶음 CC_bench/beat262 tl_vote262.dart). index.js 한 줄: Object.assign(exports, require('./vote_fn').makeVoteFunctions());
//
// votePage (GET ?t=<토큰 22자>) — 웹 한 장. 링크 문서 vote_links/{토큰} 이 켜져 있고(on) 만료(exp) 전이면 장소마다 좋아요·별로 + 열린 9a 투표 선택지.
//   카카오톡·메신저 미리보기용 og:title(여행 제목)·og:description(장소 수)·og:image(문서의 img, https 만). 로그인·쿠키·추적 없음.
// voteCast (POST JSON {t, bid, v, p, nick?}) — 서버가 검사해서 vote_links/{토큰}/ballots/{bid} 에 쓴다(한 기기 = 한 문서, 다시 보내면 덮어씀).
//   검사: 토큰·링크 열림 · bid 모양 · v 의 열쇠 ⊂ 링크 장소 id, 값 ∈ {1, -1} · p 의 열쇠 ⊂ 투표 id, 값 ∈ 그 투표 선택지 id · 닉네임 20자 · 링크당 300명.
// 클라이언트 규칙(v15 후보): ballots 쓰기는 앱·웹 모두 불가(이 함수만), 읽기는 그 여행 멤버만. 링크 문서는 멤버가 만들고 끄기만.
// 링크 문서에는 장소 이름·일차·9a 질문/선택지만 있다(앱이 만들 때 거름) — 이 함수도 그 밖의 칸은 내보내지 않는다.

const REGION = 'asia-northeast3';
const TOKEN = /^[A-Za-z0-9_-]{22}$/;
const BID = /^[A-Za-z0-9_-]{16,40}$/;
const MAX_BALLOTS = 300;

const T = {
  ko: { like: '좋아요', nope: '별로', send: '보내기', sent: '보냈어요. 다시 누르면 바꿀 수 있어요.', nick: '이름(선택)', closed: '이 링크는 닫혔어요.', poll: '하나 골라 주세요', day: '{d}일차', note: '가입·설치 없이 투표해요. 이 기기에서만 내 표를 기억해요.', err: '보내지 못했어요. 잠시 뒤 다시 눌러 주세요.' },
  en: { like: 'Like', nope: 'Not really', send: 'Send', sent: 'Sent. Tap again to change.', nick: 'Name (optional)', closed: 'This link is closed.', poll: 'Pick one', day: 'Day {d}', note: 'No sign-up or app. Your vote is remembered on this device only.', err: "Couldn't send. Please try again." },
  ja: { like: 'いいね', nope: 'いまいち', send: '送信', sent: '送信しました。もう一度押すと変更できます。', nick: '名前（任意）', closed: 'このリンクは終了しました。', poll: '1つ選んでください', day: '{d}日目', note: '登録・インストール不要。投票はこの端末だけに記憶されます。', err: '送信できませんでした。もう一度お試しください。' },
  zh: { like: '喜欢', nope: '一般', send: '提交', sent: '已提交。再次点击可修改。', nick: '名字（选填）', closed: '此链接已关闭。', poll: '请选一个', day: '第{d}天', note: '无需注册或安装。你的投票只保存在本设备。', err: '提交失败，请稍后再试。' },
  zt: { like: '喜歡', nope: '普通', send: '送出', sent: '已送出。再次點擊可修改。', nick: '名字（選填）', closed: '此連結已關閉。', poll: '請選一個', day: '第{d}天', note: '無需註冊或安裝。你的投票只保存在本裝置。', err: '送出失敗，請稍後再試。' },
};

function pickLang(h) {
  for (const part of String(h || '').toLowerCase().split(',')) {
    const s = part.trim();
    if (/^zh-(tw|hk|mo|hant)/.test(s)) return 'zt';
    for (const k of ['ko', 'ja', 'zh', 'en']) if (s.startsWith(k)) return k;
  }
  return 'en';
}

const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

function toMs(x) {
  if (!x) return 0;
  if (typeof x.toMillis === 'function') return x.toMillis();
  if (x instanceof Date) return x.getTime();
  if (typeof x._seconds === 'number') return x._seconds * 1000;
  return 0;
}

function isOpen(link, now = Date.now()) {
  return !!link && link.on === true && toMs(link.exp) > now;
}

/** 링크 문서 → 내보내도 되는 칸만(장소 id·이름·일차, 투표 id·질문·선택지). */
function publicView(link) {
  const places = (Array.isArray(link.places) ? link.places : []).slice(0, 60)
    .filter((p) => p && typeof p.id === 'string' && typeof p.n === 'string')
    .map((p) => ({ id: p.id.slice(0, 80), n: p.n.slice(0, 80), d: Number.isInteger(p.d) ? p.d : 0 }));
  const polls = (Array.isArray(link.polls) ? link.polls : []).slice(0, 5)
    .filter((v) => v && typeof v.id === 'string' && Array.isArray(v.o))
    .map((v) => ({ id: v.id.slice(0, 80), q: String(v.q || '').slice(0, 80), o: v.o.filter((o) => o && typeof o.id === 'string').slice(0, 12).map((o) => ({ id: o.id.slice(0, 40), l: String(o.l || '').slice(0, 60) })) }));
  return { title: String(link.title || link.city || '').slice(0, 60), city: String(link.city || '').slice(0, 40), img: typeof link.img === 'string' && /^https:\/\/[^\s"'<>]{1,600}$/.test(link.img) ? link.img : '', places, polls };
}

/** 표 검사 → {ok, ballot} | {ok:false, reason}. */
function checkBallot(link, body, now = Date.now()) {
  if (!isOpen(link, now)) return { ok: false, reason: 'closed' };
  const b = body || {};
  if (!BID.test(String(b.bid || ''))) return { ok: false, reason: 'bid' };
  const pv = publicView(link);
  const ids = new Set(pv.places.map((p) => p.id));
  const v = {};
  for (const [k, x] of Object.entries(b.v && typeof b.v === 'object' ? b.v : {})) {
    if (!ids.has(k) || (x !== 1 && x !== -1)) return { ok: false, reason: 'v' };
    v[k] = x;
  }
  const p = {};
  for (const [k, x] of Object.entries(b.p && typeof b.p === 'object' ? b.p : {})) {
    const poll = pv.polls.find((q) => q.id === k);
    if (!poll || !poll.o.some((o) => o.id === x)) return { ok: false, reason: 'p' };
    p[k] = x;
  }
  if (!Object.keys(v).length && !Object.keys(p).length) return { ok: false, reason: 'empty' };
  const nick = String(b.nick || '').replace(/\s+/g, ' ').trim();
  if (nick.length > 20 || /:\/\/|www\.|[<>]/i.test(nick)) return { ok: false, reason: 'nick' };
  return { ok: true, ballot: { v, p, ...(nick ? { nick } : {}) } };
}

function renderPage(link, token, lang, nonce) {
  const L = T[lang] || T.en;
  if (!isOpen(link)) {
    return `<!DOCTYPE html><html lang="${lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex"><title>TripRoutie</title></head><body style="font-family:system-ui;padding:24px"><p>${esc(L.closed)}</p></body></html>`;
  }
  const pv = publicView(link);
  const desc = `${pv.places.length} · ${L.like} / ${L.nope}`;
  const rows = pv.places.map((p) => `<li data-id="${esc(p.id)}"><span class="d">${esc(L.day.replace('{d}', p.d))}</span> <b>${esc(p.n)}</b><span class="b"><button type="button" data-v="1" aria-pressed="false">👍 ${esc(L.like)}</button><button type="button" data-v="-1" aria-pressed="false">👎 ${esc(L.nope)}</button></span></li>`).join('');
  const polls = pv.polls.map((q) => `<fieldset data-poll="${esc(q.id)}"><legend>${esc(q.q || L.poll)}</legend>${q.o.map((o) => `<label><input type="radio" name="p_${esc(q.id)}" value="${esc(o.id)}"> ${esc(o.l)}</label>`).join('')}</fieldset>`).join('');
  return `<!DOCTYPE html><html lang="${lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>${esc(pv.title)}</title><meta property="og:type" content="website"><meta property="og:title" content="${esc(pv.title)}"><meta property="og:description" content="${esc(desc)}">${pv.img ? `<meta property="og:image" content="${esc(pv.img)}">` : ''}
<style nonce="${nonce}">body{font-family:system-ui,sans-serif;margin:0;padding:16px;max-width:560px;margin:auto;color:#191f28}h1{font-size:20px}ul{list-style:none;padding:0}li{padding:10px 0;border-bottom:1px solid #eef}li b{display:block;margin:2px 0 6px}.d{color:#6b7684;font-size:12px}button{min-height:44px;margin-right:8px;border:1px solid #cdd;border-radius:10px;background:#fff;padding:0 12px;font-size:15px}button[aria-pressed=true]{background:#3166cf;color:#fff;border-color:#3166cf}fieldset{border:1px solid #dde;border-radius:10px;margin:10px 0}label{display:block;min-height:36px}input[type=text]{min-height:40px;width:100%;box-sizing:border-box}#s{width:100%;margin-top:12px;background:#3166cf;color:#fff}.n{color:#6b7684;font-size:12px}</style></head>
<body><h1>${esc(pv.title)}</h1><p class="n">${esc(L.note)}</p><ul id="l">${rows}</ul>${polls}<input type="text" id="nick" maxlength="20" placeholder="${esc(L.nick)}" autocomplete="off"><button type="button" id="s">${esc(L.send)}</button><p id="m" role="status" class="n"></p>
<script nonce="${nonce}">(function(){var t=${JSON.stringify(token)},k='tlv_'+t,s;try{s=JSON.parse(localStorage.getItem(k)||'{}')}catch(e){s={}}
if(!s.bid){var a=new Uint8Array(18);crypto.getRandomValues(a);s.bid=btoa(String.fromCharCode.apply(null,a)).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');s.v={};s.p={}}
function paint(){document.querySelectorAll('#l li').forEach(function(li){li.querySelectorAll('button').forEach(function(b){b.setAttribute('aria-pressed',String(s.v[li.dataset.id]===+b.dataset.v))})});document.querySelectorAll('fieldset').forEach(function(f){var x=s.p[f.dataset.poll];f.querySelectorAll('input').forEach(function(i){i.checked=i.value===x})});if(s.nick)document.getElementById('nick').value=s.nick}
document.getElementById('l').addEventListener('click',function(e){var b=e.target.closest('button');if(!b)return;var id=b.closest('li').dataset.id,v=+b.dataset.v;if(s.v[id]===v)delete s.v[id];else s.v[id]=v;paint()});
document.querySelectorAll('fieldset').forEach(function(f){f.addEventListener('change',function(e){s.p[f.dataset.poll]=e.target.value})});
document.getElementById('s').addEventListener('click',function(){s.nick=document.getElementById('nick').value.slice(0,20);localStorage.setItem(k,JSON.stringify(s));var m=document.getElementById('m');
fetch('voteCast',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({t:t,bid:s.bid,v:s.v,p:s.p,nick:s.nick})}).then(function(r){m.textContent=r.ok?${JSON.stringify(L.sent)}:${JSON.stringify(L.err)}}).catch(function(){m.textContent=${JSON.stringify(L.err)}})});paint()})();</script></body></html>`;
}

// 276기: 웹 면 보안 머리글(페이지·표 보내기 공통). HSTS·틀 안 띄우기·검색/AI 수집 금지·권한 끄기.
function baseHeaders(res) {
  res.set('Cache-Control', 'no-store');
  res.set('Strict-Transport-Security', 'max-age=31536000; includeSubDomains');
  res.set('X-Frame-Options', 'DENY');
  res.set('X-Robots-Tag', 'noindex, nofollow, noarchive, noai, noimageai');
  res.set('Permissions-Policy', 'camera=(), microphone=(), geolocation=(), payment=(), usb=()');
  res.set('Cross-Origin-Opener-Policy', 'same-origin');
  res.set('Cross-Origin-Resource-Policy', 'same-origin');
}

// 276기: 같은 접속 주소(IP)에서 몰아 넣는 표 막기(인스턴스 메모리, 최선 노력). 주소는 해시로만 들고 로그하지 않는다.
// Cloud Run 앞단은 실제 접속 주소를 X-Forwarded-For 끝에 붙인다(앞쪽은 보내는 쪽이 꾸밀 수 있음) → 끝 값을 쓴다.
const IP_LIMIT = Object.freeze({ windowMs: 10 * 60e3, casts: 60, newPerLink: 25, memMax: 5000 });
function clientKey(req) {
  const xff = String((req.get && req.get('x-forwarded-for')) || '').split(',').map((x) => x.trim()).filter(Boolean);
  let ip = xff.length ? xff[xff.length - 1] : String(req.ip || '');
  // SCAN1120: IPv6 는 한 사람이 /64 안에서 주소를 무한히 바꿀 수 있다 → 앞 4묶음(/64)으로 묶어 센다.
  if (ip.includes(':') && !/^::ffff:\d/.test(ip)) {
    const [head, tail = ''] = ip.split('::');
    const h = head ? head.split(':') : [], t = tail ? tail.split(':') : [];
    const full = [...h, ...Array(Math.max(0, 8 - h.length - t.length)).fill('0'), ...t];
    ip = full.slice(0, 4).map((x) => x.toLowerCase().replace(/^0+(?=.)/, '')).join(':') + '::/64';
  }
  return require('crypto').createHash('sha256').update('tlv276|' + ip).digest('base64').slice(0, 16);
}
function makeIpGate(now = Date.now, L = IP_LIMIT) {
  const m = new Map();
  const prune = (a, t) => a.filter((x) => t - x.t < L.windowMs);
  return {
    /** 'ok' | 'rate'. isNew = 새 표(그 링크에 이 기기 첫 표) 여부를 알면 넣는다. */
    hit(key, link, isNew) {
      const t = now();
      if (m.size >= L.memMax && !m.has(key)) m.delete(m.keys().next().value);
      const a = prune(m.get(key) || [], t);
      if (a.length >= L.casts) { m.set(key, a); return 'rate'; }
      if (isNew && a.filter((x) => x.l === link && x.n).length >= L.newPerLink) { m.set(key, a); return 'rate'; }
      a.push({ t, l: link, n: !!isNew }); m.set(key, a); return 'ok';
    },
  };
}

function headers(res, nonce) {
  baseHeaders(res);
  res.set('X-Content-Type-Options', 'nosniff');
  res.set('Referrer-Policy', 'no-referrer');
  res.set('Content-Security-Policy', `default-src 'none'; img-src https:; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`);
}

function makeHandlers(getDb, nowFn = Date.now) {
  const crypto = require('crypto');
  async function page(req, res) {
    const nonce = crypto.randomBytes(16).toString('base64');
    headers(res, nonce);
    const t = String((req.query || {}).t || '');
    const lang = pickLang(req.get ? req.get('accept-language') : '');
    if (req.method !== 'GET' || !TOKEN.test(t)) return res.status(404).type('html').send(renderPage(null, '', lang, nonce));
    const snap = await getDb().collection('vote_links').doc(t).get();
    const link = snap.exists ? snap.data() : null;
    return res.status(isOpen(link, nowFn()) ? 200 : 410).type('html').send(renderPage(link, t, lang, nonce));
  }
  const gate = makeIpGate(nowFn);
  async function cast(req, res) {
    baseHeaders(res);
    if (req.method !== 'POST') return res.status(405).json({ ok: false, reason: 'method' });
    const b = req.body || {};
    const t = String(b.t || '');
    if (!TOKEN.test(t)) return res.status(404).json({ ok: false, reason: 'token' });
    if (!/^Mozilla\/|^[A-Za-z][\w.-]*\/[\d.]+/.test(String((req.get && req.get('user-agent')) || ''))) return res.status(400).json({ ok: false, reason: 'client' });
    const ck = clientKey(req);
    if (gate.hit(ck, t, false) === 'rate') return res.status(429).json({ ok: false, reason: 'rate' });
    const db = getDb();
    const ref = db.collection('vote_links').doc(t);
    try {
      const out = await db.runTransaction(async (tx) => {
        const s = await tx.get(ref);
        const link = s.exists ? s.data() : null;
        const c = checkBallot(link, b, nowFn());
        if (!c.ok) return c;
        const bref = ref.collection('ballots').doc(String(b.bid));
        const had = await tx.get(bref);
        const nb = Number(link.nb || 0);
        if (!had.exists && nb >= MAX_BALLOTS) return { ok: false, reason: 'full' };
        if (!had.exists && gate.hit(ck, t, true) === 'rate') return { ok: false, reason: 'rate' };
        tx.set(bref, { ...c.ballot, at: new Date(nowFn()) });
        if (!had.exists) tx.update(ref, { nb: nb + 1 });
        return { ok: true };
      });
      return res.status(out.ok ? 200 : (out.reason === 'closed' ? 410 : out.reason === 'rate' ? 429 : 400)).json(out);
    } catch (e) {
      return res.status(500).json({ ok: false, reason: 'server' });
    }
  }
  return { page, cast };
}

function makeVoteFunctions({ region = REGION } = {}) {
  const { onRequest } = require('firebase-functions/v2/https');
  const { getFirestore } = require('firebase-admin/firestore');
  const h = makeHandlers(() => getFirestore());
  const opts = { region, maxInstances: 3, concurrency: 40, timeoutSeconds: 15, memory: '256MiB' };
  return { votePage: onRequest(opts, h.page), voteCast: onRequest(opts, h.cast) };
}

module.exports = { makeVoteFunctions, makeHandlers, makeIpGate, clientKey, IP_LIMIT, checkBallot, renderPage, publicView, isOpen, pickLang, MAX_BALLOTS };
