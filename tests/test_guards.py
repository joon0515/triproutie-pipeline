"""지킴이 시험: 라이선스 검사 규칙 · 약관/링크 감시(가짜 웹) · 실패 모음 워크플로가 실제 워크플로 이름을 가리키는지."""
import glob, os, sys, unittest, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, os.path.join(ROOT, 'scripts', 'guards')); sys.path.insert(0, os.path.join(ROOT, 'scripts', 'collect'))
import license_check as LC  # noqa: E402
import watch_sources as W  # noqa: E402
import yaml  # noqa: E402

REG = yaml.safe_load(open(os.path.join(ROOT, 'sources', 'registry.yaml'), encoding='utf-8'))
EXC = yaml.safe_load(open(os.path.join(ROOT, 'sources', 'license_exceptions.yaml'), encoding='utf-8'))['exceptions']


def src(**kw):
    return dict({'id': 'x', 'status': 'active', 'license': 'CC0-1.0', 'attribution': 'X', 'license_url': 'https://example.org/l', 'script': 'taiwan'}, **kw)


class License(unittest.TestCase):
    def test_repo_registry_passes(self):
        errs, warns = LC.check(REG, EXC, today='2026-11-17')
        self.assertEqual(errs, [])
        self.assertTrue(any('wikivoyage' in w for w in warns))  # 알고 있는 예외는 경고로 남는다

    def test_rules(self):
        cases = [(src(license='CC BY-NC 4.0'), '허용 라이선스'), (src(license='미확인'), '허용 라이선스'), (src(attribution=''), '비어 있음'),
                 (src(license_url='', url='ftp://x'), '근거 주소'), (src(url='https://www.tripadvisor.com/x'), '금지 도메인'),
                 (src(license='ODbL-1.0', script='crawl'), 'share-alike'), (src(license='CC-BY-SA-4.0', script='wikivoyage'), 'share-alike')]
        for s, want in cases:
            errs, _ = LC.check({'sources': [s]}, [], attribution='')
            self.assertTrue(any(want in e for e in errs), (s, errs))
        ok, _ = LC.check({'sources': [src(license='ODbL-1.0', script='osm_layers'), src(license='데이터셋마다', license_url='', url='(사이트마다)')]}, [], attribution='')
        self.assertEqual(ok, [])

    def test_exception_expires(self):
        s = src(id='wv', license='CC-BY-SA-4.0', script='wikivoyage')
        e = [{'id': 'wv', 'rule': 'share_alike_in_main', 'reason': 'r', 'until': '2026-12-17'}]
        self.assertEqual(LC.check({'sources': [s]}, e, today='2026-12-17', attribution='')[0], [])
        self.assertTrue(LC.check({'sources': [s]}, e, today='2026-12-18', attribution='')[0])
        self.assertTrue(LC.check({'sources': []}, [{'id': 'a', 'rule': 'x'}], attribution='')[0])  # 이유·만료일 없는 예외는 실패

    def test_attribution_must_name_sources(self):
        reg = {'sources': [src(id='overture_places', script='overture', license='CDLA-Permissive-2.0')]}
        self.assertTrue(LC.check(reg, [], attribution='Foursquare only')[0])
        self.assertEqual(LC.check(reg, [], attribution='Overture Maps Foundation')[0], [])


class Web:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def __call__(self, url, headers):
        self.calls.append(url)
        v = self.pages.get(url)
        if v is None or isinstance(v, int):
            raise urllib.error.HTTPError(url, v or 404, 'x', {}, None)
        return 200, {}, v


class Watch(unittest.TestCase):
    REG = {'sources': [
        {'id': 'a', 'status': 'active', 'license_url': 'https://a.org/terms', 'url': 'https://a.org/data.zip'},
        {'id': 'b', 'status': 'candidate', 'url': 'https://b.org/api'},
        {'id': 'c', 'status': 'active', 'url': 'https://www.google.com/maps'},
        {'id': 'd', 'status': 'active', 'url': 's3://bucket/<release>/x'},
        {'id': 'e', 'status': 'banned', 'url': 'https://e.org/'},
        {'id': 'f', 'status': 'active', 'license_url': 'https://f.org/robots-blocked'}]}

    def test_targets(self):
        self.assertEqual(sorted(W.targets(self.REG)), ['https://a.org/terms', 'https://b.org/api', 'https://f.org/robots-blocked', 'https://www.google.com/maps'])

    def test_change_broken_robots_and_no_body_stored(self):
        pages = {'https://a.org/terms': b'<html><script>var t=1</script><p>Terms v1</p> 12:30</html>', 'https://b.org/api': 500,
                 'https://f.org/robots.txt': b'User-agent: *\nDisallow: /'}
        web = Web(pages)
        st, rep = W.watch(self.REG, {}, opener=web, sleep=lambda s: None, today='2026-11-17')
        self.assertEqual([x[0] for x in rep['new']], ['https://a.org/terms'])
        self.assertEqual([x[0] for x in rep['broken']], ['https://b.org/api'])
        self.assertEqual(sorted(x[0] for x in rep['robots']), ['https://f.org/robots-blocked', 'https://www.google.com/maps'])
        self.assertFalse(any('google' in u for u in web.calls))  # 금지 도메인은 요청조차 하지 않음
        self.assertNotIn('Terms', repr(st))  # 본문은 저장하지 않는다
        pages['https://a.org/terms'] = b'<html><script>var t=2</script><p>Terms v1</p> 13:45</html>'  # 시각·스크립트만 바뀜 = 그대로
        st2, rep2 = W.watch(self.REG, st, opener=Web(pages), sleep=lambda s: None, today='2026-11-24')
        self.assertEqual((rep2['ok'], rep2['changed']), (1, []))
        pages['https://a.org/terms'] = b'<p>Terms v2: non-commercial only</p>'
        st3, rep3 = W.watch(self.REG, st2, opener=Web(pages), sleep=lambda s: None, today='2026-12-01')
        self.assertEqual([x[0] for x in rep3['changed']], ['https://a.org/terms'])
        md = W.to_md(rep3, '2026-12-01')
        self.assertIn('바뀜 1', md); self.assertIn('### 약관', md)


class Workflows(unittest.TestCase):
    def test_digest_names_exist(self):
        names = set()
        for f in glob.glob(os.path.join(ROOT, '.github', 'workflows', '*.yml')):
            names.add(yaml.safe_load(open(f, encoding='utf-8'))['name'])
        y = yaml.safe_load(open(os.path.join(ROOT, '.github', 'workflows', 'failure-digest.yml'), encoding='utf-8'))
        watched = set(y[True]['workflow_run']['workflows'])  # yaml 1.1: on → True
        pending = {'coverage-report', 'fx-daily', 'climate-normals'}  # 열린 PR(#18·#22·coverage)이 들여오는 이름
        self.assertEqual(watched - names - pending, set())
        self.assertEqual(names - watched - {'failure-digest'}, set())  # 이 저장소의 워크플로는 모두 지켜본다

    def test_no_secrets_in_guards(self):
        txt = open(os.path.join(ROOT, '.github', 'workflows', 'guards.yml'), encoding='utf-8').read()
        self.assertNotIn('secrets.', txt); self.assertNotIn('id-token', txt); self.assertNotIn('pull_request_target', txt)


if __name__ == '__main__':
    unittest.main()
