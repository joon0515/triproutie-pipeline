"""(295기) tools/gcs_meter/gcloud — 실행당 GCS 다운로드 바이트 상한. 가짜 gcloud 로 네트워크 없이 시험."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
SHIM = os.path.join(ROOT, 'tools', 'gcs_meter', 'gcloud')

FAKE = r'''#!/usr/bin/env python3
import sys, os, json
a = sys.argv[1:]
open(os.environ['FAKE_LOG'], 'a').write(json.dumps(a) + '\n')
sizes = json.loads(os.environ.get('FAKE_SIZES', '{}'))
if a[:3] == ['storage', 'du', '-s']:
    for s in a[3:]:
        if s in sizes:
            print(f"{sizes[s]}  {s}")
        else:
            sys.stderr.write('One or more URLs matched no objects.\n'); sys.exit(1)
    sys.exit(0)
if a[:2] == ['storage', 'ls']:
    for x in json.loads(os.environ.get('FAKE_DAY', '[]')):
        print(a[2] + x)
    sys.exit(0)
if a[:2] == ['storage', 'cat']:
    sys.stdout.write('x'); sys.exit(0)
if '-I' in a:
    sys.stdin.read()
sys.exit(0)
'''


class GcsMeter(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.real = os.path.join(self.d, 'real_gcloud')
        open(self.real, 'w').write(FAKE)
        os.chmod(self.real, 0o755)
        self.log = os.path.join(self.d, 'log')
        self.env = dict(os.environ, GCS_METER_REAL=self.real, GCS_METER_FILE=os.path.join(self.d, 'm.json'), FAKE_LOG=self.log,
                        GCS_DL_CAP_BYTES='1000', FAKE_SIZES=json.dumps({'gs://b/a': 400, 'gs://b/big': 5000, 'gs://b/c/*.gz': 300}))

    def run_shim(self, *a, stdin=None):
        # 부른 쪽 SIGTERM 이 시험 프로세스를 죽이지 않게 중간에 sh 를 하나 둔다
        cmd = ' '.join(['"%s"' % SHIM] + ['"%s"' % x for x in a])
        return subprocess.run(['sh', '-c', cmd + '; exit $?'], env=self.env, input=stdin, capture_output=True)

    def meter(self):
        return json.load(open(self.env['GCS_METER_FILE']))

    def calls(self):
        return [json.loads(x) for x in open(self.log)] if os.path.exists(self.log) else []

    def test_counts_download_and_passes_through(self):
        r = self.run_shim('storage', 'cp', '-q', 'gs://b/a', '/tmp/x')
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.meter()['bytes'], 400)
        self.assertIn(['storage', 'cp', '-q', 'gs://b/a', '/tmp/x'], self.calls())

    def test_cat_and_wildcard_counted(self):
        self.run_shim('storage', 'cat', 'gs://b/a')
        self.run_shim('storage', 'cp', '-q', 'gs://b/c/*.gz', '/tmp/')
        self.assertEqual(self.meter()['bytes'], 700)

    def test_stdin_list_counted_and_forwarded(self):
        r = self.run_shim('storage', 'cp', '-q', '-I', '/tmp/d/', stdin=b'gs://b/a\ngs://b/c/*.gz\n')
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.meter()['bytes'], 700)

    def test_upload_and_server_copy_not_counted(self):
        self.run_shim('storage', 'cp', '-q', '/tmp/f', 'gs://b/x')
        self.run_shim('storage', 'cp', '-q', '-I', 'gs://b/dest/', stdin=b'gs://b/big\n')
        self.assertFalse(os.path.exists(self.env['GCS_METER_FILE']))

    def test_over_cap_blocks_before_download(self):
        r = self.run_shim('storage', 'cp', '-q', 'gs://b/big', '/tmp/x')
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(self.meter()['tripped'])
        self.assertNotIn(['storage', 'cp', '-q', 'gs://b/big', '/tmp/x'], self.calls())  # 실제 받기는 일어나지 않음
        # 그 뒤로는 작은 것도 받지 않는다
        r = self.run_shim('storage', 'cat', 'gs://b/a')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.run_shim('gcs-meter', 'report').returncode, 3)

    def test_cumulative_cap(self):
        self.assertEqual(self.run_shim('storage', 'cat', 'gs://b/a').returncode, 0)
        self.assertEqual(self.run_shim('storage', 'cat', 'gs://b/a').returncode, 0)
        self.assertNotEqual(self.run_shim('storage', 'cat', 'gs://b/a').returncode, 0)  # 1200 > 1000
        self.assertEqual(self.meter()['bytes'], 800)

    def test_missing_object_is_zero_not_trip(self):
        r = self.run_shim('storage', 'cp', '-q', 'gs://b/none', '/tmp/x')
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.meter().get('tripped'))

    def test_non_storage_untouched(self):
        self.assertEqual(self.run_shim('kms', 'asymmetric-sign', '--x=1').returncode, 0)
        self.assertEqual(self.run_shim('gcs-meter', 'report').returncode, 0)

    def test_day_cap_across_jobs(self):
        self.env.update(GCS_BUCKET='b', GCS_DL_DAY_CAP_BYTES='1000', GCS_DL_CAP_BYTES='100000', FAKE_DAY=json.dumps(['1_1_publish_9-500', '2_1_desc_7-300']))
        # 오늘 다른 job 800 + 이번 300 > 1000 → 첫 받기에서 멈춤(이 job 의 상한 100000 안이어도)
        r = self.run_shim('storage', 'cat', 'gs://b/c/*.gz')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('하루 상한', self.meter()['tripped'])

    def test_report_marks_day(self):
        self.env.update(GCS_BUCKET='b')
        self.run_shim('storage', 'cat', 'gs://b/a')
        self.assertEqual(self.run_shim('gcs-meter', 'report').returncode, 0)
        ups = [c for c in self.calls() if c[:3] == ['storage', 'cp', '-q'] and '/guards/egress/' in c[-1]]
        self.assertEqual(len(ups), 1)
        self.assertTrue(ups[0][-1].endswith('-400'))

    def test_every_gcp_setup_workflow_reports(self):
        """(295기) 고친 워크플로(process·collect)는 job 마다 끝에 상한 보고 단계가 있어야 한다."""
        import yaml
        for wf in ('process.yml', 'collect.yml'):
            y = yaml.safe_load(open(os.path.join(ROOT, '.github', 'workflows', wf), encoding='utf-8'))
            for name, job in y['jobs'].items():
                steps = job.get('steps') or []
                if any('gcp-setup' in (s.get('uses') or '') for s in steps):
                    self.assertTrue(any('gcs-meter report' in (s.get('run') or '') for s in steps), f'{wf}:{name}')
                    self.assertTrue(job.get('env', {}).get('GCS_DL_CAP_BYTES') or y.get('env', {}).get('GCS_DL_CAP_BYTES'), f'{wf}:{name} 상한')


if __name__ == '__main__':
    unittest.main()
