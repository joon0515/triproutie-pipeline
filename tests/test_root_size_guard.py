"""뿌리 크기 가드(263기): 정상이면 통과, 가드 아래로 못 맞추면 실패."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts', 'guards'))
import root_size as G
import publish_layers as P


class RootSizeGuard(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(P.ROOT_GUARD, 48 * 1024)
        self.assertLess(P.ROOT_TARGET, P.ROOT_GUARD)
        self.assertLess(P.ROOT_GUARD, P.ROOT_MAX)

    def test_all_scales_pass(self):
        out = G.check()
        self.assertTrue(all(n <= P.ROOT_GUARD for _, n in out), out)

    def test_guard_fails_when_over(self):
        with self.assertRaises(ValueError):
            G.check(scales=(3,), limit=8 * 1024)  # 한도를 낮추면 걸린다(가드가 실제로 작동)

    def test_publish_refuses_over_guard(self):
        old = P.ROOT_TARGET
        P.ROOT_TARGET = 70 * 1024  # 목표를 망가뜨린 가짜 회귀: 옛 항목이 가드를 넘게 실림
        try:
            with self.assertRaises(ValueError):
                G.check(scales=(1, 3))
        finally:
            P.ROOT_TARGET = old

    def test_legacy_off_switch(self):
        os.environ['LEGACY_ROOT'] = 'off'
        try:
            self.assertLess(G.root_bytes(1), 16 * 1024)
            flat = G.canon_value(P.build_manifest('t', [], extra=[G._ent('hours', 1)]))
            root, blobs = P.split_manifest(flat, G.canonical)
            self.assertEqual((root['layers'], 'legacy_omitted' in root, len(blobs)), ([], False, 1))
        finally:
            del os.environ['LEGACY_ROOT']


if __name__ == '__main__':
    unittest.main()
