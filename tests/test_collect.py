"""세계 수집 모의 시험(네트워크·키 없음): 예절 HTTP·금지 목록·라이선스 판정·파서·계획(70/30)·3단 흐름(수집→가공→관문→게시 dry-run).
  python3 -m unittest tests/test_collect.py"""
import bz2, gzip, io, json, os, sys, tempfile, unittest, urllib.error, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts', 'collect'))
import common as K  # noqa: E402
import collectors as C  # noqa: E402
import process as P  # noqa: E402
import run as R  # noqa: E402

REG = R.load_registry()


class Web:
    """사이트 흉내: pages = {url: bytes 또는 int(HTTP 오류 코드)}. robots.txt 가 없으면 404."""

    def __init__(self, pages):
        self.pages, self.calls, self.ua = pages, [], set()

    def __call__(self, url, headers):
        self.calls.append(url); self.ua.add(headers.get('User-Agent'))
        v = self.pages.get(url)
        if v is None or isinstance(v, int):
            raise urllib.error.HTTPError(url, v or 404, 'x', {}, None)
        return 200, {}, v


def src(**kw):
    return dict({'id': 't', 'license': 'CC0', 'daily_limit': 5, 'interval_s': 1}, **kw)


class Basics(unittest.TestCase):
    def test_forbidden(self):
        for u in ('https://www.instagram.com/x', 'https://m.place.naver.com/1', 'https://www.google.co.jp/maps', 'https://tabelog.com/a',
                  'https://www.tripadvisor.fr/x', 'https://map.kakao.com/', 'https://vt.tiktok.com/a'):
            self.assertTrue(K.forbidden(u), u)
        self.assertFalse(K.forbidden('https://www.louvre.fr/en'))

    def test_license(self):
        for ok in ('CC0-1.0', 'cc-by-4.0', 'odc-odbl', 'ca-ogl-lgo', 'OGL-UK-3.0', 'Licence Ouverte 2.0', 'CDLA-Permissive-2.0'):
            self.assertTrue(K.license_open(ok), ok)
        self.assertTrue(K.license_open('https://creativecommons.org/licenses/by/4.0/'))
        self.assertFalse(K.license_open('https://creativecommons.org/licenses/by-nc/4.0/'))
        for no in ('cc-by-nc-4.0', 'CC BY-ND', 'proprietary', '', 'other-closed', 'notspecified'):
            self.assertFalse(K.license_open(no), no)

    def test_hours(self):
        self.assertEqual(K.norm_hours('Mo-Fr 9:00-18:00'), 'Mo-Fr 09:00-18:00')
        self.assertEqual(K.norm_hours([{'dayOfWeek': ['https://schema.org/Monday', 'Tuesday'], 'opens': '10:00', 'closes': '17:00:00'}]), 'Mo,Tu 10:00-17:00')
        self.assertTrue(K.norm_hours('daily, ask staff').startswith('raw:'))


class Polite(unittest.TestCase):
    def test_robots_limit_block(self):
        w = Web({'https://a.org/robots.txt': b'User-agent: *\nDisallow: /private\nCrawl-delay: 3\n', 'https://a.org/ok': b'1', 'https://a.org/b': 403,
                 'https://b.org/x': b'2'})
        slept, st, hosts = [], {}, {}
        h = K.Http(src(), st, hosts, opener=w, sleep=slept.append, clock=lambda: 100.0)
        self.assertEqual(h.get('https://a.org/ok'), b'1')
        with self.assertRaises(K.Blocked):
            h.get('https://a.org/private/1')  # robots 금지 → 요청 자체를 안 보냄
        self.assertNotIn('https://a.org/private/1', w.calls)
        h.get('https://a.org/ok')
        self.assertTrue(any(s >= 3 for s in slept))  # Crawl-delay 3초 지킴
        with self.assertRaises(K.Blocked):
            h.get('https://a.org/b')  # 403 → 7일 쉼
        self.assertIn('a.org', hosts)
        with self.assertRaises(K.Blocked):
            h.get('https://a.org/ok')  # 쉬는 동안 요청 안 함
        h.get('https://b.org/x')  # robots.txt 404 = 허용
        h.st['req'] = 5
        with self.assertRaises(K.Stop):
            h.get('https://b.org/x')
        self.assertEqual(w.ua, {K.UA})
        self.assertIn('github.com/joon0515/triproutie-pipeline', K.UA)

    def test_forbidden_never_requested(self):
        w = Web({})
        h = K.Http(src(), {}, {}, opener=w, sleep=lambda s: None)
        with self.assertRaises(K.Blocked):
            h.get('https://www.instagram.com/p/1')
        self.assertEqual(w.calls, [])


