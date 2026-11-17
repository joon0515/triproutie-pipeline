"""수천만~억 단위에서도 서명 한도·앱 내려받기 크기·러너 디스크 안에 드는지(docs/SCALE.md).
- 뿌리 목록(서명 대상) < 62 KiB: 층 파일 4,000개(2억 행) · 20,000개(10억 행)
- 층 파일 하나의 범위 = 위도 0.5° 줄 하나 안(경도는 이웃 칸 몇 개) · 파일 안은 위도·경도 순 · ≤ 5만 행
- 게시는 층 파일을 PUB_PULL 개씩만 받고 곧 지운다 · 수집 층은 Storage 안 복사 목록으로 넘긴다
"""
import gzip, json, os, random, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, os.path.join(ROOT, 'scripts')); sys.path.insert(0, os.path.join(ROOT, 'scripts', 'collect')); sys.path.insert(0, os.path.join(ROOT, 'tools'))
import publish_layers as L  # noqa: E402
import common as K  # noqa: E402
import process as P  # noqa: E402
from canonical_fix import canonical  # noqa: E402


def fake_entries(n, ncc=229, seed=1):
    r, out = random.Random(seed), []
    for i in range(n):
        cc = f'{chr(65 + i % ncc % 26)}{chr(65 + (i % ncc) // 26)}'
        x, y = r.uniform(-180, 179), r.uniform(-60, 70)
        out.append({'file': f'col_{cc}_{i}.json.gz', 'kind': 'collect', 'n': 50000, 'size': 3500000, 'sha256': 'a' * 64, 'dataset': 'places', 'cc': cc,
                    'bbox': [round(x, 4), round(y, 4), round(x + 0.5, 4), round(y + 0.5, 4)], 'license': 'mixed (sources/registry.yaml)'})
    return out


class Manifest(unittest.TestCase):
    def test_root_under_sign_limit_at_200m_and_1b_rows(self):
        for n in (4000, 20000):
            man = L.build_manifest('c202611170000', [], extra=fake_entries(n))
            root, blobs = L.split_manifest(man, canonical)
            size = len(canonical(root).encode()) + 200
            self.assertLess(size, L.ROOT_MAX, n)
            self.assertEqual(sum(p['n'] for p in root['parts']), n); self.assertEqual(root['layers'], [])
            self.assertTrue(all(len(b) < 256 * 1024 for b in blobs.values()))  # 2단 목록 한 장 < 256 KiB(앱이 나라 단위로 하나씩 받음)


class Tiles(unittest.TestCase):
    def test_layer_files_are_compact_tiles(self):
        d = tempfile.mkdtemp(); s = K.Store(os.path.join(d, 's')); r = random.Random(3); rows = []
        for i in range(3000):  # 미국 크기 나라의 줄 4개: 위도 30~32, 경도 -120~-75
            rows.append({'id': f'aa:{i}', 'nm': f'p{i}', 'lat': round(r.uniform(30, 32), 5), 'lon': round(r.uniform(-120, -75), 5), 'c': 'food', 'cc': 'US', 'src': 'aa', 'lic': 'CC0'})
        s.put_json(f'raw/aa/{K.today()}/r~US/000000-0000.json.gz', {'schema': 'collect-raw-1', 'source': 'aa', 'dataset': 'places', 'rows': rows})
        keep = (P.PART_ROWS, P.CELL_FLUSH)
        P.PART_ROWS, P.CELL_FLUSH = 400, 100
        try:
            P.process(s, None)
        finally:
            P.PART_ROWS, P.CELL_FLUSH = keep
        lat, seen = s.get_json('processed/latest.json'), set()
        for f in lat['files']:
            j = s.get_json(lat['paths'][f])
            if j.get('dataset') != 'places':
                continue
            la = lo = 0; pts = []
            for a, b in zip(j['dla'], j['dlo']):
                la += a; lo += b; pts.append((la / 1e5, lo / 1e5))
            self.assertLessEqual(len(pts), 400)
            self.assertEqual(len({int((y + 90) // 0.5) for y, _ in pts}), 1, f)  # 위도 0.5° 줄 하나
            self.assertLess(max(x for _, x in pts) - min(x for _, x in pts), 45 / 2, f)  # 나라 동서 폭(45°)의 절반보다 좁다
            seen.update(j['id'])
        self.assertEqual(len(seen), 3000)
        self.assertGreater(len(lat['files']), 3000 / 400)


class Publish(unittest.TestCase):
    def test_streams_files_and_hands_over_storage_copy_list(self):
        d = tempfile.mkdtemp(); s = K.Store(os.path.join(d, 's')); r = random.Random(5)
        rows = [{'id': f'aa:{i}', 'nm': f'p{i}', 'lat': round(r.uniform(33, 38), 5), 'lon': round(r.uniform(127, 141), 5), 'c': 'food', 'cc': 'JP', 'src': 'aa', 'lic': 'CC0'} for i in range(2000)]
        s.put_json(f'raw/aa/{K.today()}/r~JP/000000-0000.json.gz', {'schema': 'collect-raw-1', 'source': 'aa', 'dataset': 'places', 'rows': rows})
        keep = (P.PART_ROWS, P.CELL_FLUSH, P.PUB_PULL)
        P.PART_ROWS, P.CELL_FLUSH, P.PUB_PULL = 200, 50, 3
        pulls, orig, calls = [], K.Store.pull, []
        real_run = subprocess.run

        def spy_pull(self, keys, dest, strict=True):
            keys = list(keys); pulls.append(len(keys))
            return orig(self, keys, dest, strict)

        def spy_run(cmd, *a, **kw):
            calls.append(cmd)
            return real_run(cmd, *a, **kw)
        K.Store.pull, P.subprocess.run = spy_pull, spy_run
        try:
            P.process(s, None)
            pulls.clear()
            cur = os.path.join(d, 'cur'); os.makedirs(cur)
            tag, msg = P.publish(s, 'bucket', '', dry_run=True, tools=os.path.join(ROOT, 'tools'), current_dir=cur)
        finally:
            K.Store.pull, P.subprocess.run = orig, real_run
            P.PART_ROWS, P.CELL_FLUSH, P.PUB_PULL = keep
        self.assertTrue(tag, msg)
        self.assertTrue(pulls) ; self.assertLessEqual(max(pulls), 3)  # 한 번에 3개(PUB_PULL)씩만
        cmd = next(c for c in calls if 'publish_layers.py' in ' '.join(map(str, c)))
        ex = json.load(open(cmd[cmd.index('--extra') + 1], encoding='utf-8'))
        n = len(s.get_json('processed/latest.json')['files'])
        self.assertEqual((len(ex['entries']), len(ex['sources'])), (n, n))
        self.assertTrue(all(os.path.basename(v) == k for k, v in ex['sources'].items()))
        self.assertTrue(all(e['sha256'] and e['n'] for e in ex['entries']))


if __name__ == '__main__':
    unittest.main()
