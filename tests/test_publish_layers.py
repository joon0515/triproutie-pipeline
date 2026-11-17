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
        # 뿌리 목록: 예전 모양 그대로(schema·path·layers), 수집 층은 없음
        self.assertEqual(man['schema'], 'places-layers-1'); self.assertEqual(man['path'], 'places/v1/c20261117/layers/')
        self.assertEqual(sorted(e['kind'] for e in man['layers']), ['amenities'] * 30)
        for e in man['layers']:
            b = open(os.path.join(g, 'bkt', man['path'], e['file']), 'rb').read()
            self.assertEqual((len(b), hashlib.sha256(b).hexdigest()), (e['size'], e['sha256']))
        # 2단 목록: 뿌리의 sha256·크기와 같고, 수집 층 462개가 빠짐·겹침 없이 들어 있고, 층 파일 sha256 과도 맞는다
        self.assertEqual(man['parts_path'], 'places/v1/c20261117/layer_parts/')
        seen = []
        for p in man['parts']:
            b = open(os.path.join(g, 'bkt', man['parts_path'], p['file']), 'rb').read()
            self.assertEqual((len(b), hashlib.sha256(b).hexdigest()), (p['size'], p['sha256']))
            pj = json.loads(b)
            self.assertEqual((pj['schema'], pj['version'], pj['kind'], pj['path']), ('places-layers-part-1', 'c20261117', 'collect', man['path']))
            self.assertEqual(len(pj['layers']), p['n']); self.assertLessEqual(p['n'], max(P.PART_ENTRIES, 22))
            self.assertEqual(sorted({e['cc'] for e in pj['layers']}), p['cc'])
            for e in pj['layers']:
                self.assertEqual(e['kind'], 'collect')
                self.assertTrue(p['bbox'][0] <= e['bbox'][0] and e['bbox'][3] <= p['bbox'][3])
                fb = open(os.path.join(g, 'bkt', pj['path'], e['file']), 'rb').read()
                self.assertEqual((len(fb), hashlib.sha256(fb).hexdigest()), (e['size'], e['sha256']))
                seen.append(e['file'])
        self.assertEqual(sorted(seen), sorted(f for f in os.listdir(d) if f.startswith('col_')))
        ccs = [c for p in man['parts'] for c in p['cc']]
        self.assertEqual(len(ccs), len(set(ccs)))  # 한 나라는 한 장에만
        # 올린 순서: 층 파일 → 2단 목록 → 뿌리 목록
        order = open(os.path.join(g, 'order.log')).read().split()
        kinds = ['root' if o.endswith('layers.json') else 'part' if '/layer_parts/' in o else 'file' for o in order]
        self.assertEqual(kinds, sorted(kinds, key=['file', 'part', 'root'].index))

    def test_small_list_keeps_old_shape(self):
        """수집 층이 없으면 목록은 예전과 같다(parts 키 없음)."""
        d = tempfile.mkdtemp(); amen(d, 'kr'); amen(d, 'jp')
        r, g = run(d, tag='L-2026-11-17')
        self.assertEqual(r.returncode, 0, r.stderr)
        man = json.loads(open(os.path.join(g, 'bkt/places/v1/layers.json'), 'rb').read())
        self.assertEqual(sorted(man), sorted(['schema', 'version', 'created_at', 'path', 'layers', 'disabled', 'revoked', 'expires', 'attribution', 'sig']))
        self.assertFalse(os.path.exists(os.path.join(g, 'bkt/places/v1/L-2026-11-17/layer_parts')))

    def test_one_country_over_200_files_is_split(self):
        ents = [{'file': f'col_US_{i}.json.gz', 'kind': 'collect', 'n': 1, 'size': 1, 'sha256': 'x', 'cc': 'US'} for i in range(450)]
        man = {'schema': 'places-layers-1', 'version': 't', 'path': 'places/v1/t/layers/', 'layers': ents}
        root, blobs = P.split_manifest(man, canonical)
        self.assertEqual([p['n'] for p in root['parts']], [200, 200, 50]); self.assertEqual(root['layers'], [])
        self.assertEqual(sum(len(json.loads(b)['layers']) for b in blobs.values()), 450)

    def test_root_over_limit_stops_before_sign_and_upload(self):
        """앱이 읽는 종류만으로 한도를 넘으면 서명·업로드 전에 멈추고 종류별 크기를 알려 준다."""
        d = tempfile.mkdtemp()
        for r in range(330):
            amen(d, f'region-with-a-long-name-{r:04d}')
        r, g = run(d)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('amenities', r.stderr); self.assertFalse(os.path.exists(os.path.join(g, 'calls.log')))

    def test_dry_run_touches_nothing(self):
        d = tempfile.mkdtemp(); big_dir(d)
        r, g = run(d, dry=True)
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn('2단 목록', r.stdout)
        self.assertFalse(os.path.exists(os.path.join(g, 'calls.log')))


if __name__ == '__main__':
    unittest.main()
