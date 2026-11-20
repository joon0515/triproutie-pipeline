"""워크플로 규칙 시험(275기, docs/PIPELINE_CONVENTIONS.md). 네트워크·키 없음.
1) failure-digest 목록 검사는 test_guards.test_digest_names_exist(재사용 작업은 뺌).  2) 모든 작업에 timeout-minutes(또는 재사용 호출).
3) GCP 로그인은 gcp-setup 으로(예외 목록만 직접) · gcp-setup 앞에는 같은 작업 안에 checkout.  4) 공용 실행기 run_job.sh 재시도·요약.
"""
import glob, json, os, re, subprocess, tempfile, unittest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(ROOT, '.github', 'workflows')
# 아직 직접 로그인하는 곳(판이 다르거나 checkout 없는 작업). 옮기면 여기서 지운다 — 새로 늘리지 않는다.
# timeout 없이 도는 옛 작업(기본 6시간). 정할 때 여기서 지운다 — 새로 늘리지 않는다.
NO_TIMEOUT_OK = {('deploy_functions.yml', 'test'), ('places-layers.yml', 'plan'), ('places-layers.yml', 'finalize'), ('places-world.yml', 'plan'), ('places-world.yml', 'finalize'), ('shop-index.yml', 'merge-gaz')}
RAW_AUTH_OK = {'ai-fill.yml', 'deploy_functions.yml', 'patterns.yml', 'places-layers.yml', 'places-world.yml', 'shop-index.yml'}


def load(f):
    d = yaml.safe_load(open(f, encoding='utf-8'))
    d['on'] = d.get('on', d.get(True))
    return d


class Workflows(unittest.TestCase):
    def setUp(self):
        self.files = sorted(glob.glob(os.path.join(WF, '*.yml')))

    def test_timeouts(self):
        for f in self.files:
            for jid, j in load(f)['jobs'].items():
                self.assertTrue('timeout-minutes' in j or 'uses' in j or (os.path.basename(f), jid) in NO_TIMEOUT_OK, f'{os.path.basename(f)}:{jid} timeout-minutes 없음')

    def test_auth_through_composite(self):
        for f in self.files:
            b = os.path.basename(f)
            for jid, j in load(f)['jobs'].items():
                steps = j.get('steps') or []
                uses = [s.get('uses', '') for s in steps]
                if b not in RAW_AUTH_OK:
                    self.assertFalse(any(u.startswith('google-github-actions/auth') for u in uses), f'{b}:{jid} 직접 로그인 — gcp-setup 을 쓸 것')
                if './.github/actions/gcp-setup' in uses:
                    self.assertIn('actions/checkout@v4', uses[:uses.index('./.github/actions/gcp-setup')], f'{b}:{jid} gcp-setup 앞에 checkout')


class RunJob(unittest.TestCase):
    def run_job(self, body, tries):
        with tempfile.TemporaryDirectory() as d:
            cmd = os.path.join(d, 'c.sh'); open(cmd, 'w').write(body)
            summ = os.path.join(d, 's.md'); env = dict(os.environ, GITHUB_STEP_SUMMARY=summ)
            sh = os.path.join(ROOT, 'scripts', 'lib', 'run_job.sh')
            r = subprocess.run(['bash', sh, 'demo', str(tries), cmd], capture_output=True, text=True, env=env)
            evs = [json.loads(l) for l in r.stderr.splitlines() if l.startswith('{')]
            return r.returncode, evs, open(summ).read()

    def test_success_once(self):
        rc, evs, s = self.run_job('echo hi\n', 1)
        self.assertEqual(rc, 0); self.assertEqual([e['event'] for e in evs], ['job.started', 'job.finished']); self.assertIn('| demo | 성공 | 1 |', s)

    def test_failure_no_retry_keeps_exit(self):
        rc, evs, s = self.run_job('exit 3\n', 1)
        self.assertEqual(rc, 3); self.assertEqual(evs[-1]['exit'], 3); self.assertIn('실패(3)', s)

    def test_pipefail(self):
        rc, _, _ = self.run_job('false | cat\n', 1)
        self.assertNotEqual(rc, 0)


class Obs(unittest.TestCase):
    def test_event_hides_secrets_and_retry(self):
        import sys; sys.path.insert(0, os.path.join(ROOT, 'scripts'))
        from lib import obs
        self.assertEqual(obs.event('x.done', api_key='abc', n=1)['api_key'], '***')
        calls, waits = [], []
        def f():
            calls.append(1)
            if len(calls) < 3: raise OSError('boom')
            return 'ok'
        self.assertEqual(obs.retry(f, tries=3, base=1, sleep=waits.append), 'ok'); self.assertEqual(waits, [1, 2])
        with self.assertRaises(OSError): obs.retry(lambda: (_ for _ in ()).throw(OSError()), tries=2, sleep=lambda s: None)


if __name__ == '__main__':
    unittest.main()
