"""층 목록 게시(publish_layers.py) 시험 — 수집 층 수백 개여도 뿌리 목록이 KMS 서명·앱 읽기 한도(64 KiB) 안에 들어가는지,
2단 목록(layer_parts/)의 sha256·크기가 뿌리 목록과 맞는지, 서명한 바이트가 앱이 다시 만드는 정규 JSON 과 같은지. gcloud 는 가짜(로컬 폴더)."""
import base64, gzip, hashlib, json, os, stat, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, '..', 'tools')
SCRIPT = os.path.join(HERE, '..', 'scripts', 'publish_layers.py')
sys.path.insert(0, os.path.join(HERE, '..', 'scripts')); sys.path.insert(0, TOOLS)
import publish_layers as P  # noqa: E402
from canonical_fix import canon_value, canonical  # noqa: E402

# 가짜 gcloud: storage cp → $FAKE_GCS 아래 복사, kms asymmetric-sign → 입력이 64 KiB 를 넘으면 실제 KMS 처럼 거부,
# 아니면 '서명' 자리에 입력의 sha512(64바이트)를 쓴다(시험이 '무엇을 서명했는지'를 확인하려고). 호출은 calls.log 에 적는다.
FAKE = r'''#!/usr/bin/env python3
import hashlib, os, shutil, sys
a = sys.argv[1:]; root = os.environ['FAKE_GCS']
open(os.path.join(root, 'calls.log'), 'a').write(' '.join(x for x in a[:3]) + '\n')
def loc(u): return os.path.join(root, u[5:])
if a[:2] == ['kms', 'asymmetric-sign']:
    kv = dict(x[2:].split('=', 1) for x in a[2:])
    b = open(kv['input-file'], 'rb').read()
    open(os.path.join(root, 'signed.bin'), 'wb').write(b)
    if len(b) > 65536:
        sys.stderr.write('ERROR: (gcloud.kms.asymmetric-sign) The file is larger than the maximum size of 65536 bytes.\n'); sys.exit(1)
    open(kv['signature-file'], 'wb').write(hashlib.sha512(b).digest()); sys.exit(0)
if a[:2] == ['storage', 'cp']:
    xs = [x for x in a[2:] if x != '-q']; dst = xs[-1]
    for s in xs[:-1]:
        d = loc(dst) + (os.path.basename(s) if dst.endswith('/') else '')
        os.makedirs(os.path.dirname(d), exist_ok=True); shutil.copyfile(s, d)
        open(os.path.join(root, 'order.log'), 'a').write(d[len(root) + 1:] + '\n')
    sys.exit(0)
sys.exit(2)
'''


def col(d, cc, i, n=3):
    """수집 층 파일(collect-1, 타일과 같은 열 모양) 하나."""
    j = {'schema': 'collect-1', 'dataset': 'places', 'cc': cc, 'id': [f'{cc}:{i}:{k}' for k in range(n)],
         'dla': [3500000 + i * 1000, 10, 10], 'dlo': [13500000 + i * 1000, 10, 10]}
    f = os.path.join(d, f'col_{cc}_{i}.json.gz')
    open(f, 'wb').write(gzip.compress(json.dumps(j).encode(), 1, mtime=0))
    return f


def amen(d, r):
    j = {'schema': 'amenities-1', 'rows': [{'lat': 35.0, 'lon': 135.0, 't': 'toilet'}, {'lat': 35.1, 'lon': 135.2, 't': 'atm'}]}
    f = os.path.join(d, f'amenities-{r}.json.gz')
    open(f, 'wb').write(gzip.compress(json.dumps(j).encode(), 1, mtime=0))
    return f


def big_dir(d):
    """11/17 실패 때와 같은 크기: 나라 46개에 수집 층 462개 + 다른 층 30개."""
    ccs = [chr(65 + a) + chr(65 + b) for a in range(2) for b in range(23)]
    per = [10] * 46; per[3] = 22; per[5] = 0  # 합계 462(나라 하나는 22개, 하나는 0개)
    for cc, k in zip(ccs, per):
        for i in range(k):
            col(d, cc, i)
    for r in range(30):
        amen(d, f'r{r:02d}')


