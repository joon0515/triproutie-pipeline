"""(299기) scripts/dedup_gcs.py — 가짜 목록·가짜 포인터로 남김 규칙 시험(네트워크·키 없음)."""
import hashlib, json, os, shutil, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import dedup_gcs as D  # noqa: E402


def md5(b):
    import base64
    return base64.b64encode(hashlib.md5(b).digest()).decode()


class Fake:
    """로컬 폴더 = 버킷. 목록 jsonl 은 파일에서 만든다(t = 넣은 순서)."""

    def __init__(self):
        self.d = tempfile.mkdtemp(); self.rows = []; self.i = 0

    def put(self, name, b):
        if isinstance(b, (dict, list)):
            b = json.dumps(b).encode()
        p = os.path.join(self.d, 'b', name); os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'wb').write(b)
        self.i += 1
        self.rows = [r for r in self.rows if r['name'] != name]
        self.rows.append({'name': name, 'size': len(b), 'md5': md5(b), 'crc': str(hash(b) & 0xffff), 'gen': self.i, 't': f'2026-11-{self.i:02d}T00:00:00'})
        return b

    def listing(self):
        p = os.path.join(self.d, 'list.jsonl')
        open(p, 'w').write(''.join(json.dumps(r) + '\n' for r in self.rows))
        return p

    def root(self):
        return os.path.join(self.d, 'b')


def build():
    f = Fake()
    A, B, OLDU, TILE, COL, UNI = b'layer-A' * 10, b'layer-B' * 10, b'old-unique' * 9, b'tile-1' * 9, b'col-KR' * 9, b'orphan' * 11
    f.put('collect/v1/raw/src/2026-11-01/p1.jsonl.gz', COL)            # raw: 내용이 out 과 같아도 절대 대상 아님
    f.put('collect/v1/raw/src/2026-11-02/p1.jsonl.gz', COL)
    # 가공 판: R1(옛), R2(latest)
    f.put('collect/v1/processed/out/R1/col_KR_1.json.gz', COL)        # latest.paths 가 가리킴(옛 run 폴더지만 참조)
    f.put('collect/v1/processed/out/R1/col_JP_1.json.gz', b'jp-old' * 9)  # 참조 없음, 고유 → old
    f.put('collect/v1/processed/out/R0/col_US_1.json.gz', UNI)        # 참조 없음, R0(먼저)·R1(나중) 둘 다 → 최근 것(R1) 하나 남김
    f.put('collect/v1/processed/out/R1/col_US_1.json.gz', UNI)
    f.put('collect/v1/processed/out/R2/col_JP_1.json.gz', b'jp-new' * 9)
    f.put('collect/v1/processed/out/R3/col_KR_1.json.gz', COL)        # 합치기 전 조각 결과(latest 보다 새 run) → 보존
    f.put('collect/v1/processed/out/R1b/col_TW_1.json.gz', b'tw' * 30)  # 조각 파일이 가리킴 → 보존
    f.put('collect/v1/processed/shards/latest_0.json', {'batch': 'x', 'cc': {'TW': {'files': {'col_TW_1.json.gz': 'processed/out/R1b/col_TW_1.json.gz'}}}})
    f.put('collect/v1/processed/bands/KR/m_0370.jsonl.gz', b'band' * 20)   # 이름 규칙으로 읽힘 → 후보 아님
    f.put('collect/v1/processed/out/R0/copy_of_band.json.gz', b'band' * 20)  # 띠와 같은 내용 → 지움(띠가 남음)
    f.put('collect/v1/processed/latest.json', {'run': 'R2', 'files': ['col_JP_1.json.gz', 'col_KR_1.json.gz'],
                                              'paths': {'col_KR_1.json.gz': 'processed/out/R1/col_KR_1.json.gz', 'col_JP_1.json.gz': 'processed/out/R2/col_JP_1.json.gz'}})
    # 층 판: c1(옛) c2(이전) c3(지금)
    for t in ('c1', 'c2', 'c3'):
        f.put(f'places/v1/{t}/layers/col_KR_1.json.gz', COL)
        f.put(f'places/v1/{t}/layers/amen-a.json.gz', A)
    f.put('places/v1/c1/layers/old-only.json.gz', OLDU)
    f.put('places/v1/c1/layers/empty.json.gz', b'')
    f.put('places/v1/c3/layers/amen-b.json.gz', B)
    f.put('places/v1/c1/layers/amen-b.json.gz', B)
    for t in ('c1', 'c2'):
        f.put(f'places/v1/{t}/layers.json', {'path': f'places/v1/{t}/layers/', 'layers': [{'file': 'amen-a.json.gz'}]})
    part = f.put('places/v1/c3/layer_parts/collect-000.json', {'path': 'places/v1/c3/layers/', 'layers': [{'file': 'col_KR_1.json.gz'}, {'file': 'amen-b.json.gz'}]})
    root = {'version': 'c3', 'path': 'places/v1/c3/layers/', 'parts_path': 'places/v1/c3/layer_parts/', 'layers': [{'file': 'amen-a.json.gz'}],
            'parts': [{'file': 'collect-000.json', 'size': len(part), 'sha256': hashlib.sha256(part).hexdigest()}]}
    f.put('places/v1/c3/layers.json', root)
    f.put('places/v1/layers.json', root)
    f.put('places/v1/layers_extra/amen-a.json.gz', A)                  # publish 가 이름 규칙으로 읽음 → 후보 아님
    f.put('collect/v1/published/history.json', [{'tag': 'c1'}, {'tag': 'c2'}, {'tag': 'c3'}, {'tag': 'c9', 'dry': True}])
    # 세계 타일: W1(옛) W2(지금)
    f.put('places/v1/W0/tiles/w-1.json.gz', TILE)                       # 가장 옛 세계 판(만든 순서 W0 < W1 < W2)
    f.put('places/v1/W0/manifest.json', {'path': 'places/v1/W0/tiles/', 'regions': []})
    f.put('places/v1/W1/tiles/w-1.json.gz', TILE)
    f.put('places/v1/W1/manifest.json', {'path': 'places/v1/W1/tiles/', 'regions': []})
    f.put('places/v1/W2/tiles/w-1.json.gz', TILE)
    reg = f.put('places/v1/W2/regions/asia.json', {'path': 'places/v1/W2/tiles/', 'tiles': [{'file': 'w-1.json.gz'}]})
    man = {'version': 'W2', 'path': 'places/v1/W2/tiles/', 'regions': [{'path': 'places/v1/W2/regions/asia.json', 'sha256': hashlib.sha256(reg).hexdigest(), 'size': len(reg)}]}
    f.put('places/v1/W2/manifest.json', man)
    f.put('places/v1/manifest.json', man)
    return f


