"""fx_daily 시험(네트워크·키 없음): python3 -m unittest tests/test_fx_daily.py"""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import fx_daily as fx  # noqa: E402

NOW = dt.datetime(2026, 11, 17, 21, 10)


def rows(date='2026-11-16', **over):
    base = {'JPY': 0.11963, 'USD': 0.00074853, 'EUR': 0.00064, 'CNY': 0.0053, 'THB': 0.024, 'VND': 19.7}
    base.update(over)
    return [{'date': date, 'base': 'KRW', 'quote': q, 'rate': r} for q, r in base.items()]


class T(unittest.TestCase):
    def test_parse(self):
        d, kp = fx.parse(rows())
        self.assertEqual(d, '2026-11-16')
        self.assertAlmostEqual(kp['USD'], 1335.95, places=1)
        self.assertAlmostEqual(kp['JPY'], 8.359, places=2)
        self.assertAlmostEqual(kp['VND'], 0.0507614, places=6)

    def test_parse_rejects(self):
        self.assertIsNone(fx.parse({'x': 1}))
        self.assertIsNone(fx.parse(rows()[:4]))  # 5개 미만
        bad = rows() + [{'date': '2026-11-16', 'base': 'USD', 'quote': 'GBP', 'rate': 1}, {'date': '2026-11-16', 'base': 'KRW', 'quote': 'XXX', 'rate': 1},
                        {'date': '2026-11-16', 'base': 'KRW', 'quote': 'GBP', 'rate': 0}, {'date': 'x', 'base': 'KRW', 'quote': 'AUD', 'rate': 1}, {'date': '2026-11-16', 'base': 'KRW', 'quote': 'SGD', 'rate': True}]
        self.assertEqual(sorted(fx.parse(bad)[1]), ['CNY', 'EUR', 'JPY', 'THB', 'USD', 'VND'])

    def test_guard(self):
        kp = fx.parse(rows())[1]
        out, held = fx.guard(kp, {'krw_per': {'USD': 900.0, 'JPY': 8.3}})
        self.assertEqual(held, ['USD'])
        self.assertEqual(out['USD'], 900.0)
        self.assertAlmostEqual(out['JPY'], 8.359, places=2)
        self.assertEqual(fx.guard(kp, None)[1], [])
        self.assertEqual(fx.guard(kp, {'krw_per': 'x'})[1], [])

    def test_build(self):
        doc = fx.build(rows(), None, NOW)
        self.assertEqual((doc['schema'], doc['base'], doc['date'], doc['held']), (1, 'KRW', '2026-11-16', []))
        self.assertEqual(doc['fetched_at'], '2026-11-17T21:10:00Z')
        self.assertEqual(list(doc['krw_per']), sorted(doc['krw_per']))
        self.assertIn('실시간 시세 아님', doc['source'])

    def test_build_rejects_old_or_older(self):
        self.assertIsNone(fx.build(rows('2026-11-01'), None, NOW))  # 출처 기준일이 10일 넘게 묵음
        self.assertIsNone(fx.build(rows('2026-11-13'), {'date': '2026-11-16', 'krw_per': {}}, NOW))  # 더 옛 기준일
        self.assertIsNotNone(fx.build(rows('2026-11-13'), {'date': '2026-11-13', 'krw_per': {}}, NOW))  # 주말: 같은 기준일
        self.assertIsNone(fx.build(rows('2026-13-99'), None, NOW))
        self.assertIsNone(fx.build([], None, NOW))

    def test_main_file(self):
        with tempfile.TemporaryDirectory() as d:
            src, out, prev = (os.path.join(d, n) for n in ('r.json', 'o.json', 'p.json'))
            today = dt.datetime.now(dt.timezone.utc).date().isoformat()
            json.dump(rows(today), open(src, 'w'))
            json.dump({'date': '2020-01-01', 'krw_per': {'JPY': 80.0}}, open(prev, 'w'))
            self.assertEqual(fx.main(['--out', out, '--prev', prev, '--from-file', src]), 0)
            doc = json.load(open(out))
            self.assertEqual(doc['held'], ['JPY'])
            self.assertEqual(doc['krw_per']['JPY'], 80.0)
            json.dump([], open(src, 'w'))
            os.remove(out)
            self.assertEqual(fx.main(['--out', out, '--from-file', src]), 2)
            self.assertFalse(os.path.exists(out))


if __name__ == '__main__':
    unittest.main()