def run(d, tag='c20261117', dry=False):
    g = tempfile.mkdtemp(); b = tempfile.mkdtemp()
    fake = os.path.join(b, 'gcloud'); open(fake, 'w').write(FAKE); os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
    env = {**os.environ, 'PATH': b + os.pathsep + os.environ['PATH'], 'FAKE_GCS': g, 'TMPDIR': tempfile.mkdtemp()}
    cmd = [sys.executable, SCRIPT, '--tag', tag, '--dir', d, '--bucket', 'bkt', '--tools', TOOLS] + (['--dry-run'] if dry else ['--kms-key', 'projects/p/locations/l/keyRings/r/cryptoKeys/k/cryptoKeyVersions/1'])
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return r, g


class PublishLayers(unittest.TestCase):
    def test_flat_list_of_462_collect_layers_is_over_the_kms_limit(self):
        """고치기 전 모양(한 장짜리 목록)은 64 KiB 를 넘는다 — 실패를 다시 만든다."""
        d = tempfile.mkdtemp(); big_dir(d)
        files = sorted(os.path.join(d, f) for f in os.listdir(d))
        flat = canon_value(P.build_manifest('c20261117', files))
        self.assertEqual(sum(1 for e in flat['layers'] if e['kind'] == 'collect'), 462)
        self.assertGreater(len(canonical(flat).encode()), P.SIGN_MAX)
        root, blobs = P.split_manifest(flat, canonical)
        self.assertLess(len(canonical(canon_value(root)).encode()), P.ROOT_MAX - 200)

    def test_publish_big_signs_small_root_and_parts_match(self):
        d = tempfile.mkdtemp(); big_dir(d)
        r, g = run(d)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout[-300:])
        raw = open(os.path.join(g, 'bkt/places/v1/layers.json'), 'rb').read()
        self.assertEqual(raw, open(os.path.join(g, 'bkt/places/v1/c20261117/layers.json'), 'rb').read())
        self.assertLessEqual(len(raw), 64 * 1024)  # 앱 kTlPlMaxRootBytes
        man = json.loads(raw)
        # 앱(tlVerifySignedJson)과 같은 방식: sig 를 뺀 나머지의 정규 JSON = 서명한 바이트
        body = canonical({k: v for k, v in man.items() if k != 'sig'}).encode()
        signed = open(os.path.join(g, 'signed.bin'), 'rb').read()
        self.assertEqual(body, signed); self.assertLessEqual(len(signed), 65536)
        self.assertEqual(base64.b64decode(man['sig']), hashlib.sha512(body).digest())
        # 뿌리 목록: 예전 모양 그대로(schema·path·layers), layers 에는 지금 앱이 읽는 종류만(앱이 쓰는 칸만), 수집 층은 없음
        self.assertEqual(man['schema'], 'places-layers-1'); self.assertEqual(man['path'], 'places/v1/c20261117/layers/')
        self.assertEqual(sorted(e['kind'] for e in man['layers']), ['amenities'] * 30)
        self.assertNotIn('legacy_omitted', man)
        for e in man['layers']:
            self.assertLessEqual(set(e), set(P.LEGACY_FIELDS))
            b = open(os.path.join(g, 'bkt', man['path'], e['file']), 'rb').read()
            self.assertEqual((len(b), hashlib.sha256(b).hexdigest()), (e['size'], e['sha256']))
        # 2단 목록: 뿌리의 sha256·크기와 같고, 장마다 따로 서명(본문 = sig 를 뺀 정규 JSON), 모든 층이 빠짐·겹침 없이 들어 있고, 층 파일 sha256 과도 맞는다
        self.assertEqual(man['parts_path'], 'places/v1/c20261117/layer_parts/')
        seen = []
        for p in man['parts']:
            b = open(os.path.join(g, 'bkt', man['parts_path'], p['file']), 'rb').read()
            self.assertEqual((len(b), hashlib.sha256(b).hexdigest()), (p['size'], p['sha256']))
            self.assertLessEqual(len(b), P.PART_MAX)
            pj = json.loads(b)
            pb = canonical({k: v for k, v in pj.items() if k != 'sig'}).encode()
            self.assertEqual(base64.b64decode(pj['sig']), hashlib.sha512(pb).digest())
            self.assertEqual((pj['schema'], pj['version'], pj['kind'], pj['path']), ('places-layers-part-1', 'c20261117', p['kind'], man['path']))
            self.assertEqual(len(pj['layers']), p['n'])
            if p['kind'] == 'collect':
                self.assertEqual(sorted({e['cc'] for e in pj['layers']}), p['cc'])
            for e in pj['layers']:
                self.assertEqual(e['kind'], p['kind'])
                self.assertTrue(p['bbox'][0] <= e['bbox'][0] and e['bbox'][3] <= p['bbox'][3])
                fb = open(os.path.join(g, 'bkt', pj['path'], e['file']), 'rb').read()
                self.assertEqual((len(fb), hashlib.sha256(fb).hexdigest()), (e['size'], e['sha256']))
                seen.append(e['file'])
        self.assertEqual(sorted(seen), sorted(os.listdir(d)))
        ccs = [c for p in man['parts'] if p['kind'] == 'collect' for c in p['cc']]
        self.assertEqual(len(ccs), len(set(ccs)))  # 한 나라는 한 장에만
        self.assertEqual(open(os.path.join(g, 'calls.log')).read().count('kms asymmetric-sign'), len(man['parts']) + 1)  # 장마다 + 뿌리
        # 게시된 것을 Storage 에서 다시 읽어 모으면(process.py 이어 싣기) 층 전부. 장이 깨지면 멈춘다
        rd = lambda path: open(os.path.join(g, 'bkt', path), 'rb').read() if os.path.exists(os.path.join(g, 'bkt', path)) else None
        self.assertEqual(sorted(e['file'] for e in P.entries_of(man, rd)), sorted(os.listdir(d)))
        open(os.path.join(g, 'bkt', man['parts_path'], man['parts'][0]['file']), 'ab').write(b' ')
        self.assertRaises(ValueError, P.entries_of, man, rd)
        # 올린 순서: 층 파일 → 2단 목록 → 뿌리 목록
        order = open(os.path.join(g, 'order.log')).read().split()
        kinds = ['root' if o.endswith('layers.json') else 'part' if '/layer_parts/' in o else 'file' for o in order]
        self.assertEqual(kinds, sorted(kinds, key=['file', 'part', 'root'].index))

    def test_small_list_keeps_old_keys(self):
        """층이 적어도 뿌리의 옛 키(schema·layers…)는 그대로, parts 만 더해진다(앱은 모르는 키를 무시)."""
        d = tempfile.mkdtemp(); amen(d, 'kr'); amen(d, 'jp')
        r, g = run(d, tag='L-2026-11-17')
        self.assertEqual(r.returncode, 0, r.stderr)
        man = json.loads(open(os.path.join(g, 'bkt/places/v1/layers.json'), 'rb').read())
        self.assertEqual(sorted(man), sorted(['schema', 'version', 'created_at', 'path', 'layers', 'disabled', 'revoked', 'expires', 'attribution', 'sig', 'parts_path', 'parts']))
        self.assertEqual([e['file'] for e in man['layers']], ['amenities-jp.json.gz', 'amenities-kr.json.gz'])
        self.assertEqual([p['file'] for p in man['parts']], ['amenities-000.json'])
        self.assertTrue(os.path.exists(os.path.join(g, 'bkt/places/v1/L-2026-11-17/layer_parts/amenities-000.json')))

    def test_one_country_over_one_part_is_split(self):
        ents = [{'file': f'col_US_{i}.json.gz', 'kind': 'collect', 'n': 1, 'size': 1, 'sha256': 'x' * 64, 'cc': 'US'} for i in range(900)]
        man = {'schema': 'places-layers-1', 'version': 't', 'path': 'places/v1/t/layers/', 'layers': ents}
        root, blobs = P.split_manifest(man, canonical)
        self.assertGreater(len(root['parts']), 1); self.assertEqual(root['layers'], [])
        self.assertTrue(all(len(b) <= P.PART_MAX for b in blobs.values()))
        self.assertEqual(sum(len(json.loads(b)['layers']) for b in blobs.values()), 900)

    def test_app_kinds_over_root_limit_are_omitted_not_failed(self):
        """(11/19) 앱이 뿌리에서 읽는 종류만으로 64 KiB 를 넘던 실패 → 이제 게시는 되고, 못 실은 종류는 legacy_omitted 에 적힌다(parts 에는 다 있음)."""
        d = tempfile.mkdtemp()
        for r in range(330):
            amen(d, f'region-with-a-long-name-{r:04d}')
        r, g = run(d)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('legacy_omitted', r.stderr)
        raw = open(os.path.join(g, 'bkt/places/v1/layers.json'), 'rb').read()
        self.assertLessEqual(len(raw), P.ROOT_MAX)
        man = json.loads(raw)
        self.assertEqual((man['layers'], man['legacy_omitted']), ([], ['amenities']))
        self.assertEqual(sum(p['n'] for p in man['parts']), 330)

    def test_dry_run_touches_nothing(self):  # 그리고 utcnow 사용 중단 경고가 없다
        d = tempfile.mkdtemp(); big_dir(d)
        r, g = run(d, dry=True)
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn('2단 목록', r.stdout); self.assertNotIn('Deprecat', r.stderr)
        self.assertFalse(os.path.exists(os.path.join(g, 'calls.log')))


