"""watchdog 판단·동작 시험(네트워크 없음 — 가짜 REST)."""
import datetime, json, os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts'))
import watchdog as W  # noqa: E402

NOW = datetime.datetime(2026, 11, 18, 5, 37, tzinfo=datetime.timezone.utc)
CFG = {'cooldown_min': 50, 'fail_backoff_h': 6, 'fail_streak_alert': 3, 'keepalive_hour_utc': 3, 'cache_warn_gib': 8, 'artifact_warn_mib': 400,
       'digest_title': '자동화 실패 모음', 'capacity_title': '저장 용량·비용 경고',
       'workflows': [{'file': 'collect.yml', 'max_age_h': 3}, {'file': 'fx_daily.yml', 'max_age_h': 27}, {'file': 'off.yml', 'max_age_h': 1}]}


def R(i, h_ago, conclusion='success', status='completed', event='workflow_dispatch'):
    t = (NOW - datetime.timedelta(hours=h_ago)).strftime('%Y-%m-%dT%H:%M:%SZ')
    return {'id': i, 'created_at': t, 'updated_at': t, 'status': status, 'conclusion': conclusion if status == 'completed' else None, 'event': event}


class FakeApi:
    def __init__(self, runs, states=None, issues=(), comments=(), cache=0, artifacts=()):
        self.runs, self.log, self.calls = runs, [], 0
        self.states = states or {}
        self.issues, self.comments, self.cache, self.artifacts = list(issues), list(comments), cache, list(artifacts)

    def call(self, method, path, body=None, query=None):
        self.calls += 1
        self.log.append((method, path, body))
        if path == '/actions/workflows':
            files = list(self.runs) + [f for f in self.states if f not in self.runs]
            return 200, {'workflows': [{'id': n + 1, 'path': f'.github/workflows/{f}', 'state': self.states.get(f, 'active')} for n, f in enumerate(files)]}
        if path.endswith('/runs'):
            wid = int(path.split('/')[3])
            f = (list(self.runs) + [f for f in self.states if f not in self.runs])[wid - 1]
            return 200, {'workflow_runs': self.runs.get(f, [])}
        if path == '/issues' and method == 'GET':
            return 200, self.issues
        if path.endswith('/comments') and method == 'GET':
            return 200, self.comments
        if path == '/actions/cache/usage':
            return 200, {'active_caches_size_in_bytes': self.cache, 'active_caches_count': 1}
        if path == '/actions/artifacts':
            return 200, {'artifacts': self.artifacts}
        return 204, None

    def did(self, method, frag):
        return [x for x in self.log if x[0] == method and frag in x[1]]


class Decide(unittest.TestCase):
    wf = {'file': 'collect.yml', 'max_age_h': 3}

    def test_fresh_success_is_ok(self):
        self.assertEqual(W.decide(self.wf, [R(1, 1)], NOW, CFG)['action'], 'ok')

    def test_stale_dispatches(self):
        d = W.decide(self.wf, [R(2, 4, 'cancelled'), R(1, 5)], NOW, CFG)
        self.assertEqual(d['action'], 'dispatch')
        self.assertEqual(d['age_h'], 5.0)

    def test_running_or_pending_never_dispatches(self):
        """대기 실행이 있는데 새로 걸면 GitHub 가 그 대기 실행을 취소한다 → 걸지 않는다."""
        for st in ('queued', 'in_progress', 'pending', 'waiting'):
            self.assertEqual(W.decide(self.wf, [R(3, 0.1, status=st), R(1, 10)], NOW, CFG)['action'], 'running')

    def test_cooldown_after_recent_run(self):
        self.assertEqual(W.decide(self.wf, [R(2, 0.5, 'failure'), R(1, 10)], NOW, CFG)['action'], 'wait')

    def test_three_failures_alert_and_backoff(self):
        runs = [R(5, 2, 'failure'), R(4, 3, 'cancelled'), R(3, 4, 'failure'), R(2, 5, 'timed_out'), R(1, 30)]
        d = W.decide(self.wf, runs, NOW, CFG)
        self.assertEqual((d['fail_streak'], d['first_fail'], d['alert']), (3, 2, True))
        self.assertEqual(d['action'], 'wait')  # 연속 실패 중엔 6시간 간격
        runs = [R(5, 7, 'failure'), R(3, 8, 'failure'), R(2, 9, 'failure'), R(1, 30)]
        self.assertEqual(W.decide(self.wf, runs, NOW, CFG)['action'], 'dispatch')

    def test_success_breaks_streak(self):
        self.assertEqual(W.streak([R(3, 1, 'failure'), R(2, 2), R(1, 3, 'failure')])[0], 1)

    def test_pull_request_runs_ignored(self):
        d = W.decide(self.wf, [R(2, 0.1, event='pull_request'), R(1, 5)], NOW, CFG)
        self.assertEqual(d['action'], 'dispatch')

    def test_never_succeeded(self):
        self.assertEqual(W.decide(self.wf, [], NOW, CFG)['action'], 'dispatch')


