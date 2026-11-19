"""ai_fill — 공급자 호출은 전부 가짜(mock). 네트워크·키 없음."""
import gzip, json, os, sys, tempfile, unittest
ROOT = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, os.path.join(ROOT, 'scripts')); sys.path.insert(0, os.path.join(ROOT, 'scripts', 'collect'))
import ai_fill as A  # noqa: E402
from common import Store  # noqa: E402

CITIES = [{'rank': 1, 'key': 'tokyo', 'bbox': [139.0, 35.0, 140.0, 36.0]}]


def layer(names):
    n = len(names)
    return {'schema': 'collect-1', 'dataset': 'places', 'cc': 'JP', 'cats': ['restaurant', 'parking'], 'id': [f'p{i}' for i in range(n)], 'nm': names,
            'ko': [None] * n, 'en': [None] * n, 'ja': [None] * n, 'zh': [None] * n, 'src': ['fsq'] * n, 'wid': [None] * n, 'c': [0] * n,
            'dla': [3550000] + [0] * (n - 1), 'dlo': [13950000] + [0] * (n - 1)}


def good(items, mode='names'):
    if mode == 'desc':
        return json.dumps({'results': [{'i': i['i'], 'ko': '도쿄의 식당', 'en': 'A restaurant in Tokyo'} for i in items]}, ensure_ascii=False)
    return json.dumps({'results': [{'i': i['i'], 'ko': '맛집', 'en': 'Shop', 'ja': '店', 'zh_Hans': '国家', 'zh_Hant': '國家'} for i in items]}, ensure_ascii=False)


