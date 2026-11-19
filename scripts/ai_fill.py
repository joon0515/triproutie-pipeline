#!/usr/bin/env python3
"""AI 채움(ai-fill) — 이름 5개 언어(ko·en·ja·zh_Hans·zh_Hant) 번역·음역과 짧은 설명을 공식 무료 AI 사용량으로 채운다. (2026-11-20, 260기)

  python3 scripts/ai_fill.py run --store gs://<b>/collect/v1 [--max-places 1500] [--budget-min 55] [--dry-run]
  python3 scripts/ai_fill.py run --store <로컬 폴더> --col-dir <col_*.json.gz 폴더> --extra-dir <층 폴더>   # 시험

원칙
  - 공급자는 공식 엔드포인트만, 키는 환경 변수 이름으로만 다룬다(출력·로그·파일에 값을 쓰지 않는다). 키가 하나도 없으면 아무것도 하지 않고 끝낸다.
  - 앱 사용자 데이터는 보내지 않는다. 보내는 것은 공개 장소 데이터(이름·분류·도시·나라·출처 칸)뿐이다.
  - 이미 Wikidata·OSM·Overture 이름이 있는 칸은 건드리지 않는다(names 층·수집 열을 먼저 본다). AI 는 빈 칸만 채운다.
  - 설명은 보낸 근거 칸(이름·분류·도시·나라·출처)만으로 쓰게 하고, 근거가 모자라면 비운다. 금지 낱말(추정·어림·대략 등)이 든 글은 싣지 않는다.
  - 여러 공급자: 한도(분당·일당)를 지키고, 429·한도 초과면 그 공급자를 쉬게 하고 다음 공급자로 넘긴다. 하루 처리량 상한이 있다.
  - 결과 파일은 Storage processed/ai_fill/ 에만(저장소에 커밋하지 않음). 진행 위치(state.json)와 처리한 장소 목록(done.json.gz)도 Storage.
  - 모든 행에 출처 표시 {src:'ai', provider, model, date}. 앱이 'AI 번역·AI 요약' 으로 표시한다.
"""
import argparse, datetime, gzip, hashlib, json, os, re, sys, tempfile, time, unicodedata, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, 'collect'))
import place_desc as PD  # noqa: E402
import place_names as PN  # noqa: E402
from coverage import itin_of  # noqa: E402
from common import Store, bad_text  # noqa: E402

SLOTS = PD.SLOTS
SCHEMA = 'aifill-1'
NAME_MAX = 120
DESC_MAX = 120
BATCH = 10                      # 한 요청에 담는 장소 수
UA = 'TripRoutie-aifill/1.0 (+https://github.com/joon0515/triproutie-pipeline)'
BANNED = re.compile(PD.BANNED_WORDS.pattern + r'|추측|아마|maybe|perhaps|probably', re.I)


def log(msg):
    print(f'[ai-fill] {msg}', flush=True)


def today():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')


