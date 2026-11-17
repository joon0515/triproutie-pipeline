"""Wikidata 덤프 조각 풀기 시험 — 합성 bz2(한 스트림, 여러 블록)를 바이트 범위로 나눠 풀어도 줄이 빠지거나 겹치지 않는지. 네트워크 없음."""
import bz2, io, json, os, random, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts')); sys.path.insert(0, os.path.join(HERE, '..', 'scripts', 'collect'))
import wd_dump as W  # noqa: E402


def entity(i, rnd):
    e = {'type': 'item', 'id': f'Q{i}',
         'labels': {'en': {'language': 'en', 'value': f'Place {i}'}, 'ko': {'language': 'ko', 'value': f'장소 {i}'},
                    'zh-hant': {'language': 'zh-hant', 'value': f'地點{i}號'}, 'de': {'language': 'de', 'value': f'Ort {i}'}},
         'descriptions': {'en': {'language': 'en', 'value': f'museum in town number {i}'}, 'ja': {'language': 'ja', 'value': f'{i}番目の町にある博物館'}},
         'claims': {'P18': [{'mainsnak': {'datavalue': {'value': f'Photo of place {i} (old).jpg'}}, 'rank': 'normal'}]},
         'pad': ''.join(rnd.choice('abcdefghij') for _ in range(rnd.randint(50, 900)))}
    if i % 97 == 0:
        e['claims']['P31'] = [{'mainsnak': {'datavalue': {'value': {'id': 'Q4167410'}}}}]
    return json.dumps(e, ensure_ascii=False, separators=(',', ':'))  # 덤프와 같은 꼴(빈칸 없음)


