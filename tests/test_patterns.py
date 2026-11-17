"""일정 패턴 학습기 시험(네트워크·키 없음): 합성 설명문으로 파서 정확도, 출처 관문, 분포·덧대기, 산출물에 글·주소가 없는지.
  python3 -m unittest tests/test_patterns.py"""
import bz2, json, os, re, sys, tempfile, unittest, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts', 'collect'))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts', 'patterns'))
import common as K  # noqa: E402
import parse as P  # noqa: E402
import places as PL  # noqa: E402
import sources as S  # noqa: E402
import stats as T  # noqa: E402
import learn as R  # noqa: E402

# (언어, 설명문, 기대: 날마다 (시작, 끝, 장소 수, 점심, 저녁))
CASES = [
    ('ko', '도쿄 2박3일 가족여행\n1일차\n15:00 나리타 공항 도착\n17:00 호텔 체크인\n18:00 센소지\n19:30 저녁 스시\n2일차\n09:00 츠키지 시장\n10:30 팀랩\n12:30 점심 라멘\n2시 오다이바\n'
           '오후 4시 30분 시부야 스카이\n저녁 7시 이자카야\n3일차\n오전 9시 메이지 신궁\n11:00 하라주쿠\n12:00 점심\n14:00 공항 이동',
     [(1080, 1170, 2, None, 1170), (540, 1140, 6, 750, 1140), (540, 720, 3, 720, None)]),
    ('en', 'Kyoto solo trip itinerary\nDay 1\n8:30am Fushimi Inari\n11:00 AM Nishiki Market\n12:30pm Lunch at ramen shop\n2 PM Kiyomizu-dera\n4:30 PM Gion walk\n7pm Dinner\n'
           'Day 2\n09:00 - 10:30 Arashiyama bamboo grove\n11:00 Tenryu-ji\n13:00 lunch\n15:00 Kinkaku-ji\n18:30 dinner at izakaya',
     [(510, 1140, 6, 750, 1140), (540, 1110, 5, 780, 1110)]),
    ('ja', '大阪 カップル旅行 モデルコース\n1日目\n午前9時30分 大阪城\n11:30 黒門市場\n12:30 昼食\n午後2時 海遊館\n午後5時 道頓堀\n19:00 夕食 居酒屋\n2日目\n10:00 USJ\n13:00 ランチ\n18:00 ディナー',
     [(570, 1140, 6, 750, 1140), (600, 1080, 3, 780, 1080)]),
    ('zh-Hans', '上海亲子三日游\n第一天\n上午9点半 外滩\n11点 豫园\n中午12点 午餐\n下午2点 上海博物馆\n下午4点半 南京路\n晚上6点半 晚餐\n第二天\n09:00 迪士尼乐园\n12:30 午饭\n19:00 晚饭',
     [(570, 1110, 6, 720, 1110), (540, 1140, 3, 750, 1140)]),
    ('zh-Hant', '台北三天兩夜 閨蜜行程\n第1天\n上午10點 中正紀念堂\n中午12點 午餐 永康街\n下午2點 西門町\n下午5點 象山\n晚上7點 饒河夜市\n第2天\n9:00 九份老街\n12:00 午餐\n15:00 十分瀑布\n18:30 晚餐',
     [(600, 1140, 5, 720, None), (540, 1110, 4, 720, 1110)]),
]
TYPES = {'ko': 'family', 'en': 'solo', 'ja': 'couple', 'zh-Hans': 'family', 'zh-Hant': 'friends'}
CHAPTERS = '0:00 Intro\n0:45 Day 1\n1:10 Senso-ji\n3:20 Lunch at Asakusa\n5:00 Tokyo Skytree\n7:30 Day 2\n7:50 Meiji Shrine\n9:10 Harajuku cafe\n11:00 Shibuya Sky\n12:30 Outro'


class Web:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def __call__(self, url, headers):
        self.calls.append(url)
        v = self.pages.get(url)
        if v is None or isinstance(v, int):
            raise urllib.error.HTTPError(url, v or 404, 'x', {}, None)
        return 200, {}, v