WV = """<mediawiki><page><title>Kyoto</title><revision><text>==See==
* {{see | name=Kinkaku-ji | alt=Golden Pavilion | lat=35.0394 | long=135.7292 | hours=9:00-17:00 | price=¥500 | wikidata=Q131234
| content=A long [[Zen]] text that must not be copied }}
* {{eat | name=Bad | lat= | long= }}
* {{sleep|name=Inn {{nowrap|A}}|lat=35.0|long=135.7|url=https://www.tripadvisor.com/x}}
</text></revision></page><page><title>Talk:Kyoto</title><revision><text>{{see|name=X|lat=1|long=1}}</text></revision></page></mediawiki>"""


class Parsers(unittest.TestCase):
    def test_wikivoyage(self):
        out = []
        C.parse_wikivoyage(io.BytesIO(WV.encode()), src(id='wikivoyage', license='CC-BY-SA-4.0'), 'en', out.extend)
        self.assertEqual(len(out), 2)
        k = out[0]
        self.assertEqual((k['nm'], k['c'], k['h'], k['wd'], k['city']), ('Kinkaku-ji', 'sight', '09:00-17:00', 'Q131234', 'Kyoto'))
        self.assertNotIn('content', json.dumps(out))  # 본문 글 없음
        self.assertNotIn('url', out[1])  # 금지 도메인 링크 버림

    def test_gtfs(self):
        csvb = ('id,data_type,status,urls.authentication_type,urls.license,urls.latest,provider,location.country_code,location.municipality,redirect.id\n'
                'mdb-1,gtfs,active,0,https://creativecommons.org/licenses/by/4.0/,https://files.mobilitydatabase.org/mdb-1/latest.zip,Metro,JP,Kyoto,\n'
                'mdb-2,gtfs,active,0,,https://x.org/2.zip,NoLic,FR,Paris,\n'
                'mdb-3,gtfs,active,1,https://creativecommons.org/licenses/by/4.0/,https://x.org/3.zip,Key,FR,Lyon,\n'
                'mdb-4,gtfs-rt,active,0,https://creativecommons.org/licenses/by/4.0/,https://x.org/4,RT,FR,Lyon,\n').encode()
        f = C.gtfs_feeds(csvb)
        self.assertEqual([x['id'] for x in f], ['mdb-1'])
        z = io.BytesIO()
        with zipfile.ZipFile(z, 'w') as zz:
            zz.writestr('stops.txt', 'stop_id,stop_name,stop_lat,stop_lon,location_type\nS1,Kyoto Sta,34.985,135.758,1\nP1,Platform,34.985,135.758,0\n')
            zz.writestr('routes.txt', 'route_id,route_type\nR1,1\nR2,3\n')
        rows, info = C.parse_gtfs(z.getvalue(), src(id='mobility_gtfs'), f[0])
        self.assertEqual(len(rows), 1); self.assertEqual(info['routes'], 2); self.assertEqual(rows[0]['cc'], 'JP')

    def test_taiwan(self):
        z = io.BytesIO()
        with zipfile.ZipFile(z, 'w') as zz:
            zz.writestr('a.json', json.dumps({'XML_Head': {'Infos': {'Info': [
                {'Id': 'C1_1', 'Name': '太魯閣', 'Px': 121.6, 'Py': 24.16, 'Opentime': '全天', 'Tel': '886-3-8621100', 'Description': '長文', 'Picture1': 'x.jpg'},
                {'Id': 'C1_2', 'Name': '壞', 'Px': 0, 'Py': 0}]}}}, ensure_ascii=False))
        rows = C.parse_taiwan(z.getvalue(), src(id='taiwan_tourism'))
        self.assertEqual(len(rows), 1); self.assertEqual(rows[0]['zh'], '太魯閣')
        self.assertNotIn('長文', json.dumps(rows, ensure_ascii=False)); self.assertNotIn('x.jpg', json.dumps(rows))

    def test_structured(self):
        page = """<html><script type="application/ld+json">{"@context":"https://schema.org","@graph":[
          {"@type":"Museum","name":"Mori Art Museum","address":{"streetAddress":"6-10-1 Roppongi","addressLocality":"Tokyo"},
           "geo":{"latitude":35.6604,"longitude":139.7292},"openingHours":"Mo-Su 10:00-22:00","telephone":"+81-50-5541-8600"},
          {"@type":"Person","name":"Jane Curator","email":"jane@x.org","telephone":"+1"},
          {"@type":"Event","name":"Expo","startDate":"2026-12-01","endDate":"2027-01-10","location":{"name":"Gallery 1"}}]}</script>
          <div itemscope itemtype="https://schema.org/Restaurant"><span itemprop="name">Cafe M</span><meta itemprop="openingHours" content="Mo-Fr 9:00-17:00"></div>
          <a href="/exhibitions">x</a><a href="https://www.instagram.com/mori">ig</a></html>"""
        items, links = C.structured(page)
        kinds = [k for k, _ in items]
        self.assertEqual(sorted(kinds), ['event', 'museum', 'restaurant'])
        self.assertNotIn('Jane', json.dumps([i for _, i in items]))
        f = C.facts_of('museum', dict(items)['museum'])
        self.assertEqual((f['lat'], f['hours']), (35.6604, 'Mo-Su 10:00-22:00'))
        self.assertIn('Tokyo', f['addr'])