class FakeRouter:
    def __init__(self, fn=good):
        self.fn, self.used = fn, {'gemini': 0}

    def names(self):
        return ['gemini']

    def ask(self, system, user):
        items = json.loads(user)
        mode = 'names' if 'have' in items[0] else 'desc'
        self.used['gemini'] += 1
        try:
            return self.fn(items, mode), 'gemini', 'm1'
        except TypeError:
            return self.fn(items), 'gemini', 'm1'


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.f = os.path.join(self.d, 'col_JP_0.json.gz')
        with gzip.open(self.f, 'wt', encoding='utf-8') as g:
            json.dump(layer([f'Shop {i}' for i in range(40)]), g)
        self.store = Store(os.path.join(self.d, 'store'))

    def test_run_fills_and_never_repeats(self):
        r = A.run(self.store, [self.f], {}, {}, FakeRouter(), 100, 5, CITIES, desc_max=40)
        self.assertEqual(r['places'], 40); self.assertEqual(r['rows'], 40)
        self.assertEqual(r['requests'], 1 + 4)   # 이름 50곳 묶음 1회 + 설명 10곳 묶음 4회
        out = [k for k in self.store.list('processed/ai_fill/out/')]
        row = next(iter(self.store.get_json(out[0])['rows'].values()))
        self.assertEqual((row['src'], row['provider'], row['model']), ('ai', 'gemini', 'm1'))
        self.assertTrue(row['date'])
        r2 = A.run(self.store, [self.f], {}, {}, FakeRouter(), 100, 5, CITIES)   # 같은 장소 두 번 처리 금지
        self.assertEqual(r2['places'], 0)

    def test_daily_cap(self):
        r = A.run(self.store, [self.f], {}, {}, FakeRouter(), 20, 5, CITIES, desc_max=0)
        self.assertEqual(r['places'], 20)
        r = A.run(self.store, [self.f], {}, {}, FakeRouter(), 20, 5, CITIES, desc_max=0)
        self.assertEqual((r['places'], r['stopped']), (0, 'daily-cap'))

    def test_existing_names_kept_and_only_missing_asked(self):
        seen = []

        def fn(items, mode='names'):
            seen.append(items)
            return json.dumps({'results': [{'i': i['i'], 'ko': '새이름', 'en': 'New'} for i in items]}, ensure_ascii=False) if mode == 'names' else '{"results":[]}'
        have = {'ko': {f'p{i}' for i in range(40)}}
        A.run(self.store, [self.f], have, {}, FakeRouter(fn), 100, 5, CITIES, desc_max=0)
        rows = self.store.get_json(self.store.list('processed/ai_fill/out/')[0])['rows']
        self.assertTrue(all('ko' not in v for v in rows.values()))   # 이미 있는 칸은 덮지 않음
        self.assertTrue(all(v['en'] == 'New' for v in rows.values()))

    def test_no_provider_does_nothing(self):
        class R0(FakeRouter):
            def names(self):
                return []
        r = A.run(self.store, [self.f], {}, {}, R0(), 100, 5, CITIES)
        self.assertEqual(r['stopped'], 'no-provider')
        self.assertEqual(self.store.list('processed/'), [])

    def test_validation_drops_bad(self):
        items = [{'id': 'a', 'nm': 'X', 'have': []}]
        res = [{'i': 0, 'ko': 'abc', 'en': '한글', 'zh_Hans': '國', 'ja': '店', 'zh_Hant': '國家'}, {'i': 9, 'en': 'Q'}]
        v = A.validate(res, items, 'names')
        self.assertEqual(list(v), ['a'])
        self.assertEqual(v['a']['names'], {'ja': '店', 'zh_Hant': '國家'})
        d = A.validate([{'i': 0, 'ko': '대략 맛있는 곳', 'en': 'A cafe', 'ja': 'http://x'}], items, 'desc')
        self.assertEqual(d['a']['desc'], {'en': 'A cafe'})
        self.assertEqual(A.validate([{'i': 0, 'en': 'A cafe'}], [{'id': 'a', 'nm': 'X', 'have': [], 'hd': True}], 'desc'), {})

    def test_banned_word_estimate(self):
        self.assertIsNone(A.ok_desc('추정 인기 식당'))
        self.assertIsNone(A.ok_desc('Estimated 4 stars'))

    def test_router_falls_over_on_429_and_respects_rpm(self):
        env = {'GEMINI_API_KEY': 'k1', 'GROQ_API_KEY': 'k2'}
        t = [1000.0]
        slept = []
        ps = [A.Provider(n, A.SPEC[n], env, clock=lambda: t[0], sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s))) for n in A.ORDER]
        calls = []

        def post(url, hdr, body):
            calls.append(url)
            if 'googleapis' in url:
                raise A.RateLimited(60)
            return {'choices': [{'message': {'content': '{"results":[]}'}}]}
        r = A.Router(ps, post=post, clock=lambda: t[0])
        self.assertEqual(r.names(), ['gemini', 'gemini_b', 'groq'])
        a = r.ask('s', 'u')
        self.assertEqual(a[1], 'groq')
        self.assertEqual(len(calls), 3)   # gemini·gemini_b 429 → groq
        r.ask('s', 'u')
        self.assertEqual(len(calls), 4)   # 앞 둘은 쉬는 중 — 다시 부르지 않음
        # 분당 한도
        ps[3].rpm = 2
        for _ in range(3):
            r.ask('s', 'u')
        self.assertTrue(slept)   # 분당 2회 → 30초 간격으로 예약

    def test_threads_respect_rpm(self):
        import concurrent.futures
        p = A.Provider('gemini', A.SPEC['gemini'], {'GEMINI_API_KEY': 'k', 'AI_GEMINI_RPM': '600', 'AI_GEMINI_RPD': '1000'}, sleep=lambda s: None)
        r = A.Router([p], post=lambda u, h, b: {'candidates': [{'content': {'parts': [{'text': '{}'}]}}]})
        with concurrent.futures.ThreadPoolExecutor(8) as ex:
            list(ex.map(lambda _: r.ask('s', 'u'), range(50)))
        self.assertEqual(r.used['gemini'], 50)
        self.assertEqual(p.day_count, 50)

    def test_daily_limit_per_provider(self):
        env = {'GEMINI_API_KEY': 'k', 'AI_GEMINI_RPD': '2'}
        p = A.Provider('gemini', A.SPEC['gemini'], env, sleep=lambda s: None)
        r = A.Router([p], post=lambda u, h, b: {'candidates': [{'content': {'parts': [{'text': '{}'}]}}]})
        self.assertTrue(r.ask('s', 'u')); self.assertTrue(r.ask('s', 'u'))
        self.assertIsNone(r.ask('s', 'u'))

    def test_key_only_in_header_and_disabled_without_secret(self):
        env = {'GEMINI_API_KEY': 'SECRETVALUE'}
        p = A.Provider('gemini', A.SPEC['gemini'], env)
        url, hdr, body = p.request('s', 'u')
        self.assertNotIn('SECRETVALUE', url); self.assertNotIn('SECRETVALUE', json.dumps(body))
        self.assertFalse(A.Provider('cloudflare', A.SPEC['cloudflare'], {'CF_AI_TOKEN': 'x'}).enabled)   # 두 값이 다 있어야 켜짐
        self.assertEqual([x.name for x in A.build_providers({})  if x.enabled], [])

    def test_source_has_no_secret_logging(self):
        src = open(os.path.join(ROOT, 'scripts', 'ai_fill.py'), encoding='utf-8').read()
        self.assertNotIn('print(env', src); self.assertNotIn("log(f'{hdr", src)


