"""외교부 입국허가요건 수집기(scripts/visa_mofa.py) — 네트워크 없이. (2026-11-20, 267기)"""
import json, os, sys, unittest, urllib.parse
ROOT = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import visa_mofa as V  # noqa: E402

NUM = json.load(open(os.path.join(ROOT, 'sources', 'iso3166_numeric.json'), encoding='utf-8'))


class Classify(unittest.TestCase):
    def test_free_with_days(self):
        self.assertEqual(V.classify('무비자 입국 가능(90일). 취업·장기체류는 사증 필요'), ('free', 90))
        self.assertEqual(V.classify('사증면제 협정에 따라 3개월 체류 가능'), ('free', 90))

    def test_free_without_days_is_dropped(self):
        self.assertIsNone(V.classify('무비자 입국 가능'))

    def test_eta_and_visa(self):
        self.assertEqual(V.classify('전자여행허가(ESTA) 사전 취득 필요, 90일'), ('eta', 90))
        self.assertEqual(V.classify('사증(비자)을 사전에 발급받아야 함'), ('visa', None))

    def test_ambiguous_dropped(self):
        self.assertIsNone(V.classify(''))
        self.assertIsNone(V.classify(None))
        self.assertIsNone(V.classify('무비자 30일, 단 일부 지역은 사증 필요'))
        self.assertIsNone(V.classify('영주 목적은 사증 필요'))  # 제한 문장만 있으면 판단 안 함


class Collect(unittest.TestCase):
    def _opener(self, pages, seen):
        def op(u):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)
            seen.append(q)
            return json.dumps(pages[int(q['pageNo'][0]) - 1]).encode()
        return op

    def test_collect_two_pages_new_and_old_shapes(self):
        jp = {'country_nm': '일본', 'iso_num': '392', 'gnrl_pspt_visa_cn': '무비자 90일'}
        us = {'country_nm': '미국', 'iso_num': '840', 'gnrl_pspt_visa_cn': 'ESTA 필요 90일'}
        bad = {'country_nm': '어딘가', 'iso_num': '', 'gnrl_pspt_visa_cn': '무비자 30일'}
        pages = [{'response': {'header': {'resultCode': '00'}, 'body': {'totalCount': 3, 'items': {'item': [jp, us]}}}},
                 {'data': [bad], 'totalCount': 3}]
        seen = []
        c = V.Client('SE%43RET', opener=self._opener(pages, seen), sleep=lambda s: None)
        countries, n, unknown, why = V.collect(c, NUM, per=2)
        self.assertEqual((n, why), (3, None))
        self.assertEqual(countries, {'JP': {'need': 'free', 'days': 90}, 'US': {'need': 'eta', 'days': 90}})
        self.assertEqual(unknown, 1)
        self.assertEqual(seen[0]['serviceKey'], ['SECRET'])  # 이미 인코딩된 키는 풀어서 한 번만 인코딩
        self.assertEqual(seen[0]['returnType'], ['JSON'])
        self.assertNotIn('SECRET', json.dumps(V.doc(countries, '2026-11-20')))

    def test_error_code_stops(self):
        seen = []
        c = V.Client('K', opener=self._opener([{'header': {'resultCode': '30', 'resultMsg': 'SERVICE KEY IS NOT REGISTERED'}}], seen), sleep=lambda s: None)
        countries, n, unknown, why = V.collect(c, NUM)
        self.assertEqual((countries, n), ({}, 0))
        self.assertEqual(why, 'resultCode 30')

    def test_xml_error_and_quota(self):
        c = V.Client('K', opener=lambda u: b'<OpenAPI_ServiceResponse>', sleep=lambda s: None)
        self.assertIn('XML', V.collect(c, NUM)[3])
        c2 = V.Client('K', opener=lambda u: b'{}', sleep=lambda s: None, limit=0)
        self.assertEqual(V.collect(c2, NUM)[3], 'LIMIT')

    def test_doc_has_asof_and_iso_table_ok(self):
        d = V.doc({'JP': {'need': 'free', 'days': 90}}, '2026-11-20')
        self.assertEqual(d['asof'], '2026-11-20')
        self.assertIn('2026-11-20', d['src'])
        self.assertEqual((NUM['392'], NUM['840'], NUM['410']), ('JP', 'US', 'KR'))


if __name__ == '__main__':
    unittest.main()
