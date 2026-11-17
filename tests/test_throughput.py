"""처리량 확대 시험: 큰 덤프를 조각으로 잘라 이어받기(sliced) · 실패 조각 자동 재시도 순서(plan) · 워크플로 한도."""
import datetime, os, re, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, os.path.join(ROOT, 'scripts', 'collect'))
import common as K  # noqa: E402
import collectors as C  # noqa: E402
import run as R  # noqa: E402

REG = R.load_registry()


class Ctx:
    def __init__(self, st=None):
        self.st, self.notes = st if st is not None else {}, []

    def note(self, m):
        self.notes.append(m)


class Slices(unittest.TestCase):
    def test_strips_cover_box_without_overlap(self):
        boxes = [[122.9, 24.0, 146.0, 45.6], [-10.0, 35.0, 5.0, 44.0]]
        for n in (1, 4, 16):
            st = C.lon_strips(boxes, n)
            self.assertEqual(len(st), n * len(boxes))
            for b, part in zip(boxes, [st[:n], st[n:]]):
                self.assertEqual((part[0][0], part[-1][2]), (b[0], b[2]))
                self.assertTrue(all(part[i][2] == part[i + 1][0] for i in range(n - 1)))  # 이어 붙음
                self.assertEqual([p[4] for p in part], [False] * (n - 1) + [True])
                for x in (b[0], b[2], (b[0] + b[2]) / 2, part[0][2]):  # 경계 값도 정확히 한 조각에만
                    self.assertEqual(sum(1 for p in part if p[0] <= x and (x <= p[2] if p[4] else x < p[2])), 1)
        self.assertEqual(C.xcond('lon', (1, 0, 2, 9, False)), 'lon >= 1 and lon < 2')
        self.assertEqual(C.xcond('lon', (1, 0, 2, 9, True)), 'lon >= 1 and lon <= 2')

    def test_first_run_is_single_query_like_before(self):
        ctx, seen = Ctx(), []
        C.sliced(ctx, 'r1', [[0, 0, 8, 8]], seen.append)
        self.assertEqual(seen, [(0, 0, 8, 8, True)])
        self.assertEqual(ctx.st['slices'], {'key': 'r1', 'n': 1, 'done': []})

    def test_timeout_makes_finer_slices_then_resumes(self):
        ctx, seen = Ctx(), []

        def slow(sp):  # 조각 폭이 2도보다 넓으면 시간에 걸린다
            if sp[2] - sp[0] > 2:
                raise K.Stop('TIME')
            seen.append(sp)

        with self.assertRaises(K.Stop):
            C.sliced(ctx, 'r1', [[0, 0, 8, 8]], slow)
        self.assertEqual((ctx.st['slices']['n'], ctx.st['slices']['done']), (4, []))
        budget = [2]

        def two_per_run(sp):
            if budget[0] == 0:
                raise K.Stop('TIME')
            budget[0] -= 1; slow(sp)

        with self.assertRaises(K.Stop):
            C.sliced(ctx, 'r1', [[0, 0, 8, 8]], two_per_run)
        self.assertEqual(ctx.st['slices'], {'key': 'r1', 'n': 4, 'done': [0, 1]})  # 나아간 만큼 적고, 더 잘게 자르지는 않음
        budget[0] = 9
        C.sliced(ctx, 'r1', [[0, 0, 8, 8]], two_per_run)
        self.assertEqual([s[0] for s in seen], [0, 2, 4, 6])  # 끝낸 조각을 다시 읽지 않음
        self.assertEqual(ctx.st['slices'], {'key': 'r1', 'n': 4, 'done': []})
        seen.clear()
        C.sliced(ctx, 'r2', [[0, 0, 8, 8]], seen.append)  # 새 릴리스: 처음부터, 조각 수는 기억
        self.assertEqual(len(seen), 4)

    def test_slice_cap_and_other_stops(self):
        ctx = Ctx({'slices': {'key': 'r', 'n': 64, 'done': []}})

        def t(sp):
            raise K.Stop('TIME')
        with self.assertRaises(K.Stop):
            C.sliced(ctx, 'r', [[0, 0, 8, 8]], t)
        self.assertEqual(ctx.st['slices']['n'], 64)
        ctx = Ctx()

        def lim(sp):
            raise K.Stop('LIMIT')
        with self.assertRaises(K.Stop):
            C.sliced(ctx, 'r', [[0, 0, 8, 8]], lim)
        self.assertEqual(ctx.st['slices']['n'], 1)  # 시간 말고 다른 멈춤은 조각 수를 바꾸지 않음