if __name__ == '__main__':
    unittest.main()


class Export(unittest.TestCase):
    def test_export_priority_and_provenance(self):
        d = tempfile.mkdtemp(); st = Store(os.path.join(d, 's'))
        st.put_json('processed/ai_fill/out/aifill-x-0000.json.gz', {'schema': 'aifill-1', 'rows': {
            'a': {'ko': '가', 'en': 'A', 'd': {'ko': '도쿄의 식당'}, 'provider': 'gemini', 'model': 'm', 'date': '2026-11-20'},
            'b': {'en': 'B', 'd': {}, 'provider': 'gemini', 'model': 'm', 'date': '2026-11-20'}}})
        out = os.path.join(d, 'o')
        r = A.export_layers(st, out, {'en': {'a'}}, {})   # a 의 en 은 기존 출처에 이미 있음 → 싣지 않는다
        self.assertEqual(sorted(r), sorted(f for f in os.listdir(out)))
        rows = {}
        for f in os.listdir(out):
            j = json.load(gzip.open(os.path.join(out, f), 'rt', encoding='utf-8'))
            self.assertEqual((j['src'], j['license']), ('ai', 'AI-generated'))
            self.assertIn(j['schema'], ('names-2', 'desc-1'))
            rows.setdefault(j['schema'], {}).update(j['rows'])
        self.assertIsNone(rows['names-2']['a']['en']); self.assertEqual(rows['names-2']['a']['ko'], '가')
        self.assertEqual(rows['names-2']['b']['ai'], 'gemini|m|2026-11-20')
        self.assertEqual(rows['desc-1']['a']['ko'], '도쿄의 식당')

    def test_publish_entry_marks_ai(self):
        import publish_layers as P
        d = tempfile.mkdtemp(); st = Store(os.path.join(d, 's'))
        st.put_json('processed/ai_fill/out/o.json.gz', {'schema': 'aifill-1', 'rows': {'a': {'ko': '가', 'provider': 'g', 'model': 'm', 'date': 'd'}}})
        A.export_layers(st, os.path.join(d, 'o'))
        e = P.describe(os.path.join(d, 'o', 'names-ai-%03d.json.gz' % [int(f[9:12]) for f in os.listdir(os.path.join(d, 'o'))][0]))
        self.assertEqual((e['kind'], e['prio'], e['license']), ('names5', 9, 'AI-generated'))


class Wiring(unittest.TestCase):
    def test_main_pieces_exist(self):   # 11/20 합치다 gather_store 가 빠졌던 일 방지
        for n in ('gather_store', 'CITY_CC', 'export_layers', 'run', 'main'):
            self.assertTrue(hasattr(A, n), n)


class Retry(unittest.TestCase):
    def test_503_retried_then_ok(self):
        n = []

        def post(u, h, b):
            n.append(1)
            if len(n) < 3:
                raise A.ProviderError('http 503 UNAVAILABLE')
            return {'candidates': [{'content': {'parts': [{'text': '{}'}]}}]}
        p = A.Provider('gemini', A.SPEC['gemini'], {'GEMINI_API_KEY': 'k'}, sleep=lambda s: None)
        r = A.Router([p], post=post, sleep=lambda s: None)
        self.assertTrue(r.ask('s', 'u'))
        self.assertEqual(len(n), 3)


class Wait(unittest.TestCase):
    def test_retry_in(self):
        t = [100.0]
        p = A.Provider('gemini', A.SPEC['gemini'], {'GEMINI_API_KEY': 'k'}, clock=lambda: t[0])
        r = A.Router([p], post=None, clock=lambda: t[0])
        p.cool_until = 130.0
        self.assertEqual(round(r.retry_in()), 31)
        p.dead = True
        self.assertIsNone(r.retry_in())