# ---------------------------------------------------------------- 공급자
# 한도는 공식 문서로 확인한 값만 기본값으로 둔다(docs/PROVIDERS.md). 문서에 숫자가 없는 것은 보수적인 값 + 환경 변수로 바꿀 수 있게 한다.
# AI_<이름>_RPM / AI_<이름>_RPD / AI_<이름>_MODEL 로 덮어쓴다.
SPEC = {
    'gemini':   {'keys': ('GEMINI_API_KEY',), 'model': 'gemini-3.1-flash-lite', 'rpm': 10, 'rpd': 250, 'kind': 'gemini',
                 'url': 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'},
    'cerebras': {'keys': ('CEREBRAS_API_KEY',), 'model': 'gpt-oss-120b', 'rpm': 5, 'rpd': 200, 'kind': 'openai',   # 공식 표: RPM 5(Free Trial)
                 'url': 'https://api.cerebras.ai/v1/chat/completions'},
    'groq':     {'keys': ('GROQ_API_KEY',), 'model': 'llama-3.1-8b-instant', 'rpm': 10, 'rpd': 200, 'kind': 'openai',
                 'url': 'https://api.groq.com/openai/v1/chat/completions'},
    'cloudflare': {'keys': ('CF_ACCOUNT_ID', 'CF_AI_TOKEN'), 'model': '@cf/meta/llama-3.1-8b-instruct', 'rpm': 10, 'rpd': 150, 'kind': 'openai',  # 일 10,000 Neurons(공식)
                   'url': 'https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/v1/chat/completions'},
    'nvidia':   {'keys': ('NVIDIA_API_KEY',), 'model': 'meta/llama-3.1-8b-instruct', 'rpm': 5, 'rpd': 100, 'kind': 'openai',
                 'url': 'https://integrate.api.nvidia.com/v1/chat/completions'},
}
ORDER = ('gemini', 'cerebras', 'groq', 'cloudflare', 'nvidia')


class RateLimited(Exception):
    def __init__(self, wait=60.0, daily=False):
        self.wait, self.daily = wait, daily


class ProviderError(Exception):
    pass


def _env_int(name, d):
    try:
        return int(os.environ.get(name, d))
    except ValueError:
        return d


class Provider:
    """공급자 하나 — 키 이름 목록만 들고, 값은 호출 순간에만 환경 변수에서 읽는다."""

    def __init__(self, name, spec, env=None, clock=time.time, sleep=time.sleep):
        self.name, self.spec, self.env = name, spec, env if env is not None else os.environ
        self.clock, self.sleep = clock, sleep
        up = name.upper()
        self.model = self.env.get(f'AI_{up}_MODEL') or spec['model']
        self.rpm = int(self.env.get(f'AI_{up}_RPM') or spec['rpm'])
        self.rpd = int(self.env.get(f'AI_{up}_RPD') or spec['rpd'])
        self.stamps, self.day_count, self.day, self.cool_until, self.dead = [], 0, today(), 0.0, False

    @property
    def enabled(self):
        return all(self.env.get(k) for k in self.spec['keys'])

    def _roll(self):
        d = today()
        if d != self.day:
            self.day, self.day_count, self.dead = d, 0, False

    def available(self):
        self._roll()
        return self.enabled and not self.dead and self.day_count < self.rpd and self.clock() >= self.cool_until

    def wait_slot(self):
        """분당 한도 — 60초 안의 호출이 rpm 개면 기다린다."""
        now = self.clock()
        self.stamps = [t for t in self.stamps if now - t < 60]
        if len(self.stamps) >= self.rpm:
            self.sleep(max(0.0, 60 - (now - self.stamps[0])) + 0.5)
            self.stamps = [t for t in self.stamps if self.clock() - t < 60]
        self.stamps.append(self.clock())
        self.day_count += 1

    def request(self, system, user):
        """(주소, 머리말, 본문 dict) — 키는 머리말에만 넣는다(주소에 넣지 않음)."""
        s, e = self.spec, self.env
        url = s['url'].format(model=self.model, **{k: e.get(k, '') for k in s['keys']})
        if s['kind'] == 'gemini':
            body = {'systemInstruction': {'parts': [{'text': system}]}, 'contents': [{'role': 'user', 'parts': [{'text': user}]}],
                    'generationConfig': {'temperature': 0.2, 'responseMimeType': 'application/json'}}
            return url, {'x-goog-api-key': e['GEMINI_API_KEY']}, body
        tok = e[s['keys'][-1]]
        body = {'model': self.model, 'temperature': 0.2, 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]}
        if self.name != 'cloudflare':
            body['response_format'] = {'type': 'json_object'}
        return url, {'Authorization': 'Bearer ' + tok}, body

    def parse(self, j):
        try:
            if self.spec['kind'] == 'gemini':
                return j['candidates'][0]['content']['parts'][0]['text']
            return j['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError):
            raise ProviderError('응답 모양')


def http_post(url, headers, body, timeout=180):
    """실제 호출. 오류 본문은 로그에 쓰지 않는다(키·내용 보호) — 코드만 올린다."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST',
                                 headers={'Content-Type': 'application/json', 'User-Agent': UA, **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            try:
                w = float(e.headers.get('Retry-After') or 60)
            except ValueError:
                w = 60.0
            raise RateLimited(w, daily=w > 900)
        if e.code in (401, 403):
            raise ProviderError(f'auth {e.code}')
        why = ''
        try:   # 오류 이유 코드·짧은 문구만(요청 내용·키는 기록하지 않는다)
            er = json.loads(e.read()).get('error') or {}
            why = ' ' + re.sub(r'[^A-Za-z0-9 _.,:\-]', '', str(er.get('status') or '') + ' ' + str(er.get('message') or ''))[:90]
        except (ValueError, AttributeError, OSError):
            pass
        raise ProviderError(f'http {e.code}{why}')
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        raise ProviderError('network ' + type(getattr(e, 'reason', e)).__name__)


class Router:
    """켜진 공급자를 차례로 쓴다. 한도 초과·429 면 쉬게 하고 다음으로. 전부 쓸 수 없으면 None."""

    def __init__(self, providers, post=http_post, clock=time.time):
        self.ps = [p for p in providers if p.enabled]
        self.post, self.clock = post, clock
        self.used = {p.name: 0 for p in self.ps}

    def names(self):
        return [p.name for p in self.ps]

    def ask(self, system, user):
        """돌려줌 = (글, 공급자 이름, 모델) 또는 None(오늘 쓸 곳이 없음)."""
        for p in self.ps:
            if not p.available():
                continue
            p.wait_slot()
            url, hdr, body = p.request(system, user)
            try:
                text = p.parse(self.post(url, hdr, body))
            except RateLimited as e:
                p.cool_until = self.clock() + e.wait
                if e.daily:
                    p.dead = True
                log(f'{p.name}: 429 — 다음 공급자로')
                continue
            except ProviderError as e:
                log(f'{p.name}: 실패({e}) — 다음 공급자로')
                p.cool_until = self.clock() + (30 if str(e).startswith('network') else 300)
                if str(e).startswith('auth'):
                    p.dead = True
                continue
            self.used[p.name] += 1
            return text, p.name, p.model
        return None


def build_providers(env=None):
    return [Provider(n, SPEC[n], env) for n in ORDER]


# ---------------------------------------------------------------- 프롬프트·검증
SYSTEM = ('You fill missing multilingual fields for public place records of a travel app. Output JSON only.\n'
          'Input: a JSON list of places with id, name (local name), cat (category), city, cc (country code), have (languages already named), src.\n'
          'For each place return {"id":..., "names":{"ko":..,"en":..,"ja":..,"zh_Hans":..,"zh_Hant":..}, "desc":{"ko":..,"en":..,"ja":..,"zh_Hans":..,"zh_Hant":..}}.\n'
          'Rules:\n'
          '1. names: only for languages missing in "have". Translate or transliterate the given name into that language; if the name is a proper noun, transliterate. '
          'Never invent a different place name. Use null when unsure. zh_Hans uses simplified characters, zh_Hant traditional.\n'
          '2. desc: one short sentence (max 100 characters) saying what kind of place it is, using ONLY the given name, cat, city and cc. '
          'Never add facts such as opening hours, prices, history, ratings, address or dishes. Use null for every language when the given fields are not enough.\n'
          '3. Do not use words of guessing or approximation (like estimate, approximately, maybe, probably).\n'
          '4. Return {"results":[...]} with one entry per input id.')


def make_prompt(items):
    return json.dumps([{'id': i['id'], 'name': i['nm'], 'cat': i['cat'], 'city': i['city'], 'cc': i['cc'], 'have': i['have'], 'src': i['src']} for i in items],
                      ensure_ascii=False, separators=(',', ':'))


def parse_json(text):
    t = (text or '').strip()
    t = re.sub(r'^```(?:json)?\s*|\s*```$', '', t)
    try:
        j = json.loads(t)
    except ValueError:
        return None
    if isinstance(j, list):
        return j
    r = j.get('results') if isinstance(j, dict) else None
    return r if isinstance(r, list) else None


def ok_name(slot, v):
    s = PN.name_ok(v)
    if not s or BANNED.search(s):
        return None
    if slot in ('zh_Hans', 'zh_Hant'):
        sc = PD.han_script(s)
        if (slot == 'zh_Hans' and sc == 'Hant') or (slot == 'zh_Hant' and sc == 'Hans') or PN.KANA.search(s) or PN.HANGUL.search(s):
            return None
    if slot == 'ja' and PN.HANGUL.search(s):
        return None
    if slot == 'ko' and not PN.HANGUL.search(s):
        return None   # 한국어 이름은 한글이 있어야 한다
    if slot == 'en' and not re.search(r'[A-Za-z]', s):
        return None
    return s


def ok_desc(v):
    if not isinstance(v, str):
        return None
    s = re.sub(r'\s+', ' ', unicodedata.normalize('NFC', v)).strip()
    if len(s) < PD.MIN_LEN or len(s) > DESC_MAX or 'http' in s.lower() or bad_text(s) or BANNED.search(s):
        return None
    return s


def validate(results, items):
    """모델 답 → {id: {names, desc}} — 요청하지 않은 id·이미 있는 이름 칸·모양이 틀린 글은 버린다."""
    by = {i['id']: i for i in items}
    out = {}
    for r in results or []:
        if not isinstance(r, dict) or r.get('id') not in by:
            continue
        it = by[r['id']]
        names, desc = {}, {}
        nm = r.get('names') if isinstance(r.get('names'), dict) else {}
        for s in SLOTS:
            if s in it['have']:
                continue
            v = ok_name(s, nm.get(s))
            if v:
                names[s] = v
        d = r.get('desc') if isinstance(r.get('desc'), dict) and not it.get('hd') else {}   # 공개 설명(Wikidata 등)이 있는 장소는 AI 설명을 받지 않는다
        for s in SLOTS:
            v = ok_desc(d.get(s))
            if v and v.casefold() != it['nm'].casefold():
                desc[s] = v
        if names or desc:
            out[it['id']] = {'names': names, 'desc': desc}
    return out


# ---------------------------------------------------------------- 후보 장소
def load_priority():
    j = json.load(open(os.path.join(HERE, '..', 'sources', 'priority_cities.json'), encoding='utf-8'))
    return sorted(j['cities'], key=lambda c: c['rank'])


def _gz(p):
    with open(p, 'rb') as f:
        return json.loads(gzip.decompress(f.read()))


def candidates_from_layer(j, cities, have_names, have_desc, done, limit):
    """col 층 한 파일 → 아직 처리 안 한 일정 장소 후보(우선 도시 순서). 이름·설명이 이미 다 있으면 뺀다."""
    if j.get('schema') != 'collect-1' or j.get('dataset') != 'places':
        return []
    n, cats, cc = len(j['id']), j.get('cats') or [], j.get('cc') or '??'
    col = lambda k: j.get(k) or [None] * n
    ko, en, ja, zh, src, wid, ids, nm = (col(k) for k in ('ko', 'en', 'ja', 'zh', 'src', 'wid', 'id', 'nm'))
    la = lo = 0
    out = []
    for i in range(n):
        la += j['dla'][i]; lo += j['dlo'][i]
        if ids[i] in done or not nm[i] or not (0 <= j['c'][i] < len(cats)) or not itin_of(cats[j['c'][i]]):
            continue
        x, y = lo / 1e5, la / 1e5
        city = next((c for c in cities if c['bbox'][0] <= x <= c['bbox'][2] and c['bbox'][1] <= y <= c['bbox'][3]), None)
        if not city:
            continue
        keys = [k for k in (ids[i], wid[i]) if k]
        zk = PN.zh_generic(PN.name_ok(zh[i])) if zh[i] else None
        own = {'ko': bool(PN.name_ok(ko[i])), 'en': bool(PN.name_ok(en[i])), 'ja': bool(PN.name_ok(ja[i])), 'zh_Hans': zk == 'zh_Hans', 'zh_Hant': zk == 'zh_Hant'}
        have = [s for s in SLOTS if own[s] or any(k in have_names.get(s, ()) for k in keys)]
        has_desc = any(k in have_desc.get(s, ()) for s in SLOTS for k in keys)
        if len(have) == len(SLOTS) and has_desc:
            continue
        out.append({'rank': city['rank'], 'city': city['key'], 'id': ids[i], 'nm': nm[i], 'cat': cats[j['c'][i]], 'cc': cc, 'have': have, 'hd': has_desc,
                    'src': (src[i] or '').split('|')[0] if src[i] else ''})
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- 진행 위치·저장
class State:
    """진행 위치(state.json, 1MB 미만)와 처리한 장소 목록(done.json.gz) — 둘 다 Storage. 같은 장소를 두 번 처리하지 않는다."""

    def __init__(self, store, prefix='processed/ai_fill/'):
        self.store, self.pre = store, prefix
        s = store.get_json(prefix + 'state.json', {}) or {}
        self.s = {'day': s.get('day', ''), 'count': s.get('count', 0), 'files_done': s.get('files_done', 0), 'shard': s.get('shard', 0), 'total': s.get('total', 0), 'by_provider': s.get('by_provider', {})}
        self.done = set(store.get_json(prefix + 'done.json.gz', []) or [])
        if self.s['day'] != today():
            self.s['day'], self.s['count'], self.s['by_provider'] = today(), 0, {}

    def save(self):
        self.store.put_json(self.pre + 'state.json', self.s)
        self.store.put_json(self.pre + 'done.json.gz', sorted(self.done))


def doc(rows, provider_models):
    return {'schema': SCHEMA, 'dataset': 'aifill', 'src': 'ai', 'built': today(), 'license': 'AI-generated', 'models': provider_models, 'rows': rows}


def run(store, col_files, have_names, have_desc, router, max_places=1500, budget_min=55.0, cities=None, clock=time.time, dry_run=False, cap_per_city=None):
    """col_files = 후보를 읽을 층 파일 경로 목록(호출쪽이 순서를 정함). 돌려줌 = 요약 dict."""
    cities = cities or load_priority()
    end = clock() + budget_min * 60
    st = State(store)
    rep = {'places': 0, 'requests': 0, 'rows': 0, 'by_provider': {}, 'stopped': ''}
    if not router.names():
        rep['stopped'] = 'no-provider'
        log('켜진 공급자 없음 — 아무것도 하지 않음')
        return rep
    room = max_places - st.s['count']
    if room <= 0:
        rep['stopped'] = 'daily-cap'
        return rep
    pool = []
    for f in col_files:
        pool += candidates_from_layer(_gz(f), cities, have_names, have_desc, st.done, room)
    pool.sort(key=lambda c: (c['rank'], c['id']))
    pool = pool[:room]
    rows, meta = {}, {}
    for i in range(0, len(pool), BATCH):
        if clock() > end:
            rep['stopped'] = 'time'
            break
        items = pool[i:i + BATCH]
        ans = router.ask(SYSTEM, make_prompt(items))
        rep['requests'] += 1
        if ans is None:
            rep['stopped'] = 'providers-exhausted'
            break
        text, pname, model = ans
        got = validate(parse_json(text), items)
        for it in items:
            st.done.add(it['id'])            # 답이 비어도 다시 보내지 않는다(같은 장소 두 번 금지)
        for rid, v in got.items():
            rows[rid] = {**v['names'], 'd': v['desc'], 'src': 'ai', 'provider': pname, 'model': model, 'date': today(), 'city': next(c['city'] for c in items if c['id'] == rid)}
            meta[pname] = model
        rep['places'] += len(items)
        st.s['count'] += len(items)
        st.s['by_provider'][pname] = st.s['by_provider'].get(pname, 0) + 1
    rep['rows'] = len(rows)
    rep['by_provider'] = dict(router.used)
    if rows and not dry_run:
        n = st.s['shard']; st.s['shard'] = n + 1
        d = doc(rows, meta)
        store.put_json(f"{st.pre}out/aifill-{today()}-{n:04d}.json.gz", d)
    if not dry_run:
        st.save()
    log(f"처리 {rep['places']}곳 · 요청 {rep['requests']}회 · 채움 {rep['rows']}곳 · 공급자 {rep['by_provider']} · 멈춤 {rep['stopped'] or '끝'}")
    return rep


# ---------------------------------------------------------------- 입력 모으기(Storage)
def gather_store(store, work, cities, extra_dir='', max_files=60):
    """Storage 의 수집 층 col_*(최신 run)을 우선 도시의 나라부터 받아 로컬 경로 목록을 돌려준다. 이름·소개 층은 extra_dir(워크플로가 layers_extra 에서 받아 둠)에서 읽는다."""
    import coverage as CV
    names, desc = {}, {}
    if extra_dir and os.path.isdir(extra_dir):
        ex = [os.path.join(extra_dir, f) for f in sorted(os.listdir(extra_dir)) if f.startswith(('names-', 'desc-')) and f.endswith('.json.gz')]
        CV.side_ids(ex, names)
        desc = {k: v for k, v in (CV.desc_ids(ex) or {}).items() if k in SLOTS}
    latest = store.get_json('processed/latest.json', None)
    if not latest:
        return [], names, desc
    paths = latest.get('paths') or {f: f"processed/out/{latest['run']}/{f}" for f in latest.get('files', [])}
    keys = sorted(v for k, v in paths.items() if k.startswith('col_') and not k.startswith('col_info_'))
    ccs = {}
    for k in keys:
        m = re.match(r'col_([A-Z0-9]{2})_', os.path.basename(k))
        if m:
            ccs.setdefault(m.group(1), []).append(k)
    want = []
    for c in cities:
        for k in ccs.get(CITY_CC.get(c['key']), []):
            if k not in want:
                want.append(k)
    want = want[:max_files]
    got = store.pull(want, os.path.join(work, 'col'), strict=False)
    return [got[k] for k in want if k in got], names, desc


# 우선 도시 키 → 나라 코드(수집 층 파일 이름 col_<cc>_N). 목록에 없는 도시는 건너뛴다.
CITY_CC = {'jeju': 'KR', 'seoul': 'KR', 'busan': 'KR', 'gangneung': 'KR', 'gyeongju': 'KR', 'yeosu': 'KR', 'jeonju': 'KR', 'sokcho': 'KR',
           'osaka': 'JP', 'tokyo': 'JP', 'fukuoka': 'JP', 'kyoto': 'JP', 'sapporo': 'JP', 'okinawa_naha': 'JP', 'danang': 'VN', 'nhatrang': 'VN',
           'hochiminh': 'VN', 'hanoi': 'VN', 'bangkok': 'TH', 'taipei': 'TW', 'hongkong': 'HK', 'singapore': 'SG', 'cebu': 'PH', 'manila': 'PH', 'guam': 'GU',
           'bali_south': 'ID', 'shanghai': 'CN', 'paris': 'FR', 'rome': 'IT', 'barcelona': 'ES', 'london': 'GB', 'prague': 'CZ', 'newyork': 'US',
           'losangeles': 'US', 'honolulu': 'US', 'sydney': 'AU', 'istanbul': 'TR', 'dubai': 'AE'}


# ---------------------------------------------------------------- 앱 묶음(publish)에 싣는 층 만들기
EXPORT_MAX = 6 * 1024 * 1024
CREDIT = {'names': 'AI 번역·음역 (Gemini 등 공식 무료 API, 행마다 provider·model·date)', 'desc': 'AI 요약 (근거 칸만 사용, 행마다 provider·model·date)'}


def export_layers(store, out_dir, have_names=None, have_desc=None):
    """Storage processed/ai_fill/out/* → names-ai-NN.json.gz(names-2)·desc-ai-NN.json.gz(desc-1). 돌려줌 = {파일: 행 수}.
    우선순위: 기존 출처(Wikidata·OSM·Overture·수집 열) 이름·설명이 이미 있는 칸은 싣지 않는다 — AI 는 빈 칸만. 출처 표시: 문서에 src='ai'·license·credit, 행에 ai='provider|model|date'."""
    have_names, have_desc = have_names or {}, have_desc or {}
    nrows, drows = {}, {}
    for k in store.list('processed/ai_fill/out/'):
        j = store.get_json(k, None) or {}
        if j.get('schema') != SCHEMA:
            continue
        for rid, v in (j.get('rows') or {}).items():
            tag = '|'.join(str(v.get(x) or '') for x in ('provider', 'model', 'date'))
            nm = {s: v[s] for s in SLOTS if v.get(s) and rid not in have_names.get(s, ())}
            if nm:
                nrows[rid] = {**{s: nm.get(s) for s in SLOTS}, 'ai': tag}
            ds = {s: t for s, t in (v.get('d') or {}).items() if s in SLOTS and t and rid not in have_desc.get(s, ())}
            if ds:
                drows[rid] = {**{s: ds.get(s) for s in SLOTS}, 'ai': tag}
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        if f.startswith(('names-ai-', 'desc-ai-')):
            os.remove(os.path.join(out_dir, f))
    res = {}
    for kind, rows, schema in (('names', nrows, 'names-2'), ('desc', drows, 'desc-1')):
        n = 4
        while rows:
            parts = [{} for _ in range(n)]
            for rid in sorted(rows):
                parts[PD.shard_of(rid, n)][rid] = rows[rid]
            files = {}
            for i, p in enumerate(parts):
                if p:
                    cnt = {s: sum(1 for v in p.values() if v.get(s)) for s in SLOTS}
                    d = {'schema': schema, 'dataset': kind, 'src': 'ai', 'license': 'AI-generated', 'credit': CREDIT[kind], 'page': None, 'built': today(), 'langs': cnt, 'rows': p}
                    fn = f'{kind}-ai-{i:03d}.json.gz'
                    files[fn] = (PD.write_layer(os.path.join(out_dir, fn), d), len(p))
            if max(sz for sz, _ in files.values()) <= EXPORT_MAX or n >= 1024:
                res.update({f: c for f, (_, c) in files.items()})
                break
            for f in files:
                os.remove(os.path.join(out_dir, f))
            n *= 2
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['run', 'providers', 'export'])
    ap.add_argument('--out-dir', default='')
    ap.add_argument('--store', default=os.environ.get('STORE', ''))
    ap.add_argument('--col-dir', default='')
    ap.add_argument('--extra-dir', default='')
    ap.add_argument('--max-places', type=int, default=_env_int('AI_MAX_PLACES', 1500))
    ap.add_argument('--budget-min', type=float, default=55.0)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    if a.cmd == 'export':
        _, names, desc = gather_store(Store(a.store), '', [], a.extra_dir)
        r = export_layers(Store(a.store), a.out_dir, names, desc)
        log(f'내보냄: 파일 {len(r)}개 · 행 {sum(r.values())}')
        return 0
    provs = build_providers()
    on = [p.name for p in provs if p.enabled]
    log('켜진 공급자: ' + (', '.join(on) if on else '없음'))   # 이름만 — 키 값은 읽어도 쓰지 않는다
    if a.cmd == 'providers' or not on:
        return 0
    cities = load_priority()
    store = Store(a.store)
    work = tempfile.mkdtemp(dir=os.environ.get('RUNNER_TEMP') or None)
    if a.col_dir:
        files = sorted(os.path.join(a.col_dir, f) for f in os.listdir(a.col_dir) if f.endswith('.json.gz'))
        _, names, desc = gather_store(store, work, [], a.extra_dir)
    else:
        files, names, desc = gather_store(store, work, cities, a.extra_dir)
    run(store, files, names, desc, Router(provs), a.max_places, a.budget_min, cities, dry_run=a.dry_run)
    return 0


if __name__ == '__main__':
    sys.exit(main())