class Dump(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rnd = random.Random(7)
        lines = [entity(i, rnd) for i in range(1, 2501)]
        raw = ('[\n' + ',\n'.join(lines) + '\n]\n').encode()
        cls.data = bz2.compress(raw, compresslevel=1)  # 100KB 블록 → 여러 블록, 한 스트림
        cls.size = len(cls.data)
        cls.eos = W.find_marks(cls.data[-64:], cls.size - 64, W.EOS)[-1]
        cls.n = len(lines)

    def opener(self, start):
        return io.BytesIO(self.data[start:])

    def run_parts(self, parts, want):
        W.WANT.clear(); W.WANT.update(want)
        out, lines = {}, 0
        for p in range(parts):
            o, st = W.scan_part((p, parts, self.size, self.eos, '2026-11-11', self.opener))
            self.assertFalse(set(o) & set(out), f'조각 {p} 겹침')
            out.update(o); lines += st['lines']
        return out, lines

    def test_many_blocks(self):
        self.assertGreater(len(W.find_marks(self.data, 0, W.MAGIC)), 5)

    def test_split_any_way_same_result(self):
        want = set(range(1, 2501, 3))
        base, n1 = self.run_parts(1, want)
        self.assertEqual(set(base), {f'Q{i}' for i in want})
        self.assertEqual(n1, self.n + 2)  # '[' 와 ']' 줄 포함
        for parts in (3, 8, 13):
            got, n = self.run_parts(parts, want)
            self.assertEqual(got, base, parts)
            self.assertEqual(n, n1, parts)

    def test_cache_shape_matches_fetch_wd(self):
        got, _ = self.run_parts(4, {5, 97})
        d, desc, ex = got['Q5']
        self.assertEqual(d, '2026-11-11')
        self.assertEqual(desc, {'en': 'museum in town number 5', 'ja': '5番目の町にある博物館'})
        self.assertEqual(ex['l'], {'ko': '장소 5', 'en': 'Place 5', 'zh_Hant': '地點5號'})  # de 는 칸 밖
        self.assertEqual(ex['p'], W.FILEPATH + 'Photo%20of%20place%205%20%28old%29.jpg')  # QLever 와 같은 꼴
        self.assertEqual(got['Q97'], ['2026-11-11', {}, {'l': {}}])  # 동음이의 문서는 비움(다시 묻지 않게)

    def test_marks_found_at_every_bit_shift(self):
        for r in range(8):
            v = (W.MAGIC << (64 - 48 - r)) | 0
            buf = b'\xaa\x55' + v.to_bytes(8, 'big') + b'\x00'
            self.assertIn(16 + r, W.find_marks(buf, 0, W.MAGIC), r)

    def test_shift_bits(self):
        self.assertEqual(W.shift_bits(b'\x0f\xf0\x00', 4, 8), b'\xff')
        self.assertEqual(W.shift_bits(b'\xff\xff', 0, 12), b'\xff\xf0')


if __name__ == '__main__':
    unittest.main()


class JoinWithPlaceDesc(unittest.TestCase):
    """place_desc(218기)·place_names(223기)와 같은 씨앗·캐시로 이어지는지 — 씨앗 QID 를 남기고, 덤프 결과가 있으면 그 QID 는 다시 묻지 않는다."""

    def test_seeds_exported_and_dump_used(self):
        import tempfile, shutil
        sys.path.insert(0, HERE)
        import test_place_desc as TP
        import place_desc as PD
        from common import Store, today
        tmp = tempfile.mkdtemp()
        try:
            store = Store(os.path.join(tmp, 'store'))
            store.put_json('processed/out/r1/col_JP_0.json.gz', TP.col_layer([{'id': 'wikivoyage:1', 'nm': '東京タワー', 'wd': 'Q183536', 'wid': 'w-tile:1'}]))
            store.put_json('processed/latest.json', {'run': 'r1', 'files': ['col_JP_0.json.gz'], 'paths': {'col_JP_0.json.gz': 'processed/out/r1/col_JP_0.json.gz'}})
            extra = os.path.join(tmp, 'extra'); os.makedirs(extra)
            TP.gz(os.path.join(extra, 'hours-japan.json.gz'), {'schema': 'hours-1', 'rows': {'w-tile:2': {'q': 'Q287165', 'src': 'osm'}}})
            store.put_json('processed/desc/wd_dump.json.gz', {'Q183536': [today(), {'en': 'lattice tower in Minato, Tokyo'}, {'l': {'en': 'Tokyo Tower', 'ja': '東京タワー'}}]})
            h = TP.FakeHttp({})
            PD.run(store, None, extra, os.path.join(tmp, 'out'), 5, http=h)
            self.assertEqual(sorted(h.asked), ['Q287165'])  # 덤프로 채운 QID 는 묻지 않음
            q = store.get_json('processed/desc/qids.json.gz')
            self.assertEqual(q['q'], [183536, 287165])
            self.assertEqual(store.get_json('processed/desc/wd.json.gz')['Q183536'][1], {'en': 'lattice tower in Minato, Tokyo'})
        finally:
            shutil.rmtree(tmp)

    def test_merge(self):
        import tempfile, shutil
        from common import Store
        tmp = tempfile.mkdtemp()
        try:
            store = Store(tmp)
            store.put_json('processed/desc/dump/2026-11-11/job-000.json.gz', {'Q1': ['2026-11-11', {'en': 'a'}, {'l': {'en': 'A'}}]})
            store.put_json('processed/desc/dump/2026-11-11/job-001.json.gz', {'Q2': ['2026-11-11', {}, {'l': {}}]})
            sys.argv = ['wd_dump.py', 'merge', '--store', tmp, '--meta', json.dumps({'date': '2026-11-11', 'qids': 5})]
            import contextlib
            with contextlib.redirect_stdout(io.StringIO()):
                W.main()
            self.assertEqual(sorted(store.get_json('processed/desc/wd_dump.json.gz')), ['Q1', 'Q2'])
            st = store.get_json('processed/desc/dump/state.json')
            self.assertEqual((st['jobs'], st['found'], st['with_desc'], st['with_label']), (2, 2, 1, 1))
        finally:
            shutil.rmtree(tmp)