class Retry(unittest.TestCase):
    def idx(self, **kw):
        base = {'last_ok': None, 'req': 0, 'req_date': None, 'fail_streak': 0, 'last_run': None}
        return dict(base, **kw)

    def test_recent_failure_goes_first(self):
        ago = (K.now() - datetime.timedelta(minutes=50)).isoformat()
        index = {'overture_places/japan:JP': self.idx(fail_streak=1, last_run=ago)}
        p = R.plan(REG, index, env={})
        col = [x for x in p if x['source'] == 'overture_places']
        self.assertEqual(col[0], {'source': 'overture_places', 'shard': 'japan:JP'})

    def test_long_failing_shard_waits_six_hours(self):
        recent = (K.now() - datetime.timedelta(hours=2)).isoformat()
        old = (K.now() - datetime.timedelta(hours=7)).isoformat()
        sh = [x['shard'] for x in R.plan(REG, {}, env={}, max_jobs=400) if x['source'] == 'wikivoyage']
        self.assertIn('en', sh)
        for last_run, want in ((recent, False), (old, True)):
            index = {'wikivoyage/en': self.idx(fail_streak=5, last_run=last_run)}
            got = any(x == {'source': 'wikivoyage', 'shard': 'en'} for x in R.plan(REG, index, env={}, max_jobs=400))
            self.assertEqual(got, want)

    def test_caps(self):
        p = R.plan(REG, {}, env={'HAS_FSQ_TOKEN': 'true'}, max_jobs=400)
        n = lambda s: sum(1 for x in p if x['source'] == s)
        self.assertEqual((n('overture_places'), n('fsq_os_places'), n('commons_photos')), (8, 6, 1))


class Workflows(unittest.TestCase):
    """공식 한도(docs/THROUGHPUT.md): 작업 6시간 · 동시 20 · matrix 256."""

    def test_limits(self):
        import yaml
        tot = 0
        for f in ('collect.yml', 'process.yml'):
            y = yaml.safe_load(open(os.path.join(ROOT, '.github', 'workflows', f), encoding='utf-8'))
            for name, job in y['jobs'].items():
                self.assertLessEqual(job.get('timeout-minutes', 360), 360, f'{f}:{name}')
            mp = [j['strategy']['max-parallel'] for j in y['jobs'].values() if 'strategy' in j]
            tot += max(mp)
        self.assertLessEqual(tot, 20)  # 수집 조각 + 가공 조각이 함께 돌아도 무료 플랜 동시 20 안쪽

    def test_process_stages_in_order(self):
        import yaml
        y = yaml.safe_load(open(os.path.join(ROOT, '.github', 'workflows', 'process.yml'), encoding='utf-8'))
        j = y['jobs']
        self.assertEqual(j['countries']['needs'], 'partition'); self.assertIn('countries', j['merge']['needs']); self.assertEqual(j['place-desc']['needs'], 'merge')
        self.assertEqual(j['publish']['needs'], ['merge', 'place-desc']); self.assertTrue(j['place-desc'].get('continue-on-error'))  # 11/17: 소개 단계가 실패해도 게시는 계속
        n = len(j['countries']['strategy']['matrix']['shard'])
        txt = open(os.path.join(ROOT, '.github', 'workflows', 'process.yml'), encoding='utf-8').read()
        self.assertIn(f'--shard "${{{{ matrix.shard }}}}/{n}"', txt)
        self.assertEqual(len(re.findall(r'--batch "\$\{\{ github\.run_id \}\}-\$\{\{ github\.run_attempt \}\}"', txt)), 2)
        self.assertNotIn('upload-artifact', txt); self.assertNotIn('actions/cache', txt)  # 산출 데이터는 GitHub 에 남기지 않는다


if __name__ == '__main__':
    unittest.main()
