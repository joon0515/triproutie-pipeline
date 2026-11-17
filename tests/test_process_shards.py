"""가공을 partition → countries(나라 조각 n 대 동시에) → merge 로 나눠 돌린 결과가 한 러너가 전부 한 결과(stage=all)와 같은지."""
import os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts', 'collect'))
sys.path.insert(0, HERE)
import common as K  # noqa: E402
import process as P  # noqa: E402
from test_process_stream import layers, raw, row, synth  # noqa: E402


def staged(store, n, batch='b1', skip=(), **kw):
    P.process(store, None, stage='partition', **kw)
    for i in range(n):
        if i not in skip:
            P.process(store, None, stage='countries', shard=(i, n), batch=batch, **kw)
    return P.process(store, None, stage='merge', batch=batch)


class Shards(unittest.TestCase):
    def test_shard_split_is_stable_and_complete(self):
        ccs = [f'{a}{b}' for a in 'ABCDEFGH' for b in 'KRSTUV']
        for n in (1, 3, 8):
            got = [P.cc_shard(cc, n) for cc in ccs]
            self.assertEqual(got, [P.cc_shard(cc, n) for cc in ccs]); self.assertTrue(all(0 <= g < n for g in got))
        self.assertGreater(len({P.cc_shard(cc, 8) for cc in ccs}), 5)  # 한쪽으로 몰리지 않음

    def test_staged_equals_single_runner(self):
        d = tempfile.mkdtemp(); one, many = K.Store(os.path.join(d, 'one')), K.Store(os.path.join(d, 'many'))
        synth(one); synth(many)
        P.process(one, None)
        run, st = staged(many, 4)
        self.assertEqual(layers(many), layers(one))
        self.assertEqual(st['pending'], {'raw_files': 0, 'countries': []})
        self.assertEqual(many.get_json('processed/latest.json')['counts'], one.get_json('processed/latest.json')['counts'])
        self.assertEqual(many.list('processed/inbox/'), [])
        self.assertEqual(sorted(st['countries']), ['JP', 'KR'])

    def test_shards_do_not_touch_shared_files(self):
        """나라 조각 실행은 state.json·latest.json 을 쓰지 않는다(동시에 돌아도 서로 덮어쓰지 않음)."""
        d = tempfile.mkdtemp(); s = K.Store(os.path.join(d, 's'))
        synth(s)
        P.process(s, None, stage='partition')
        before = (s.get('processed/state.json'), s.get('processed/latest.json'))
        for i in range(3):
            P.process(s, None, stage='countries', shard=(i, 3), batch='x')
        self.assertEqual((s.get('processed/state.json'), s.get('processed/latest.json')), before)
        self.assertEqual(before[1], None)

    def test_failed_shard_keeps_old_layers_and_is_picked_up_next_run(self):
        d = tempfile.mkdtemp(); s = K.Store(os.path.join(d, 's'))
        synth(s)
        n = 2
        lost = P.cc_shard('JP', n)
        self.assertNotEqual(lost, P.cc_shard('KR', n))
        _, st = staged(s, n, batch='b1', skip={lost})          # JP 조각이 돌지 못함
        self.assertEqual(sorted(layers(s)), ['KR'])
        self.assertTrue(any(k.split('/')[-1].startswith('JP~') for k in s.list('processed/inbox/')))  # 대기함에 남아 있음
        raw(s, 'aa', 'r~KR', 99, [row('aa', 'KRnew', '새 가게', 36.5, 127.9, 'KR')])
        _, st = staged(s, n, batch='b2')
        got = layers(s)
        self.assertEqual(sorted(got), ['JP', 'KR']); self.assertIn('aa:KRnew', [g[0] for g in got['KR']])
        self.assertEqual(s.list('processed/inbox/'), [])

    def test_merge_ignores_other_batch(self):
        d = tempfile.mkdtemp(); s = K.Store(os.path.join(d, 's'))
        synth(s)
        P.process(s, None, stage='partition')
        P.process(s, None, stage='countries', shard=(0, 1), batch='old')
        _, st = P.process(s, None, stage='merge', batch='new')
        self.assertEqual(st['countries'], {})
        _, st = P.process(s, None, stage='merge', batch='old')
        self.assertEqual(sorted(st['countries']), ['JP', 'KR'])

    def test_interrupted_shard_resumes(self):
        """조각이 시간 예산에 걸려 나라 가운데서 멈춰도(dirty 는 조각 파일에) 다음 실행들이 이어받아 같은 결과."""
        d = tempfile.mkdtemp(); one, many = K.Store(os.path.join(d, 'one')), K.Store(os.path.join(d, 'many'))
        synth(one); synth(many)
        P.process(one, None)
        keep = P.PUSH_BANDS
        P.PUSH_BANDS = 3
        try:
            for r in range(60):
                _, st = staged(many, 2, batch=f'b{r}', budget_min=0)
                if not st['pending']['countries'] and not many.list('processed/inbox/'):
                    break
        finally:
            P.PUSH_BANDS = keep
        self.assertGreater(r, 1)
        self.assertEqual(layers(many), layers(one)); self.assertEqual(many.get_json('processed/state.json')['dirty'], [])


if __name__ == '__main__':
    unittest.main()