def _ent(kind, i, cc=None):
    """11/19 실제 목록과 비슷한 크기의 항목(describe 결과 모양)."""
    e = {'file': f'{kind}-region-{cc or "xx"}-{i:05d}.json.gz', 'kind': kind, 'n': 1000 + i, 'size': 100000 + i,
         'sha256': hashlib.sha256(f'{kind}{i}'.encode()).hexdigest(), 'bbox': [126.1234 + i % 7, 33.1234, 129.5678, 38.6543], 'license': 'ODbL-1.0'}
    if kind == 'hours':
        e['with_oh'] = 500
    if kind == 'tourapi':
        e.update(area=1, photos=10, license='KOGL')
    if kind == 'collect':
        e.update(cc=cc, dataset='places', license='mixed (sources/registry.yaml)')
    return e


# 11/19 실패 때 종류별 항목 수(뿌리 bytes: amenities 42,441 · hours 7,259 · tourapi 3,627 · names 1,721 · photos 301) + 수집 층 462
BASE_1119 = {'amenities': 200, 'hours': 34, 'tourapi': 16, 'names': 8, 'photos': 2}


def _man(scale):
    ents = [_ent(k, i) for k, n in BASE_1119.items() for i in range(int(n * scale))]
    ccs = [chr(65 + a) + chr(65 + b) for a in range(26) for b in range(26)]
    ents += [_ent('collect', i, ccs[i % 200]) for i in range(int(462 * scale))]
    return canon_value(P.build_manifest('c20261119', [], extra=ents))