class Planner(unittest.TestCase):
    def test_plan_split_and_caps(self):
        p = R.plan(REG, {}, env={})
        self.assertLessEqual(len(p), 18)
        d = sum(1 for x in p if next(s for s in REG['sources'] if s['id'] == x['source'])['script'] in C.DISCOVERY)
        self.assertGreaterEqual(d, 5)  # 30% 몫
        self.assertFalse(any(x['source'] == 'fsq_os_places' for x in p))  # Secret 없으면 넣지 않음
        self.assertLessEqual(sum(1 for x in p if x['source'] == 'overture_places'), 4)
        self.assertFalse(any(next(s for s in REG['sources'] if s['id'] == x['source']).get('status') != 'active' for x in p))
        p2 = R.plan(REG, {}, env={'HAS_FSQ_TOKEN': 'true'})
        self.assertTrue(any(x['source'] == 'fsq_os_places' for x in p2))

    def test_registry_fields(self):
        for s in REG['sources']:
            for k in ('id', 'name', 'kind', 'url', 'license', 'status'):
                self.assertIn(k, s, s['id'])
            if s['status'] == 'active':
                for k in ('attribution', 'robots', 'refresh', 'daily_limit', 'script'):
                    self.assertIn(k, s, s['id'])
                self.assertIn(s['script'], C.REGISTRY)
            self.assertFalse(K.forbidden(s['url']) and s['url'].startswith('http'), s['id'])


def tile(cc, rows):
    j = {'schema': 1, 'pack': 'w-x', 'cc': cc, 'id': [], 'nm': [], 'ko': [], 'en': [], 'ja': [], 'zh': [], 'dla': [], 'dlo': []}
    pa = po = 0
    for i, nm, la, lo in rows:
        a, b = round(la * 1e5), round(lo * 1e5)
        j['id'].append(i); j['nm'].append(nm); j['dla'].append(a - pa); j['dlo'].append(b - po); pa, po = a, b
        for k in ('ko', 'en', 'ja', 'zh'):
            j[k].append(None)
    return gzip.compress(json.dumps(j).encode())