class Parser(unittest.TestCase):
    def test_accuracy_5_langs(self):
        """합성 설명문 5개 언어 — 날마다 5칸(시작·끝·장소 수·점심·저녁). 정확도 95% 이상이어야 통과."""
        ok = tot = 0
        for lang, text, want in CASES:
            r = P.parse_text(text)
            self.assertIsNotNone(r, lang)
            self.assertEqual(r['type'], TYPES[lang], lang)
            self.assertEqual(len(r['days']), len(want), lang)
            for d, w in zip(r['days'], want):
                got = (d['start'], d['end'], d['stops'], d.get('lunch'), d.get('dinner'))
                for g, x in zip(got, w):
                    tot += 1; ok += g == x
                    if g != x:
                        print('다름', lang, d['idx'], got, w)
        print(f'파서 정확도 {ok}/{tot} = {ok / tot:.1%}')
        self.assertGreaterEqual(ok / tot, 0.95)

    def test_time_tokens(self):
        for s, m in (('9:30am Fushimi', 570), ('午前9時30分 清水寺', 570), ('上午9点半 故宫', 570), ('下午2點 西門町', 840), ('7 PM dinner', 1140), ('밤 11시 야시장', 1380),
                     ('中午12点 午餐', 720), ('오후 12시 30분 점심', 750), ('12:05 AM x', 5), ('- 09:00 경복궁', 540), ('⏰ 14：20 카페', 860)):
            self.assertEqual(P.find_time(s)[0], m, s)
        self.assertEqual(P.find_time('09:00~10:30 경복궁')[2], 630)
        self.assertIsNone(P.find_time('가격 3000원 정도'))
        for s, n in (('Day 2', 2), ('DAY3 교토', 3), ('2일차', 2), ('둘째 날', 2), ('3日目', 3), ('第二天', 2), ('第3天', 3)):
            self.assertEqual(P.day_no(s), n, s)

    def test_chapters_are_not_clock(self):
        """0:00 에서 시작하는 챕터는 재생 위치 — 시각 통계에 넣지 않고 장소 수·종류만."""
        r = P.parse_text(CHAPTERS)
        self.assertEqual([(d['clock'], d['stops']) for d in r['days']], [(False, 3), (False, 3)])
        self.assertNotIn('start', r['days'][0])
        self.assertIsNone(P.parse_text('0:00 Intro\n1:00 A\n2:00 B\n3:00 C'))  # 날 구분 없는 챕터는 세지 않음

    def test_rejects_non_itinerary(self):
        self.assertIsNone(P.parse_text('영업시간 09:00\n입장료 3000원\n문의 02-123-4567'))
        self.assertIsNone(P.parse_text('18:00 A\n09:00 B\n03:00 C\n12:00 D'))  # 시각이 거꾸로

    def test_output_has_no_text(self):
        for _, text, _ in CASES:
            s = json.dumps(P.parse_text(text), ensure_ascii=False)
            self.assertFalse(re.search(r'[가-힣぀-ヿ一-鿿]', s), s)

    def test_gazetteer_kind_only(self):
        g = PL.Gazetteer()
        g.add({'nm': 'teamLab Planets', 'ko': '팀랩 플래닛', 'c': 'museum'}); g.add({'nm': 'Ichiran', 'c': 'food'})
        self.assertEqual(P.kind_of('팀랩 플래닛', g), 'museum')
        self.assertEqual(P.kind_of('Late bite at Ichiran', g), 'food')
        self.assertEqual(P.kind_of('Lunch at Ichiran', g), 'lunch')
        self.assertEqual(PL.Cities().find('3 days in Kyoto & Osaka', 'Kyoto Kyoto Osaka'), ('교토', 'JP'))
        self.assertEqual(PL.Cities().find('우리 동네 산책'), (None, None))