class Run(unittest.TestCase):
    def test_dispatch_and_skip(self):
        api = FakeApi({'collect.yml': [R(1, 5)], 'fx_daily.yml': [R(1, 2)]}, states={'off.yml': 'disabled_manually'})
        res = W.run(api, CFG, now=NOW)
        acts = {d['file']: d['action'] for d in res['workflows']}
        self.assertEqual(acts, {'collect.yml': 'dispatch', 'fx_daily.yml': 'ok', 'off.yml': 'off'})
        disp = api.did('POST', '/dispatches')
        self.assertEqual(len(disp), 1)
        self.assertEqual(disp[0][2], {'ref': 'main'})
        self.assertFalse(api.did('PUT', '/enable'))  # 사람이 끈 것은 켜지 않음, keepalive 시각 아님

    def test_inputs_passed(self):
        cfg = dict(CFG, workflows=[{'file': 'places-layers.yml', 'max_age_h': 192, 'inputs': {'mode': 'merge'}}])
        api = FakeApi({'places-layers.yml': [R(1, 200)]})
        W.run(api, cfg, now=NOW)
        self.assertEqual(api.did('POST', '/dispatches')[0][2], {'ref': 'main', 'inputs': {'mode': 'merge'}})

    def test_inactivity_disabled_is_reenabled_then_checked(self):
        api = FakeApi({'collect.yml': [R(1, 1)]}, states={'collect.yml': 'disabled_inactivity'})
        res = W.run(api, dict(CFG, workflows=[{'file': 'collect.yml', 'max_age_h': 3}]), now=NOW)
        self.assertEqual(len(api.did('PUT', '/enable')), 1)
        self.assertEqual(res['workflows'][0]['action'], 'ok')

    def test_keepalive_hour_enables_all_active(self):
        api = FakeApi({'collect.yml': [R(1, 1)], 'fx_daily.yml': [R(1, 1)]}, states={'off.yml': 'disabled_manually'})
        W.run(api, CFG, now=NOW.replace(hour=3))
        self.assertEqual(len(api.did('PUT', '/enable')), 2)  # 켜진 2개만, 사람이 끈 1개는 아님

    def test_dry_run_writes_nothing(self):
        api = FakeApi({'collect.yml': [R(3, 8, 'failure'), R(2, 9, 'failure'), R(1, 10, 'failure')]}, states={'fx_daily.yml': 'disabled_inactivity'})
        W.run(api, CFG, now=NOW.replace(hour=3), dry=True)
        self.assertFalse([x for x in api.log if x[0] in ('POST', 'PUT')])

    def test_three_failures_post_once_to_digest(self):
        runs = {'collect.yml': [R(3, 8, 'failure'), R(2, 9, 'failure'), R(1, 10, 'failure')]}
        api = FakeApi(runs, issues=[{'number': 7, 'title': '자동화 실패 모음'}])
        W.run(api, dict(CFG, workflows=CFG['workflows'][:1]), now=NOW)
        posted = api.did('POST', '/issues/7/comments')
        self.assertEqual(len(posted), 1)
        self.assertIn('<!-- watchdog:collect.yml:1 -->', posted[0][2]['body'])
        api2 = FakeApi(runs, issues=[{'number': 7, 'title': '자동화 실패 모음'}], comments=[{'body': posted[0][2]['body']}])
        W.run(api2, dict(CFG, workflows=CFG['workflows'][:1]), now=NOW)
        self.assertFalse(api2.did('POST', '/issues/7/comments'))  # 같은 실패 묶음은 한 번만

    def test_digest_issue_created_when_missing(self):
        api = FakeApi({'collect.yml': [R(3, 8, 'failure'), R(2, 9, 'failure'), R(1, 10, 'failure')]})
        W.run(api, dict(CFG, workflows=CFG['workflows'][:1]), now=NOW)
        new = [x for x in api.log if x[0] == 'POST' and x[1] == '/issues']
        self.assertEqual(new[0][2]['title'], '자동화 실패 모음')

    def test_capacity_warning(self):
        api = FakeApi({'collect.yml': [R(1, 1)]}, cache=9 * 2 ** 30, artifacts=[{'size_in_bytes': 450 * 2 ** 20}, {'size_in_bytes': 10 ** 9, 'expired': True}])
        res = W.run(api, dict(CFG, workflows=CFG['workflows'][:1]), now=NOW)
        self.assertEqual(len(res['capacity_warn']), 2)
        self.assertEqual(res['capacity']['artifact_mib'], 450.0)
        self.assertTrue([x for x in api.log if x[0] == 'POST' and x[1] == '/issues' and x[2]['title'] == '저장 용량·비용 경고'])

    def test_call_budget_small(self):
        api = FakeApi({f'w{i}.yml': [R(1, 1)] for i in range(12)})
        W.run(api, dict(CFG, workflows=[{'file': f'w{i}.yml', 'max_age_h': 3} for i in range(12)]), now=NOW)
        self.assertLess(api.calls, 40)  # GITHUB_TOKEN 1,000회/시간 안에서 여유


class Config(unittest.TestCase):
    def test_config_files_exist_and_not_manual_only(self):
        root = os.path.join(HERE, '..')
        cfg = json.load(open(os.path.join(root, 'sources', 'watchdog.json'), encoding='utf-8'))
        import yaml
        for wf in cfg['workflows']:
            p = os.path.join(root, '.github', 'workflows', wf['file'])
            self.assertTrue(os.path.exists(p), wf['file'])
            y = yaml.safe_load(open(p, encoding='utf-8'))
            on = y.get('on', y.get(True))
            self.assertIn('workflow_dispatch', on, wf['file'])  # 다시 걸 수 있어야 한다
            for k in (wf.get('inputs') or {}):
                self.assertIn(k, (on['workflow_dispatch'] or {}).get('inputs', {}), f"{wf['file']} 입력 {k}")

    def test_failure_digest_watches_watchdog(self):
        import yaml
        y = yaml.safe_load(open(os.path.join(HERE, '..', '.github', 'workflows', 'failure-digest.yml'), encoding='utf-8'))
        on = y.get('on', y.get(True))
        self.assertIn('watchdog', on['workflow_run']['workflows'])


if __name__ == '__main__':
    unittest.main()
