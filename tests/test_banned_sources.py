"""쓰지 않기로 한 출처(sources/registry.yaml 의 banned)가 다시 들어오지 않는지 지킨다. (2026-11-17 출처·라이선스 점검)"""
import os, re, sys, unittest
import yaml
ROOT = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, os.path.join(ROOT, 'scripts', 'collect'))
import common as K  # noqa: E402

REG = yaml.safe_load(open(os.path.join(ROOT, 'sources', 'registry.yaml'), encoding='utf-8'))
HOSTS = [h for b in REG['banned'] for h in b['hosts']]
# 금지 목록 자체·잠금 코드·이 시험은 그 이름을 적을 수밖에 없다.
ALLOW = {'sources/registry.yaml', 'scripts/collect/common.py', 'functions/flight_free.js', 'functions/test_noterms.js', 'tests/test_banned_sources.py'}


class Banned(unittest.TestCase):
    def test_registry_has_banned_list(self):
        self.assertEqual({b['id'] for b in REG['banned']}, {'hko_normals', 'blog_aggregates', 'adsbdb', 'hexdb'})
        for b in REG['banned']:
            self.assertTrue(b['why'] and b['hosts'], b['id'])

    def test_no_source_uses_banned_host(self):
        for s in REG['sources']:
            h = K.host_of(s.get('url') or '') or ''
            self.assertFalse(any(h == d or h.endswith('.' + d) for d in HOSTS), s['id'])

    def test_collector_refuses_banned_hosts(self):
        for u in ('https://www.hko.gov.hk/en/cis/normal/1991_2020/normals.htm', 'https://www.weather.gov.hk/x', 'https://abc.tistory.com/12',
                  'https://blog.naver.com/a/1', 'https://ameblo.jp/a/entry-1.html'):
            self.assertTrue(K.forbidden(u), u)
        self.assertFalse(K.forbidden('https://www.jma.go.jp/jma/index.html'))

    def test_code_does_not_name_banned_hosts(self):
        pat = re.compile('|'.join(re.escape(h) for h in HOSTS) + r'|blog_facts|blog_agg')
        bad = []
        for d, dirs, files in os.walk(ROOT):
            dirs[:] = [x for x in dirs if x not in ('.git', 'node_modules', '__pycache__', 'raw', 'private_enc')]
            for f in files:
                if not f.endswith(('.py', '.js', '.sh', '.yml', '.yaml')):
                    continue
                rel = os.path.relpath(os.path.join(d, f), ROOT).replace(os.sep, '/')
                if rel in ALLOW:
                    continue
                if pat.search(open(os.path.join(d, f), encoding='utf-8', errors='ignore').read()):
                    bad.append(rel)
        self.assertEqual(bad, [])

    def test_no_terms_lookups_locked(self):
        s = open(os.path.join(ROOT, 'functions', 'flight_free.js'), encoding='utf-8').read()
        self.assertIn('const NOTERMS_OK = false;', s)
        self.assertEqual(len(re.findall(r"if \(!NOTERMS_OK \|\| env\('FLIGHT_(?:ADSBDB|HEXDB)_OK'\) !== '1'\) return null;", s)), 2)


if __name__ == '__main__':
    unittest.main()
