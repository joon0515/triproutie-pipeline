"""가공(process.py)의 '작게 나눠 흘려보내기' 시험 — 띠 단위 처리·체크포인트 이어받기·예전 누적본 옮기기·관문 표본이 예전과 같은지."""
import gzip, json, os, random, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts', 'collect'))
import common as K  # noqa: E402
import process as P  # noqa: E402

TOOLS = os.path.join(HERE, '..', 'tools')


def raw(store, src, shard, seq, rows, dataset='places', date=None):
    store.put_json(f"raw/{src}/{date or K.today()}/{shard}/000000-{seq:04d}.json.gz", {'schema': 'collect-raw-1', 'source': src, 'dataset': dataset, 'rows': rows})


def row(src, i, nm, lat, lon, cc, **kw):
    return {'id': f'{src}:{i}', 'nm': nm, 'lat': lat, 'lon': lon, 'c': 'food', 'cc': cc, 'src': src, 'lic': 'CC0', **kw}


def synth(store, n=400, seed=7):
    """두 출처 × 두 나라. 절반은 두 출처에 같은 이름·같은 좌표로 들어 있다(묶여야 함). 돌려줌 = {cc: {id: 행}}."""
    rnd, allrows = random.Random(seed), {'JP': {}, 'KR': {}}
    for cc, (la0, lo0) in (('JP', (35.0, 135.0)), ('KR', (36.0, 127.5))):
        a, b = [], []
        for i in range(n):
            la, lo = round(la0 + rnd.random() * 1.5, 6), round(lo0 + rnd.random() * 1.5, 6)
            a.append(row('aa', f'{cc}{i}', f'가게 {cc} {i}', la, lo, cc))
            if i % 2 == 0:
                b.append(row('bb', f'{cc}{i}', f'가게 {cc} {i}', la, lo, cc, h='Mo-Fr 09:00-18:00'))
        for s, rows in (('aa', a), ('bb', b)):
            for q in range(0, len(rows), 90):
                raw(store, s, f'r~{cc}', q // 90, rows[q:q + 90])
            allrows[cc].update({r['id']: dict(r, _ds='places') for r in rows})
    return allrows


def layers(store):
    """latest.json 이 가리키는 장소 층을 풀어 {cc: [(id, 위도e5, 경도e5, tr, src, wid)]} (파일·행 순서대로)."""
    lat, out = store.get_json('processed/latest.json'), {}
    for f in sorted(lat['files'], key=lambda f: (f.split('_')[1], int(f.split('_')[2].split('.')[0]) if f.split('_')[1] != 'info' else 0)):
        j = store.get_json(lat['paths'][f])
        if j.get('dataset') != 'places':
            continue
        la = lo = 0
        for i in range(len(j['id'])):
            la += j['dla'][i]; lo += j['dlo'][i]
            out.setdefault(j['cc'], []).append((j['id'][i], la, lo, j['tr'][i], j['src'][i], j['wid'][i]))
    return out


def old_gate(layers, prev_counts, counts):
    """고치기 전 gate 의 표본 부분(모든 (파일, 행)을 list 로 모아 sample) — 새 구현이 같은 행을 고르는지 견줄 기준."""
    sample, errs = [], []
    for f, j in layers.items():
        for i in range(len(j['id'])):
            sample.append((f, i))
    for f, i in random.Random(20).sample(sample, min(20, len(sample))):
        j = layers[f]
        if not (isinstance(j['id'][i], str) and isinstance(j['nm'][i], str) and isinstance(j['c'][i], int) and 0 <= j['c'][i] < len(j['cats'])
                and isinstance(j['tr'][i], int) and all(j[k][i] is None or isinstance(j[k][i], str) for k in ('ko', 'en', 'ja', 'zh', 'url', 'h'))):
            errs.append(f'{f}#{i}: 표본 모양 틀림')
    return errs


class Stream(unittest.TestCase):
    def test_bands_match_whole_country_result(self):
        """띠로 나눠 처리한 결과 = 나라 전체를 한꺼번에 묶은 결과(예전 방식)."""
        d = tempfile.mkdtemp(); store = K.Store(os.path.join(d, 's'))
        allrows = synth(store)
        run, st = P.process(store, None)
        self.assertEqual(st['pending'], {'raw_files': 0, 'countries': []})
        got = layers(store)
        for cc, merged in allrows.items():
            want = P.cluster_band(merged, P.Index())
            self.assertEqual(len(got[cc]), len(want)); self.assertEqual(st['countries'][cc]['places'], len(want))
            self.assertEqual(sorted(g[0] for g in got[cc]), sorted(m['id'] for m in want))
            self.assertEqual(len(set(g[0] for g in got[cc])), len(got[cc]))  # 같은 곳이 두 파일에 나뉘어 실리지 않음(파일 안 순서·칸 범위는 test_scale.py)
            self.assertEqual(st['countries'][cc]['multi_src'], 200); self.assertEqual(st['countries'][cc]['raw'], 600)
        self.assertGreater(len({P.band_of(r['lat']) for r in allrows['JP'].values()}), 10)  # 실제로 여러 띠
        self.assertEqual(store.list('processed/inbox/'), [])  # 대기함은 비워짐
        self.assertGreater(st['peak_rss_mb'], 0)
        run2, st2 = P.process(store, None)  # 새 원본 없음 → 다시 읽지 않고 층도 그대로
        self.assertEqual((st2['read_files'], st2['countries']), (0, {})); self.assertEqual(layers(store), got)

    def test_resume_from_checkpoint(self):
        """시간 예산이 0 이어도 실행마다 조금씩 나아가고, 여러 번 이어받은 끝 결과가 한 번에 끝낸 결과와 같다."""
        d = tempfile.mkdtemp(); one, many = K.Store(os.path.join(d, 'one')), K.Store(os.path.join(d, 'many'))
        synth(one); synth(many)
        P.process(one, None)
        keep = (P.RAW_PULL, P.FLUSH_ROWS, P.PUSH_BANDS)
        P.RAW_PULL, P.FLUSH_ROWS, P.PUSH_BANDS = 2, 1, 3
        try:
            runs, seen_partial = 0, False
            while True:
                runs += 1
                _, st = P.process(many, None, budget_min=0)
                self.assertLess(runs, 400)
                if not st['pending']['raw_files'] and not st['pending']['countries']:
                    break
                seen_partial = seen_partial or bool(many.get_json('processed/state.json').get('dirty'))
        finally:
            P.RAW_PULL, P.FLUSH_ROWS, P.PUSH_BANDS = keep
        self.assertGreater(runs, 5); self.assertTrue(seen_partial)  # 나라 가운데서 멈췄다가 이어받음
        self.assertEqual(layers(many), layers(one))
        self.assertEqual(many.list('processed/inbox/'), []); self.assertEqual(many.get_json('processed/state.json')['dirty'], [])
        self.assertFalse(many.get_json('processed/state.json')['backfill'])

    def test_world_match_across_band_edge(self):
        """타일 장소가 띠 경계 바로 건너편(50m 안)에 있어도 wid 가 붙는다."""
        d = tempfile.mkdtemp(); store = K.Store(os.path.join(d, 's')); world = os.path.join(d, 'tiles'); os.makedirs(world)
        la, wla = 24.20002, 24.19990  # 서로 다른 띠, 약 13m
        self.assertNotEqual(P.band_of(la), P.band_of(wla))
        t = {'schema': 1, 'id': ['w1'], 'nm': ['太魯閣'], 'ko': [None], 'en': [None], 'ja': [None], 'zh': [None], 'dla': [round(wla * 1e5)], 'dlo': [12160000]}
        open(os.path.join(world, 'w-85_TW.taiwan.json.gz'), 'wb').write(gzip.compress(json.dumps(t).encode()))
        raw(store, 'aa', '0', 0, [row('aa', 1, '太魯閣', la, 121.6, 'TW'), row('aa', 2, '다른 곳', 24.5, 121.6, 'TW')])
        _, st = P.process(store, world)
        self.assertEqual(st['countries']['TW']['matched_world'], 1); self.assertEqual(st['countries']['TW']['world'], 1)
        self.assertEqual([g[5] for g in layers(store)['TW']], ['w1', None])

    def test_legacy_merged_and_untouched_country(self):
        """예전 누적본(나라 한 파일)이 있던 나라에 새 행이 오면 띠로 옮겨 합치고(수가 줄지 않음), 손대지 않은 나라 층은 예전 파일 그대로 실린다."""
        d = tempfile.mkdtemp(); store = K.Store(os.path.join(d, 's'))
        old = {r['id']: r for r in (row('aa', 1, '故宮', 25.1024, 121.5485, 'TW'), row('aa', 2, '太魯閣', 24.16, 121.6, 'TW'))}
        store.put_json('processed/merged/TW.json.gz', old)
        for cc, rows in (('TW', list(old.values())), ('JP', [row('aa', 9, '東京塔', 35.6586, 139.7454, 'JP')])):
            store.put_json(f'processed/out/old/col_{cc}_0.json.gz', P.to_layer(cc, rows))
        store.put_json('processed/latest.json', {'run': 'old', 'files': ['col_JP_0.json.gz', 'col_TW_0.json.gz'], 'counts': {'JP': 1, 'TW': 2}, 'stats': {}})
        store.put_json('processed/state.json', {'last_run': 'old', 'seen': {}})
        raw(store, 'bb', '0', 0, [row('bb', 5, '九份', 25.1092, 121.8443, 'TW')])
        raw(store, 'bb', '0', 1, [row('bb', 6, '옛 파일', 25.2, 121.8, 'TW')], date='2020-01-01')  # 7일 넘은 원본은 (처음이 아니면) 읽지 않음 — 예전과 같음
        run, st = P.process(store, None)
        lat = store.get_json('processed/latest.json')
        self.assertEqual(lat['counts'], {'JP': 1, 'TW': 3}); self.assertEqual(st['read_files'], 1)
        self.assertEqual(lat['paths']['col_JP_0.json.gz'], 'processed/out/old/col_JP_0.json.gz')
        self.assertEqual(lat['paths']['col_TW_0.json.gz'], f'processed/out/{run}/col_TW_0.json.gz')
        self.assertEqual(sorted(g[0] for g in layers(store)['TW']), ['aa:1', 'aa:2', 'bb:5'])
        cur = os.path.join(d, 'cur'); os.makedirs(cur)
        tag, msg = P.publish(store, 'bucket', '', dry_run=True, tools=TOOLS, current_dir=cur)  # 관문·게시는 그대로(두 판의 파일을 함께 읽음)
        self.assertEqual(tag, 'c' + run, msg)

    def test_gate_stream_same_sample_as_before(self):
        rnd, lay = random.Random(3), {}
        for f in range(4):
            rows = [row('aa', f'{f}-{i}', f'n{i}', 35 + i / 1000, 135, 'JP') for i in range(37)]
            j = P.to_layer('JP', rows)
            for i in range(37):
                if rnd.random() < 0.4:
                    j['tr'][i] = 'x'  # 표본에 걸리면 '모양 틀림'
            lay[f'col_JP_{f}.json.gz'] = j
        loads = []
        errs = P.gate_stream(list(lay), lambda f: loads.append(f) or lay[f], {}, {'JP': 148})
        self.assertTrue(errs); self.assertEqual(errs, old_gate(lay, {}, {'JP': 148})); self.assertEqual(errs, P.gate(lay, {}, {'JP': 148}))
        self.assertLessEqual(len(loads), 8)  # 파일마다 검사 1번 + 표본 1번까지

    def test_iter_json_object_small_chunks(self):
        obj = {f'k{i}': {'id': f'k{i}', 'nm': '이름 {"}:,' + 'x' * (i % 13), 'n': [i, {'a': None}]} for i in range(200)}
        p = os.path.join(tempfile.mkdtemp(), 'm.json.gz')
        for text in (json.dumps(obj, ensure_ascii=False, separators=(',', ':')), json.dumps(obj, indent=1), '{}'):
            open(p, 'wb').write(gzip.compress(text.encode()))
            for chunk in (1, 7, 64, 1 << 20):
                self.assertEqual(dict(P.iter_json_object(p, chunk)), json.loads(text))

    def test_store_bulk(self):
        d = tempfile.mkdtemp(); store = K.Store(os.path.join(d, 's'))
        store.put('a/x.bin', b'1'); store.put('a/y.bin', b'2')
        got = store.pull(['a/x.bin', 'a/y.bin'], os.path.join(d, 'l'))
        self.assertEqual(open(got['a/y.bin'], 'rb').read(), b'2')
        with self.assertRaises(IOError):
            store.pull(['a/none.bin'], os.path.join(d, 'l2'))
        self.assertEqual(store.pull(['a/none.bin'], os.path.join(d, 'l3'), strict=False), {})
        store.push(list(got.values()), 'b/c'); self.assertEqual(store.list('b/'), ['b/c/x.bin', 'b/c/y.bin'])
        store.delete(['a/x.bin', 'a/none.bin']); self.assertEqual(store.list('a/'), ['a/y.bin'])


if __name__ == '__main__':
    unittest.main()


class PublishCache295(unittest.TestCase):
    """(295기) 게시 캐시 — 지난번에 검사한 같은 층 파일(같은 키·md5)은 다시 받지 않는다. 바뀌지 않았으면 게시 자체를 건너뛴다."""

    def _store(self, n_files=30):
        sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
        d = tempfile.mkdtemp(); store = K.Store(os.path.join(d, 'store'))
        files, paths = [], {}
        for k in range(n_files):
            f = f'col_JP_{k}.json.gz'
            rows = [{'id': f'x:{k}', 'nm': f'寺{k}', 'lat': 35.0 + k / 100, 'lon': 139.0, 'src': 'x', 'lic': 'CC0', 'c': 'temple', 'url': None}]
            store.put_json(f'processed/out/r1/{f}', P.to_layer('JP', rows))
            files.append(f); paths[f] = f'processed/out/r1/{f}'
        store.put_json('processed/latest.json', {'run': 'r1', 'files': files, 'paths': paths, 'counts': {'JP': n_files}})
        cur = os.path.join(d, 'cur'); os.makedirs(cur)
        pulled = []
        real = store.pull
        store.pull = lambda keys, dest, strict=True: (pulled.extend(keys), real(keys, dest, strict))[1]
        return P, store, files, paths, cur, pulled

    def test_cached_files_not_pulled(self):
        P, store, files, paths, cur, pulled = self._store()
        tools = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools')
        import publish_layers as PL
        tag, msg = P.publish(store, 'bucket', '', dry_run=True, tools=tools, current_dir=cur)
        self.assertTrue(tag, msg)
        self.assertEqual(len(set(pulled)), 30)  # 캐시 없음 → 전부 받음
        # 캐시를 지난 게시가 남긴 모양으로 채운다
        meta = store.meta(paths.values())
        tmp = tempfile.mkdtemp(); got = store.pull(list(paths.values()), tmp)
        gfiles = {paths[f]: {'n': 1, 'errs': [], 'entry': PL.describe(got[paths[f]]), 'meta': meta[paths[f]]} for f in files}
        fsig = __import__('hashlib').sha1(__import__('json').dumps([sorted(P.FORBIDDEN), sorted(P.FORBIDDEN_LABELS)]).encode()).hexdigest()
        store.put_json(P.GATE_CACHE, {'fsig': fsig, 'files': gfiles})
        pulled.clear(); store.put_json('published/history.json', [])
        tag, msg = P.publish(store, 'bucket', '', dry_run=True, tools=tools, current_dir=cur)
        self.assertTrue(tag, msg)
        self.assertLessEqual(len(pulled), 20)  # 표본 20곳 파일만(파일당 1개씩)
        # 파일 하나가 바뀌면(md5 다름) 그 파일은 다시 검사한다
        j = store.get_json(paths[files[5]]); j['nm'][0] = '��'; store.put_json(paths[files[5]], j)
        pulled.clear(); store.put_json('published/history.json', [])
        tag, errs = P.publish(store, 'bucket', '', dry_run=True, tools=tools, current_dir=cur)
        self.assertIsNone(tag)
        self.assertTrue(any(files[5] in e and '글자 깨짐' in e for e in errs), errs)

    def test_unchanged_paths_skip_publish(self):
        P, store, files, paths, cur, pulled = self._store(3)
        import hashlib, json
        psig = hashlib.sha1(json.dumps(sorted(paths.values())).encode()).hexdigest()
        store.put_json('published/history.json', [{'tag': 'c0', 'run': 'r0', 'counts': {'JP': 3}, 'dry': False, 'psig': psig}])
        tag, msg = P.publish(store, 'bucket', '', dry_run=True, tools='tools', current_dir=cur)
        self.assertIsNone(tag)
        self.assertTrue(any('바뀐 층 파일 없음' in m for m in msg), msg)
        self.assertEqual(pulled, [])