class RootSizeCeiling(unittest.TestCase):
    """종류별 크기가 늘어나도 뿌리 목록은 상한 아래, 2단(·3단 색인) 목록 장마다 서명 한도 아래, 항목은 빠짐·겹침 없이."""

    def check(self, scale):
        flat = _man(scale)
        root, blobs = P.split_manifest(flat, canonical)
        body = canonical(canon_value(root)).encode()
        self.assertLessEqual(len(body) + P.SIG_ROOM, P.ROOT_TARGET, f'x{scale}')  # 263기: 목표 44 KiB, 가드 48 KiB
        self.assertTrue(all(len(b) + P.SIG_ROOM <= P.PART_MAX for b in blobs.values()), f'x{scale}')
        # 뿌리 → (색인) → 장 → 항목을 따라가 처음 목록과 같은지
        got = []

        def walk(p):
            j = json.loads(blobs[p['file']])
            self.assertEqual((len(blobs[p['file']]), hashlib.sha256(blobs[p['file']]).hexdigest()), (p['size'], p['sha256']))
            if p.get('index'):
                self.assertEqual(j['schema'], 'places-layers-index-1')
                for q in j['parts']:
                    walk(q)
            else:
                self.assertEqual(j['schema'], 'places-layers-part-1')
                got.extend(e['file'] for e in j['layers'])
        for p in root['parts']:
            walk(p)
        self.assertEqual(sorted(got), sorted(e['file'] for e in flat['layers']))
        # process.py 가 지금 판의 층을 이어 실을 때 쓰는 entries_of 도 같은 목록을 돌려준다
        allf = P.entries_of(root, lambda path: blobs.get(path.rsplit('/', 1)[1]))
        self.assertEqual(sorted(e['file'] for e in allf), sorted(e['file'] for e in flat['layers']))
        # 옛 항목: 지금 앱이 읽는 종류는 통째로 뿌리에 있거나 legacy_omitted 에 있다(일부만 싣지 않음)
        for k in {e['kind'] for e in flat['layers']} & set(P.LEGACY_KINDS):
            n_root = sum(1 for e in root['layers'] if e['kind'] == k)
            if k in root.get('legacy_omitted', []):
                self.assertEqual(n_root, 0)
            else:
                self.assertEqual(n_root, sum(1 for e in flat['layers'] if e['kind'] == k))
        self.assertFalse([e for e in root['layers'] if e['kind'] not in P.LEGACY_KINDS])
        return root, len(body)

    def test_1119_sizes_root_under_guard(self):
        """(263기) 11/19 실제 크기(뿌리 55.6 KB)가 가드 48 KiB 아래로: 옛 항목은 고정 예산 안에서 작은 종류부터, 못 실은 amenities 는 legacy_omitted(2단 목록에는 있음)."""
        flat = _man(1)
        by = {}
        for e in flat['layers']:
            by[e['kind']] = by.get(e['kind'], 0) + len(canonical(e).encode()) + 1
        real = {'amenities': 42441, 'hours': 7259, 'tourapi': 3627, 'names': 1721, 'photos': 301}  # 11/19 실패 메시지(뿌리에 있던 종류별 bytes)
        self.assertTrue(all(by[k] >= v for k, v in real.items()), by)  # 시험 항목이 실제보다 크거나 같다(보수적)
        root, n = self.check(1)
        self.assertLess(n + P.SIG_ROOM, P.ROOT_GUARD)
        self.assertEqual(root['legacy_omitted'], ['amenities'])
        self.assertEqual({e['kind'] for e in root['layers']}, {'hours', 'names', 'photos'})  # 작은 종류는 그대로(앱 변경 없이 동작)

    def test_root_size_is_flat_in_layer_count(self):
        """층이 1배에서 200배로 늘어도 뿌리 크기는 가드(48 KiB) 아래에서 거의 일정(목표 44 KiB)."""
        sizes = [self.check(s)[1] for s in (1, 3, 10, 40, 100, 200)]
        self.assertLess(max(sizes) + P.SIG_ROOM, P.ROOT_GUARD)
        self.assertLess(max(sizes) - min(sizes), 24 * 1024, sizes)

    def test_growth_stays_under_ceiling(self):
        for s in (1.3, 3, 10, 40, 200):
            root, n = self.check(s)
        self.assertIn('amenities', root['legacy_omitted'])

    def test_huge_growth_uses_index_level(self):
        root, n = self.check(100)  # 층 7만여 개 — 장 500장 남짓이면 뿌리 parts 만으로 100 KB → 색인(3단)
        self.assertTrue(any(p.get('index') for p in root['parts']))


if __name__ == '__main__':
    unittest.main()
