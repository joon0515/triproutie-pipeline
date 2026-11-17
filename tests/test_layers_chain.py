"""collect.yml chain 의 places-layers 보충 판단 시험 — 워크플로 파일 안의 파이썬 토막을 그대로 꺼내 돌린다(사본을 따로 두지 않는다)."""
import json, os, subprocess, sys, textwrap, unittest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
WF = os.path.join(HERE, '..', '.github', 'workflows')


def snippet():
    y = yaml.safe_load(open(os.path.join(WF, 'collect.yml'), encoding='utf-8'))
    step = [s for s in y['jobs']['chain']['steps'] if s.get('name', '').startswith('places-layers')][0]
    run = step['run']
    return textwrap.dedent(run[run.index("<<'PY'\n") + 6:run.index('\nPY\n')]), run


def decide(now, runs):
    code, _ = snippet()
    r = subprocess.run([sys.executable, '-c', code, json.dumps(runs)], capture_output=True, text=True, env={**os.environ, 'NOW': now})
    assert r.returncode == 0, r.stderr
    return r.stdout.split(':')[0]


def R(title, at, status='completed'):
    return {'displayTitle': title, 'createdAt': at, 'status': status}


class LayersChain(unittest.TestCase):
    def test_state_on_1117_only_old_manual_run(self):
        """지금 상태(11/14 수동 1회뿐, 이름 없는 옛 실행) → 전 지역 빌드를 건다."""
        self.assertEqual(decide('2026-11-17T01:00:00+00:00', [R('places-layers', '2026-11-14T03:25:59Z')]), 'full')

    def test_full_once_a_month_even_if_it_failed(self):
        runs = [R('places-layers full all', '2026-11-17T01:10:00Z')]
        self.assertEqual(decide('2026-11-18T01:00:00+00:00', runs), 'skip')
        self.assertEqual(decide('2026-11-25T01:00:00+00:00', runs), 'merge')
        self.assertEqual(decide('2026-12-01T05:00:00+00:00', runs + [R('places-layers merge ', '2026-11-25T01:01:00Z')]), 'skip')  # 2일 19시 전
        self.assertEqual(decide('2026-12-02T19:30:00+00:00', runs + [R('places-layers merge ', '2026-11-30T01:01:00Z')]), 'full')

    def test_scheduled_full_counts(self):
        runs = [R('places-layers 40 17 2 * * ', '2026-12-02T17:55:00Z')]
        self.assertEqual(decide('2026-12-02T19:30:00+00:00', runs), 'skip')
        self.assertEqual(decide('2026-12-10T00:00:00+00:00', runs), 'merge')

    def test_priority_only_full_does_not_count_as_month_build(self):
        self.assertEqual(decide('2026-11-20T00:00:00+00:00', [R('places-layers full priority', '2026-11-19T00:00:00Z')]), 'full')

    def test_running_blocks(self):
        self.assertEqual(decide('2026-11-17T01:00:00+00:00', [R('places-layers full all', '2026-11-17T00:50:00Z', 'in_progress')]), 'skip')
        self.assertEqual(decide('2026-11-17T01:00:00+00:00', [R('places-layers', '2026-10-01T00:00:00Z', 'queued')]), 'skip')

    def test_empty_list(self):
        self.assertEqual(decide('2026-11-17T01:00:00+00:00', []), 'full')
        self.assertEqual(decide('2026-12-01T01:00:00+00:00', []), 'merge')

    def test_workflow_wiring(self):
        """연쇄가 거는 입력과 실행 이름이 맞물리는지, 워크플로 파일이 읽히는지."""
        _, run = snippet()
        self.assertIn('gh workflow run places-layers.yml --ref main -f mode=full -f groups=all', run)
        self.assertIn('gh workflow run places-layers.yml --ref main -f mode=merge', run)
        y = yaml.safe_load(open(os.path.join(WF, 'places-layers.yml'), encoding='utf-8'))
        self.assertEqual(y['run-name'], "places-layers ${{ inputs.mode || github.event.schedule || 'full' }} ${{ inputs.groups || '' }}")
        on = y.get('on') or y.get(True)
        self.assertEqual({'mode', 'groups'} - set(on['workflow_dispatch']['inputs']), set())
        self.assertIn('40 17 2 * *', [c['cron'] for c in on['schedule']])


if __name__ == '__main__':
    unittest.main()