class Flow(unittest.TestCase):
    """수집(조각 3개) → 가공(타일과 중복 제거·출처 병합·trust) → 관문 → 게시(dry-run) → 감소 시 관문이 막음."""

    def test_three_stages(self):
        d = tempfile.mkdtemp(); store = K.Store(os.path.join(d, 'store')); world = os.path.join(d, 'tiles'); os.makedirs(world)
        open(os.path.join(world, 'w-85_TW.taiwan.json.gz'), 'wb').write(tile('TW', [('ovt1', '太魯閣', 24.16001, 121.60002)]))
        z = io.BytesIO()
        with zipfile.ZipFile(z, 'w') as zz:
            zz.writestr('a.json', json.dumps({'Infos': {'Info': [{'Id': 'C1', 'Name': '太魯閣', 'Px': 121.6, 'Py': 24.16},
                                                                 {'Id': 'C2', 'Name': '故宮博物院', 'Px': 121.5485, 'Py': 25.1024, 'Opentime': 'Tu-Su 9:00-17:00'}]}}))
        ca = json.dumps({'data': {'JP': {'advisory-state': 0, 'country-eng': 'Japan', 'date-published': {'date': '2026-10-01'}}}}).encode()
        site = b"""<script type="application/ld+json">{"@type":"Museum","name":"\xe6\x95\x85\xe5\xae\xae\xe5\x8d\x9a\xe7\x89\xa9\xe9\x99\xa2",
            "geo":{"latitude":25.10245,"longitude":121.54855},"openingHours":"Tu-Su 09:00-18:30"}</script><a href="/visit">v</a>"""
        web = Web({REG_URL('taiwan_tourism'): z.getvalue(), REG_URL('advisories_ca'): ca, 'https://www.npm.gov.tw/': site, 'https://www.npm.gov.tw/visit': b'<p>x</p>'})
        sh = str(C.shard_of('www.npm.gov.tw', C.CRAWL_SHARDS))
        store.put_json(f'seeds/sites_{sh}.json.gz', {'Q49133': {'url': 'https://www.npm.gov.tw/', 'nm': 'National Palace Museum', 'lat': 25.1024, 'lon': 121.5485, 'c': 'museum', 'cc': 'TW'}})
        for s, shd in (('taiwan_tourism', '0'), ('advisories_ca', '0'), ('official_sites', sh)):
            r = R.job(REG, store, s, shd, opener=web, sleep=lambda x: None)
            self.assertIsNone(r['err'], r); self.assertTrue(r['counts'], r)
        st, bad = R.summary(REG, store, os.path.join(d, 'status.json'), os.path.join(d, 'alert.md'))
        self.assertEqual(bad, []); self.assertEqual(st['sources']['taiwan_tourism']['ok_today'], 1)
        self.assertFalse(any('instagram' in u for u in web.calls))
        run, stats = P.process(store, world)
        tw = stats['countries']['TW']
        self.assertEqual(tw['places'], 2)  # 故宮: 대만 관광서 + 공식 사이트 → 하나로
        self.assertEqual(tw['matched_world'], 1)  # 太魯閣 은 앱 타일에 이미 있음 → wid
        self.assertEqual(tw['multi_src'], 1); self.assertEqual(tw['official'], 1)
        lat = store.get_json('processed/latest.json')
        j = next(j for j in (store.get_json(f"processed/out/{run}/{f}") for f in lat['files'] if f.startswith('col_TW_')) if 'o' in j['rk'])  # 층 파일은 0.5° 칸 단위(docs/SCALE.md)
        i = j['rk'].index('o')
        self.assertEqual((j['tr'][i], j['h'][i], j['hd'][i]), (2, 'Tu-Su 09:00-18:30', 1))  # 공식 시간이 이기고 '시간 다름'
        self.assertIn('col_info_advisory.json.gz', lat['files'])
        cur = os.path.join(d, 'cur'); os.makedirs(cur)
        tag, msg = P.publish(store, 'bucket', '', dry_run=True, tools=os.path.join(HERE, '..', 'tools'), current_dir=cur)
        self.assertTrue(tag and tag.startswith('c'), msg)
        # 다음 판에서 TW 장소 수가 줄면 관문이 막는다
        hist = store.get_json('published/history.json'); hist[-1]['counts']['TW'] = 100; store.put_json('published/history.json', hist)
        lat['run'] = lat['run'] + '9'; store.put_json('processed/latest.json', lat)
        for f in lat['files']:
            store.put(f"processed/out/{lat['run']}/{f}", store.get(f"processed/out/{run}/{f}"))
        tag2, errs = P.publish(store, 'bucket', '', dry_run=True, tools=os.path.join(HERE, '..', 'tools'), current_dir=cur)
        self.assertIsNone(tag2); self.assertTrue(any('-5%' in e for e in errs))

    def test_gate_catches_bad_rows(self):
        lay = P.to_layer('JP', [{'id': 'x:1', 'nm': 'Ã©cole', 'lat': 35, 'lon': 135, 'src': 'x', 'lic': 'CC0', 'url': 'https://tabelog.com/a', 'c': 'food'}])
        errs = P.gate({'col_JP_0.json.gz': lay}, {}, {'JP': 1})
        self.assertTrue(any('글자 깨짐' in e for e in errs)); self.assertTrue(any('금지 도메인' in e for e in errs))

    def test_fail_streak_alert(self):
        d = tempfile.mkdtemp(); store = K.Store(d)
        for _ in range(3):
            R.job(REG, store, 'advisories_ca', '0', opener=Web({'https://data.international.gc.ca/travel-voyage/index-updated.json': b'not json'}), sleep=lambda x: None)
        _, bad = R.summary(REG, store, os.path.join(d, 's.json'), os.path.join(d, 'alert.md'))
        self.assertEqual(len(bad), 1); self.assertTrue(os.path.exists(os.path.join(d, 'alert.md')))