class Gate(unittest.TestCase):
    def test_blocked_sources_have_no_fetcher(self):
        srcs = {s['id']: s for s in S.load_sources()}
        for i in ('youtube_data_api', 'naver_search_api', 'kakao_search_api'):
            self.assertFalse(S.usable(srcs[i]), i)
            self.assertNotIn(srcs[i]['kind'], S.FETCHERS)
            self.assertFalse(S.usable(dict(srcs[i], status='active', verdict='ok')), i)  # 등록부만 고쳐서는 켜지지 않는다
        self.assertTrue(S.usable(srcs['wikivoyage_itineraries']))
        code = ''.join(open(os.path.join(HERE, '..', 'scripts', 'patterns', f), encoding='utf-8').read() for f in ('sources.py', 'learn.py', 'parse.py', 'stats.py', 'places.py'))
        for bad in ('googleapis.com', 'youtube.com', 'youtu.be', 'openapi.naver', 'dapi.kakao', 'yt-dlp', 'youtube_transcript', 'timedtext'):
            self.assertNotIn(bad, code)

    def test_site_needs_terms_check_and_robots(self):
        base = {'id': 's', 'kind': 'site', 'verdict': 'conditional', 'status': 'active', 'seeds': ['https://trip.example/courses'], 'path_re': '/course', 'max_pages': 5}
        self.assertFalse(S.usable(base))  # 약관 확인 날짜 없음
        self.assertFalse(S.usable(dict(base, terms_checked='2026-11-17', terms_url='https://x', seeds=['https://blog.naver.com/a'])))
        src = dict(base, terms_checked='2026-11-17', terms_url='https://trip.example/terms')
        page = ('<html><head><title>Tokyo family course</title></head><body><a href="/course/2">n</a><ul>' + ''.join(f'<li>{x}</li>' for x in
                ('09:00 Senso-ji', '11:00 Ueno Park', '12:30 Lunch', '15:00 Skytree', '18:30 Dinner')) + '</ul></body></html>').encode()
        noai = page.replace(b'<head>', b'<head><meta name="robots" content="noai">')
        web = Web({'https://trip.example/courses': page, 'https://trip.example/course/2': noai})
        got = S.site(src, K.Http(src, {}, {}, opener=web, sleep=lambda s: None), None, PL.Cities(), {})
        self.assertEqual(len(got), 1)  # noai 쪽은 읽지 않음
        self.assertEqual((got[0]['city'], got[0]['type'], got[0]['days'][0]['stops']), ('도쿄', 'family', 5))
        web2 = Web({'https://trip.example/robots.txt': b'User-agent: *\nDisallow: /', 'https://trip.example/courses': page})
        self.assertEqual(S.site(src, K.Http(src, {}, {}, opener=web2, sleep=lambda s: None), None, PL.Cities(), {}), [])
        self.assertNotIn('https://trip.example/courses', web2.calls)
        web3 = Web({'https://trip.example/.well-known/tdmrep.json': b'[{"location":"/","tdm-reservation":1}]', 'https://trip.example/courses': page})
        with self.assertRaises(K.Blocked):
            S.site(src, K.Http(src, {}, {}, opener=web3, sleep=lambda s: None), None, PL.Cities(), {})


WV = """<mediawiki><page><title>Three days in Kyoto</title><revision><text>Intro.
== Day 1 ==
* {{see|name=Kinkaku-ji|lat=35.03|long=135.72}} {{see|name=Ryoan-ji}} {{eat|name=Udon shop}} {{sleep|name=Inn}}
== Day 2 ==
* 9:00 Fushimi Inari
* 12:00 lunch
* 14:00 Kiyomizu-dera
* 18:00 dinner
== Day 3 ==
{{do|name=Arashiyama}} {{buy|name=Nishiki}}
== Stay safe ==
</text></revision></page><page><title>Kyoto</title><revision><text>== See ==
{{see|name=X}}</text></revision></page></mediawiki>"""