class DedupRules(unittest.TestCase):
    def setUp(self):
        self.f = build(); self.out = os.path.join(self.f.d, 'out')

    def tearDown(self):
        shutil.rmtree(self.f.d, ignore_errors=True)

    def plan(self):
        rc = D.main(['plan', '--bucket', 'B', '--out', self.out, '--listing', self.f.listing(), '--fake-root', self.f.root()])
        self.assertEqual(rc, 0)
        return json.load(open(os.path.join(self.out, 'plan.json')))

    def test_rules(self):
        P = self.plan()
        dup = {o['name'] for o in P['dup']}
        old = {o['name'] for o in P['old']}
        # 참조 경로 보존
        for n in ('places/v1/c3/layers/col_KR_1.json.gz', 'places/v1/c3/layers/amen-a.json.gz', 'places/v1/c3/layers/amen-b.json.gz',
                  'collect/v1/processed/out/R1/col_KR_1.json.gz', 'collect/v1/processed/out/R2/col_JP_1.json.gz', 'places/v1/W2/tiles/w-1.json.gz',
                  'collect/v1/processed/out/R3/col_KR_1.json.gz', 'collect/v1/processed/out/R1b/col_TW_1.json.gz'):
            self.assertNotIn(n, dup | old, n)
        # 되돌리기용 이전 판(c2, 이전 세계 판 W1) 보존
        self.assertFalse(any(n.startswith(('places/v1/c2/', 'places/v1/W1/')) for n in dup | old))
        self.assertEqual(P['info']['prev_tags'], ['W1', 'c2'])
        # raw·이름 규칙 경로(띠·layers_extra)·.json·0바이트 제외
        self.assertFalse(any(n.startswith(('collect/v1/raw/', 'collect/v1/processed/bands/', 'places/v1/layers_extra/')) for n in dup | old))
        self.assertFalse(any(n.endswith('.json') for n in dup | old))
        self.assertNotIn('places/v1/c1/layers/empty.json.gz', dup | old)
        # 같은 내용 사본
        self.assertEqual(dup, {'places/v1/c1/layers/col_KR_1.json.gz', 'places/v1/c1/layers/amen-a.json.gz', 'places/v1/c1/layers/amen-b.json.gz',
                               'places/v1/W0/tiles/w-1.json.gz', 'collect/v1/processed/out/R0/col_US_1.json.gz', 'collect/v1/processed/out/R0/copy_of_band.json.gz'})
        # 참조 없는 묶음은 가장 최근 하나(R1)를 남김 → old 쪽으로
        self.assertIn('collect/v1/processed/out/R1/col_US_1.json.gz', old)
        self.assertEqual(old, {'places/v1/c1/layers/old-only.json.gz', 'collect/v1/processed/out/R1/col_JP_1.json.gz', 'collect/v1/processed/out/R1/col_US_1.json.gz'})
        self.assertEqual(P['checks']['dup'], {'overlap_protected': 0, 'overlap_raw': 0, 'overlap_json_or_zero': 0})

    def test_every_group_keeps_one(self):
        P = self.plan()
        dn = {o['name'] for o in P['dup']}
        rows = [json.loads(l) for l in open(self.f.listing())]
        for o in P['dup']:
            self.assertTrue(any(r['md5'] == o['md5'] and r['size'] == o['size'] and r['name'] not in dn for r in rows), o['name'])

    def test_delete_needs_matching_hash_then_verify(self):
        self.plan()
        pl = os.path.join(self.out, 'plan.json'); sha = open(os.path.join(self.out, 'plan.sha256')).read().strip()
        args = ['delete', '--bucket', 'B', '--out', self.out, '--plan', pl, '--listing', self.f.listing(), '--fake-root', self.f.root()]
        with self.assertRaises(SystemExit):
            D.main(args + ['--sha256', '0' * 64])
        with self.assertRaises(SystemExit):  # old 는 확인 문구 없이는 안 됨
            D.main(args + ['--sha256', sha, '--scope', 'old'])
        self.assertEqual(D.main(args + ['--sha256', sha]), 0)
        self.assertFalse(os.path.exists(os.path.join(self.f.root(), 'places/v1/c1/layers/amen-a.json.gz')))
        self.assertTrue(os.path.exists(os.path.join(self.f.root(), 'places/v1/c3/layers/amen-a.json.gz')))
        self.assertTrue(os.path.exists(os.path.join(self.f.root(), 'collect/v1/raw/src/2026-11-02/p1.jsonl.gz')))
        # 다시 돌려도 같은 결과(이미 없음 → 0개 지움)
        self.assertEqual(D.main(args + ['--sha256', sha]), 0)
        self.assertIn('이미 없음 6', open(os.path.join(self.out, 'delete_summary.md')).read())
        self.assertEqual(D.main(['verify', '--bucket', 'B', '--out', self.out, '--listing', self.f.listing(), '--fake-root', self.f.root()]), 0)

    def test_delete_refuses_when_newly_protected(self):
        self.plan()
        pl = os.path.join(self.out, 'plan.json'); sha = open(os.path.join(self.out, 'plan.sha256')).read().strip()
        # 계획 뒤 c1 이 다시 게시됨(되돌리기) → c1 사본은 이제 참조 경로
        root = json.loads(open(os.path.join(self.f.root(), 'places/v1/c1/layers.json')).read())
        self.f.put('places/v1/layers.json', root)
        with self.assertRaises(SystemExit):
            D.main(['delete', '--bucket', 'B', '--out', self.out, '--plan', pl, '--sha256', sha, '--listing', self.f.listing(), '--fake-root', self.f.root()])
        self.assertTrue(os.path.exists(os.path.join(self.f.root(), 'places/v1/c1/layers/amen-a.json.gz')))

    def test_pointer_missing_stops(self):
        os.remove(os.path.join(self.f.root(), 'places/v1/manifest.json'))
        rc = D.main(['plan', '--bucket', 'B', '--out', self.out, '--listing', self.f.listing(), '--fake-root', self.f.root()])
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(os.path.join(self.out, 'plan.json')))

    def test_read_cap(self):
        r = D.Reader('B', self.f.root(), cap=10)
        with self.assertRaises(SystemExit):
            r('places/v1/layers.json')


if __name__ == '__main__':
    unittest.main()