def REG_URL(sid):
    return next(s for s in REG['sources'] if s['id'] == sid)['url']


class BadInput(unittest.TestCase):
    """11/17: 조각 전체를 죽이던 두 입력 — 빈칸이 든 영문 주소(official_sites), 열 이름 없는 칸(open_dataset)."""

    def test_ascii_url_with_space_is_encoded(self):
        self.assertEqual(K.iri_to_uri('https://a.example/SITUS BIKINISLOT/anime'), 'https://a.example/SITUS%20BIKINISLOT/anime')
        self.assertEqual(K.iri_to_uri('https://a.example/x.php?A PREFEITURA='), 'https://a.example/x.php?A%20PREFEITURA=')
        self.assertEqual(K.iri_to_uri('https://a.example/a%20b?q=1&r=2'), 'https://a.example/a%20b?q=1&r=2')  # 이미 인코딩된 것은 그대로
        self.assertEqual(K.iri_to_uri(' https://a.example/ok '), 'https://a.example/ok')

    def test_invalid_url_is_skipped_not_retried(self):
        import http.client
        calls = []

        def w(url, headers):
            calls.append(url)
            if url.endswith('/robots.txt'):
                raise urllib.error.HTTPError(url, 404, 'x', {}, None)
            raise http.client.InvalidURL("URL can't contain control characters.")
        h = K.Http(src(), {}, {}, opener=w, sleep=lambda s: None)
        with self.assertRaises(IOError):
            h.get('https://a.example/x')
        self.assertEqual([c for c in calls if not c.endswith('/robots.txt')], ['https://a.example/x'])

    def test_crawl_site_survives_link_with_space(self):
        import types
        root = 'https://s.example/'
        w = Web({root: b'<html><a href="/RDCweb/N Toon.html">a</a><a href="/ok">b</a></html>', 'https://s.example/ok': b'<html></html>',
                 'https://s.example/RDCweb/N%20Toon.html': b'<html></html>'})
        ctx = types.SimpleNamespace(http=K.Http(src(daily_limit=50), {}, {}, opener=w, sleep=lambda s: None))
        best, events = C.crawl_site(ctx, 'Q1', {'url': root})
        self.assertIn('https://s.example/RDCweb/N%20Toon.html', w.calls)
        self.assertIn('https://s.example/ok', w.calls)
        self.assertFalse(any(' ' in c for c in w.calls))

    def test_pick_ignores_unnamed_column(self):
        self.assertEqual(C._pick(['Name', None, 'lat'], C.COLS['nm']), 'Name')
        self.assertIsNone(C._pick([None], C.COLS['nm']))


if __name__ == '__main__':
    unittest.main()