class Flow(unittest.TestCase):
    def test_wikivoyage_to_patterns(self):
        got = S.wv_parse(__import__('io').BytesIO(WV.encode()), None, PL.Cities())
        self.assertEqual(len(got), 1)
        s = got[0]
        self.assertEqual((s['city'], s['cc']), ('교토', 'JP'))
        self.assertEqual([(d['idx'], d['clock'], d['stops']) for d in s['days']], [(1, False, 3), (2, True, 4), (3, False, 2)])

    def test_run_end_to_end_no_text_stored(self):
        with tempfile.TemporaryDirectory() as d:
            store = K.Store(d)
            base = 'https://dumps.wikimedia.org/enwikivoyage/latest/enwikivoyage-latest-'
            web = Web({base + 'sha1sums.txt': b'v1', base + 'pages-articles.xml.bz2': bz2.compress(WV.encode())})
            srcs = [dict(s, langs=['en']) for s in S.load_sources()]
            logs = []
            got = R.collect(store, srcs, None, PL.Cities(), opener=web, sleep=lambda s: None, log=logs.append)
            self.assertEqual(list(got), ['wikivoyage_itineraries'])
            self.assertFalse([u for u in web.calls if K.forbidden(u) or 'googleapis' in u])
            outs = R.build(store, srcs, got, min_n=1, log=logs.append)
            self.assertEqual(outs['clean'][0]['groups']['city'], {})  # 동일조건 자료는 clean 쪽에 섞이지 않는다
            self.assertEqual(outs['by-sa'][0]['groups']['city']['교토']['all']['n_days'], 3)
            n = len(web.calls)
            R.collect(store, srcs, None, PL.Cities(), opener=web, sleep=lambda s: None, log=logs.append)  # 덤프 그대로 → 다시 받지 않음
            self.assertEqual([u for u in web.calls[n:] if u.endswith('.bz2')], [])
            blob = ''
            for k in store.list(''):
                blob += json.dumps(store.get_json(k), ensure_ascii=False)
            for bad in ('Kinkaku', 'Fushimi', 'Three days', 'Udon', 'wikivoyage.org/wiki', 'watch?v='):
                self.assertNotIn(bad, blob)
            self.assertIn('CC BY-SA', blob)  # 출처 표기는 남긴다

    def test_aggregate_and_patch_keep_small(self):
        day = lambda st, n: {'clock': True, 'full': True, 'start': st, 'end': st + 540, 'stops': n, 'gaps': [90], 'stays': [], 'cats': {'sight': n}, 'lunch': 750, 'idx': 1, 'of': 1}
        samples = [{'city': '도쿄', 'cc': 'JP', 'type': 'family', 'days': [day(540 + 10 * i, 4 + i % 3)]} for i in range(10)]
        samples += [{'city': '교토', 'cc': 'JP', 'type': 'any', 'days': [day(600, 5)]} for _ in range(3)]
        pat = T.aggregate(samples, 'clean', [], {})
        tk = pat['groups']['city']['도쿄']['all']
        self.assertEqual((tk['day_start']['median'], tk['day_start']['n'], tk['stops_per_day']['median']), ('09:45', 10, 5))
        self.assertIn('family', pat['groups']['city']['도쿄'])
        patch = T.to_model_patch(pat, 8)
        self.assertIn('도쿄', patch['cities']); self.assertNotIn('교토', patch['cities'])  # 표본 3개 도시는 덧대지 않음
        model = {'cities': {'도쿄': {'generator_rules': {'day_start': {'p25': '08:00', 'median': '09:00', 'p75': '10:00', 'n': 30, 'src': 'city'},
                                                         'stops_per_day': {'relaxed': 4, 'normal': 4, 'packed': 6, 'n': 5, 'src': 'region_JP'}}, 'top_places': [1]},
                            '교토': {'generator_rules': {'day_start': {'median': '09:30', 'n': 4, 'src': 'city_d6'}}}}, 'regions': {}, 'global': {}}
        ch = T.apply_patch(model, patch, 8)
        gr = model['cities']['도쿄']['generator_rules']
        self.assertEqual(gr['day_start']['n'], 30)  # 기존 표본이 더 많으면 유지
        self.assertEqual((gr['stops_per_day']['src'], gr['stops_per_day']['normal']), ('patterns', 5))  # 지역 값 빌려 쓰던 칸은 교체
        self.assertEqual(model['cities']['도쿄']['top_places'], [1])
        self.assertEqual(model['cities']['교토']['generator_rules']['day_start']['n'], 4)
        self.assertIn('cities/도쿄/generator_rules/stops_per_day', ch)
        r = T._stops_rule({'p25': 2, 'median': 6, 'p75': 12, 'n': 9})
        self.assertEqual((r['relaxed'], r['normal'], r['packed']), (3, 6, 8))


if __name__ == '__main__':
    unittest.main()
