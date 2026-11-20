"""photo_pri 의 덤프 좌표(pov)·이름+좌표(name_geo) 사진 행 고르기 — 특성화 시험(284기, 옛 run() 안 반복문과 같은 결과)."""
import os, sys, unittest

ROOT = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, os.path.join(ROOT, 'scripts')); sys.path.insert(0, os.path.join(ROOT, 'scripts', 'collect')); sys.path.insert(0, os.path.join(ROOT, 'tools'))
import photo_pri as PP  # noqa: E402
import place_names as PN  # noqa: E402

U = PN.FILEPATH + 'A_b.jpg'
OK = {'lic': 'Q6938433', 'by': 'Kim'}


class ExtraRows(unittest.TestCase):
    def test_pov_skips_rows_that_already_have_a_photo_and_bad_entries(self):
        pov = {'rows': {'r2': [7, 0, U, OK], 'r1': [3, 0, U, OK], 'have': [1, 0, U, OK], 'short': [1, 0, U], 'badlic': [1, 0, U, {'lic': 'x', 'by': 'a'}]}}
        got = PP.pov_rows_missing(pov, {'have': {'photo_src': 'p18'}})
        self.assertEqual(list(got), ['r1', 'r2'])
        self.assertEqual(got['r1']['match'], 'pov_3')
        self.assertEqual(got['r1']['photo_author'], 'Kim')

    def test_pov_empty_doc(self):
        self.assertEqual(PP.pov_rows_missing(None, {}), {})
        self.assertEqual(PP.pov_rows_missing({'rows': None}, {}), {})

    def test_name_geo_needs_judged_file_and_free_row(self):
        hits = {'b': ('Q2', U), 'a': ('Q1', U), 'have': ('Q3', U), 'nojudge': ('Q4', PN.FILEPATH + 'X.jpg')}
        got = PP.name_geo_rows_missing(hits, {U: [0, OK]}, {'have': {}})
        self.assertEqual(list(got), ['a', 'b'])
        self.assertEqual((got['a']['q'], got['a']['match']), ('Q1', 'name_geo'))


if __name__ == '__main__':
    unittest.main()
