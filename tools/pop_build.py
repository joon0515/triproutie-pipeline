#!/usr/bin/env python3
"""인기 점수·사진 층 만들기 — 위키백과 조회수(Wikimedia Pageviews) + Wikidata.  (2026-11-13, 서버에서 월 1회 일괄 실행용)

구글 평점·리뷰 수를 쓸 수 없으므로(저장 금지), 저장·표시가 자유로운 공개 신호로 "유명한 정도"를 만든다.
  ① 팩 행 → 한국어 위키백과 문서 제목  (팩의 kos 2·8·9·13 = 한글 이름이 곧 위키 제목. 한국 팩은 명소·자연 행의 이름으로 찾고 좌표로 검증)
  ② Wikidata wbgetentities(제목 50개씩) → Q-id · 언어판 수 · 좌표(P625) · 대표 사진(P18)
  ③ 한국어 위키백과 조회수(Action API prop=pageviews, 제목 50개씩, 최근 60일) → 월평균 조회수
  ④ 인기 점수 pop(0~100) = 100 × (0.7 × log(1+조회수)/log(1+20000) + 0.3 × log(1+언어판 수)/log(1+150)), 각 항은 1 에서 자름
  ⑤ 팩 점수와 합치기 s2 = 76 + round(0.24 × pop) (pop 10 미만이면 팩 점수 그대로)   → 층 파일의 rows[id].s  (앱: tlPfInstallPopularity)
  ⑥ 사진(선택 --photos N): P18 → Commons imageinfo(extmetadata) → 저작자·라이선스. CC0·퍼블릭 도메인·CC BY·CC BY-SA 만 싣는다.

    TL_WIKI_UA='TripRoutiePopularityBot/1.0 (https://앱주소; 연락처)' python3 pop_build.py out.json.gz packs/a01.json.gz [packs/a02.json.gz …] [--photos 60] [--only-sights] [--min-s 55]

지킬 것: 식별 가능한 User-Agent(연락처 포함 — 운영 때는 TL_WIKI_UA 로 반드시 실제 연락처를 넣는다), 한 번에 한 요청, 요청 사이 쉬기(TL_WIKI_SLEEP 초,
기본 1.2 — 429 를 받으면 Retry-After 만큼 쉬고 간격을 두 배로 늘린다, 최대 6초), 5xx 면 물러났다 다시, 다섯 번 실패하면 멈추고 받은 데까지만 저장. 받은 응답은 cache/ 에 두어 다시 돌릴 때 같은 요청을 보내지 않는다.
조회수·Wikidata 는 CC0 이다. 사진은 파일마다 라이선스가 달라 저작자·라이선스를 함께 싣는다(표기 형식: "사진: 저작자 · 라이선스").
"""
import gzip, hashlib, html, json, math, os, re, sys, time, urllib.parse, urllib.request, urllib.error

UA = os.environ.get('TL_WIKI_UA', 'TripRoutiePopularityBot/0.1 (travel app place popularity batch; monthly; contact via app site)')
SLEEP = float(os.environ.get('TL_WIKI_SLEEP', '1.2'))  # 실측(11/13): 연락처 없는 User-Agent 로 약 20회/10초를 보내자 429(retry-after 29초)가 왔다 → 느리게
PACE = {'s': SLEEP}
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.environ.get('POP_CACHE') or os.path.join(HERE, 'cache')  # POP_CACHE: 캐시 폴더를 바꿀 때(GitHub Actions)
PV_REF, SL_REF = 20000, 150
OK_LICENSE = re.compile(r'^(cc0|cc[ -]by(-sa)?([ -]\d\.\d)?( [a-z-]+)?|public domain|pd\b.*|no restrictions)$', re.I)
STATS = {'net': 0, 'cache': 0, 'fail': 0}


# 장소가 아닌 문서를 거르는 규칙(교토 검증에서 나온 잡음): 행정구역 문서(교토시·교토부)가 시청·동네 행에, 사건 문서(혼노지의 변)가 근처 행에 붙는다.
NOT_PLACE_P31 = {'Q515', 'Q1549591', 'Q494721', 'Q50337', 'Q1059478', 'Q4174776', 'Q1749269', 'Q17221353', 'Q5327704', 'Q15284', 'Q486972', 'Q3957',
                 'Q7930989', 'Q532', 'Q6256', 'Q107390', 'Q35657', 'Q178561', 'Q168983', 'Q3839081', 'Q1190554', 'Q645883', 'Q124757', 'Q831663',
                 'Q3918', 'Q902104', 'Q875538', 'Q3551775', 'Q1244442', 'Q43229', 'Q4830453', 'Q891723', 'Q9174', 'Q13414953', 'Q34651'}
# '명소' 인기에서 빼는 것(11/13 2차 — 오사카·부산 검증에서 2위가 경기장, 7위가 지하철역, 9위가 아파트였다): 장소이긴 하지만 관광지는 아니다.
NOT_SIGHT_P31 = {'Q483110', 'Q595452', 'Q1076486', 'Q1154710', 'Q641226', 'Q7579839',  # 경기장·야구장·체육 시설·축구장·아레나
                 'Q55488', 'Q928830', 'Q22808404', 'Q1339195', 'Q55485', 'Q4663385', 'Q18543139', 'Q14562709',  # 역(철도·지하철·환승)
                 'Q11303', 'Q13402009', 'Q1021645', 'Q41176', 'Q12104567', 'Q18142',  # 초고층·아파트·사무용 건물·일반 건물
                 'Q25550691', 'Q543654', 'Q7843791', 'Q3917681', 'Q2360219', 'Q327333',  # 시청·청사·영사관·대사관·관공서
                 'Q742421', 'Q2416217', 'Q172754', 'Q288514', 'Q132241', 'Q27968055', 'Q15275719',  # 극단·박람회·축제·행사
                 'Q16917', 'Q4287745', 'Q1248784', 'Q44782', 'Q644371'}  # 병원·공항·항만(교통 시설)
NOT_SIGHT_TITLE = re.compile(r'(역|경기장|구장|스타디움|야구장|운동장|체육관|시청|구청|도청|청사|영사관|대사관|가극단|박람회|엑스포|아파트|더제니스|타워맨션|병원|항)( \(.*\))?$')
# 팩 행의 유형이 이것이면 '명소' 인기 대상이 아니다(유형 이름은 팩의 tps 표).
NOT_SIGHT_TYPES = re.compile(r'stadium|arena|_station$|^platform$|town_hall|embassy|music_venue|night_club|casino|neighborhood|^city$|^town$|event_venue|exhibition_and_trade_fair_venue|library|^bridge$')
NOT_PLACE_TITLE = re.compile(r'(시|부|현|구|군|촌|읍|면)$|(사건|의 변|전투|의 난|참사|사고|전쟁|조약|학살|대학|대학교|교회|교단|협회)( \(.*\))?$')


def is_place(title, p31):
    """장소 문서인가(행정구역·사건·대학·단체 문서는 아니다)."""
    return not (set(p31) & NOT_PLACE_P31) and not NOT_PLACE_TITLE.search(title)


def is_sight(title, p31, type_name=''):
    """관광지로 볼 만한 장소 문서인가(경기장·역·관공서·일반 건물·행사 문서, 그런 유형의 행은 아니다)."""
    return is_place(title, p31) and not (set(p31) & NOT_SIGHT_P31) and not NOT_SIGHT_TITLE.search(title) and not NOT_SIGHT_TYPES.search(type_name or '')


class Stop(Exception):
    pass


def get_json(url):
    """GET → JSON. 디스크 캐시. 404 는 None. 429·5xx 는 물러났다 다시(최대 3회) — 그래도 안 되면 Stop."""
    os.makedirs(CACHE, exist_ok=True)
    fn = os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest() + '.json')
    if os.path.exists(fn):
        STATS['cache'] += 1
        return json.load(open(fn))
    for attempt in range(5):
        time.sleep(PACE['s'])
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read().decode('utf-8'))
            STATS['net'] += 1
            json.dump(d, open(fn, 'w'))
            return d
        except urllib.error.HTTPError as e:
            if e.code == 404:
                STATS['net'] += 1
                json.dump(None, open(fn, 'w'))
                return None
            if e.code == 429:
                try:
                    wait = int(e.headers.get('Retry-After') or 30)
                except ValueError:
                    wait = 30
                STATS['429'] = STATS.get('429', 0) + 1
                PACE['s'] = min(6.0, PACE['s'] * 2)
                time.sleep(min(300, wait) + 5)
                continue
            if e.code in (500, 502, 503, 504):
                time.sleep(20 * (attempt + 1))
                continue
            STATS['fail'] += 1
            return None
        except Exception:
            time.sleep(5 * (attempt + 1))
    raise Stop(url.split('?')[0])


def km(a, b, c, d):
    r = math.pi / 180
    h = math.sin((c - a) * r / 2) ** 2 + math.cos(a * r) * math.cos(c * r) * math.sin((d - b) * r / 2) ** 2
    return 12742 * math.asin(min(1, math.sqrt(h)))


def pop_score(pv, sitelinks):
    """월평균 조회수 + 언어판 수 → 0~100."""
    a = min(1.0, math.log1p(max(0, pv)) / math.log1p(PV_REF))
    b = min(1.0, math.log1p(max(0, sitelinks)) / math.log1p(SL_REF))
    return round(100 * (0.7 * a + 0.3 * b))


WIKI_BASE = 76  # 위키 문서가 없는 명소의 팩 점수 최댓값은 75 다(오사카·교토·부산 실측) → 위키 연결 행은 그 위에서 인기순으로 줄 선다


def merge_score(s, pop):
    """팩 점수 s(0~100)와 인기 점수 → 최종 정렬 점수.
    pop 10 미만(거의 아무도 안 찾아보는 문서)은 팩 점수를 그대로 둔다. 그 밖은 76 + 0.24 × pop (76~100).
    (처음에는 0.5×s + 0.5×pop 로 했으나, 교토 검증에서 기요미즈데라가 87 → 80 으로 내려가 이름 없는 절(75) 과 섞였다 — 평균은 유명한 곳을 끌어내린다.)"""
    if pop < 10:
        return max(0, min(100, s))
    return min(100, WIKI_BASE + round(0.24 * pop))


def months():
    """최근 12개의 꽉 찬 달: (시작 YYYYMM0100, 끝 YYYYMMDD00)."""
    t = time.gmtime()
    y, m = t.tm_year, t.tm_mon
    ey, em = (y, m - 1) if m > 1 else (y - 1, 12)
    sy, sm = (ey - 1, em + 1) if em < 12 else (ey, 1)
    last = [31, 29 if ey % 4 == 0 and (ey % 100 != 0 or ey % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][em - 1]
    return f'{sy:04d}{sm:02d}0100', f'{ey:04d}{em:02d}{last:02d}00'


def load_pack(fn):
    d = json.load(gzip.open(fn))
    la = lo = 0
    lat, lon = [], []
    for a, b in zip(d['dla'], d['dlo']):
        la += a
        lo += b
        lat.append(la / 1e5)
        lon.append(lo / 1e5)
    d['_lat'], d['_lon'] = lat, lon
    return d


HANGUL = re.compile(r'[가-힣]')


def candidates(d, only_sights=False, min_s=0):
    """(행 번호, 위키 제목, 좌표 검증 한도 km, 좌표 없어도 믿는가)."""
    out = []
    cats = d.get('cats', [])
    sightish = {i for i, c in enumerate(cats) if c in ('sight', 'nature')}
    kr = d.get('cc') == 'KR'
    for i in range(d['n']):
        kos, ko, nm = d['kos'][i], d['ko'][i], d['nm'][i]
        if d['s'][i] < min_s or (only_sights and d['c'][i] not in sightish):
            continue
        if kos in (2, 8, 9, 13) and ko:
            out.append((i, ko, 3.0, True))
        elif kr and d['c'][i] in sightish and nm and len(nm) >= 3 and HANGUL.search(nm) and d['s'][i] >= 55:
            out.append((i, nm, 1.5, False))
    return out


def wikidata(titles):
    """제목들 → {제목: {q, sl, lat, lon, p18}}"""
    out = {}
    uniq = sorted(set(titles))
    for k in range(0, len(uniq), 50):
        part = uniq[k:k + 50]
        url = 'https://www.wikidata.org/w/api.php?' + urllib.parse.urlencode(
            {'action': 'wbgetentities', 'sites': 'kowiki', 'titles': '|'.join(part), 'props': 'sitelinks|claims', 'format': 'json'})
        j = get_json(url) or {}
        for q, e in (j.get('entities') or {}).items():
            if not q.startswith('Q'):
                continue
            sl = e.get('sitelinks') or {}
            t = (sl.get('kowiki') or {}).get('title')
            if not t:
                continue
            cl = e.get('claims') or {}

            def val(p):
                try:
                    return cl[p][0]['mainsnak']['datavalue']['value']
                except Exception:
                    return None
            c = val('P625') or {}
            n_sl = sum(1 for s in sl if s.endswith('wiki') and s not in ('commonswiki', 'specieswiki', 'metawiki', 'wikidatawiki', 'sourceswiki'))
            p31 = []
            for st in cl.get('P31') or []:
                try:
                    p31.append(st['mainsnak']['datavalue']['value']['id'])
                except Exception:
                    pass
            out[t] = {'q': q, 'sl': n_sl, 'lat': c.get('latitude'), 'lon': c.get('longitude'), 'p18': val('P18') if isinstance(val('P18'), str) else None, 'p31': p31}
    return out


def pageviews_batch(titles, days=60):
    """제목들 → {제목: 월평균 조회수}. 한국어 위키백과 Action API 의 prop=pageviews(한 번에 50개 제목, 최근 60일의 일별 조회수)를 쓴다.
    문서마다 한 번씩 부르는 REST(per-article)보다 요청 수가 50분의 1 이다 — 실측(11/13)에서 per-article 은 약 20회 만에 429 가 왔다.
    월평균 = 60일 합계 ÷ 2."""
    out = {}
    known = _load_side('pv_titles.json')  # 제목별 결과를 따로 적어 둔다 → 다음에 제목 묶음이 달라져도 다시 묻지 않는다
    for t, v in _harvest_pv(days).items():  # 예전에 묶음으로 받아 둔 응답에서도 모은다
        known.setdefault(t, v)
    todo = sorted({t for t in titles if t.replace('_', ' ') not in known})
    for t in titles:
        if t.replace('_', ' ') in known:
            out[t] = known[t.replace('_', ' ')]
    uniq = todo
    for k in range(0, len(uniq), 50):
        part = uniq[k:k + 50]
        cont = {}
        sums = {}
        for _ in range(8):  # 이어 받기(pvipcontinue)
            q = {'action': 'query', 'prop': 'pageviews', 'pvipdays': days, 'titles': '|'.join(part), 'format': 'json', 'formatversion': 2}
            q.update(cont)
            j = get_json('https://ko.wikipedia.org/w/api.php?' + urllib.parse.urlencode(q)) or {}
            qq = j.get('query') or {}
            norm = {n['to']: n['from'] for n in qq.get('normalized') or []}
            for pg in qq.get('pages') or []:
                t = norm.get(pg.get('title', ''), pg.get('title', ''))
                pv = pg.get('pageviews') or {}
                sums[t] = sums.get(t, 0) + sum(v for v in pv.values() if isinstance(v, int))
            cont = j.get('continue') or {}
            if not cont:
                break
        for t in part:
            out[t] = round(sums.get(t, 0) * 30 / days)
            known[t.replace('_', ' ')] = out[t]
        _save_side('pv_titles.json', known)
    return out


def _load_side(name):
    fn = os.path.join(CACHE, name)
    try:
        return json.load(open(fn))
    except Exception:
        return {}


def _save_side(name, d):
    os.makedirs(CACHE, exist_ok=True)
    json.dump(d, open(os.path.join(CACHE, name), 'w'), ensure_ascii=False)


def _harvest_pv(days):
    """이미 받아 둔 응답(cache/*.json)에서 제목별 조회수를 모은다(같은 묶음의 이어 받기 응답은 더한다)."""
    sums = {}
    if not os.path.isdir(CACHE):
        return {}
    for f in os.listdir(CACHE):
        if len(f) != 45:
            continue
        try:
            j = json.load(open(os.path.join(CACHE, f)))
        except Exception:
            continue
        for pg in ((j or {}).get('query') or {}).get('pages') or [] if isinstance(j, dict) and isinstance(((j or {}).get('query') or {}).get('pages'), list) else []:
            pv = pg.get('pageviews')
            if isinstance(pv, dict):
                sums[pg.get('title', '')] = sums.get(pg.get('title', ''), 0) + sum(v for v in pv.values() if isinstance(v, int))
    return {t: round(v * 30 / days) for t, v in sums.items() if t}


def pageviews(title, rng):
    """한 문서의 월평균 조회수(REST per-article, 최근 12개월). 요청이 문서마다 1회라 일괄 작업에는 쓰지 않는다 — 소수 문서 확인용."""
    url = ('https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/ko.wikipedia/all-access/user/'
           + urllib.parse.quote(title.replace(' ', '_'), safe='') + f'/monthly/{rng[0]}/{rng[1]}')
    j = get_json(url)
    items = (j or {}).get('items') or []
    return round(sum(x.get('views', 0) for x in items) / 12)


TAG = re.compile(r'<[^>]+>')


def clean_artist(s):
    s = html.unescape(TAG.sub('', s or '')).strip()
    s = re.sub(r'\s+', ' ', s)
    return s[:60]


def photo_credit(artist, license_short):
    """표기 형식(기존 원칙): 사진: 저작자 · 라이선스"""
    return f"사진: {artist or 'Wikimedia Commons'} · {license_short}"


def photos(files, width=640):
    """Commons 파일 이름들 → {파일: {thumb, credit, artist, license, license_url, page}} (쓸 수 있는 라이선스만)"""
    out = {}
    known = _load_side('photo_files.json')  # 파일별 결과(못 쓰는 라이선스는 None)
    for f in files:
        if f in known and known[f]:
            out[f] = known[f]
    uniq = sorted({f for f in files if f not in known})
    for k in range(0, len(uniq), 50):
        part = uniq[k:k + 50]
        url = 'https://commons.wikimedia.org/w/api.php?' + urllib.parse.urlencode(
            {'action': 'query', 'titles': '|'.join('File:' + f for f in part), 'prop': 'imageinfo', 'iiprop': 'url|extmetadata', 'iiurlwidth': width, 'format': 'json'})
        j = get_json(url) or {}
        q = j.get('query') or {}
        norm = {n['to']: n['from'] for n in q.get('normalized') or []}
        for p in (q.get('pages') or {}).values():
            ii = (p.get('imageinfo') or [None])[0]
            if not ii:
                continue
            em = ii.get('extmetadata') or {}
            lic = (em.get('LicenseShortName') or {}).get('value', '').strip()
            if not OK_LICENSE.match(lic):
                continue
            artist = clean_artist((em.get('Artist') or {}).get('value', ''))
            title = p.get('title', '')
            orig = norm.get(title, title)[5:]
            rec = {'thumb': (ii.get('thumburl') or '').split('?')[0], 'credit': photo_credit(artist, lic), 'artist': artist, 'license': lic,
                   'license_url': (em.get('LicenseUrl') or {}).get('value', ''), 'page': ii.get('descriptionurl', '')}
            if rec['thumb']:
                out[orig] = rec
                out[orig.replace('_', ' ')] = rec
        for f in part:
            known[f] = out.get(f) or out.get(f.replace('_', ' '))
        _save_side('photo_files.json', known)
    return out


def build(pack_files, n_photos=0, only_sights=False, min_s=0):
    rng = months()
    rows, report = {}, {}
    for fn in pack_files:
        d = load_pack(fn)
        cand = candidates(d, only_sights, min_s)
        wd = wikidata([t for _, t, _, _ in cand])
        got = []
        for i, t, lim, trust in cand:
            w = wd.get(t)
            if not w:
                continue
            # 좌표(P625)가 없는 항목은 받지 않는다: 교단·브랜드·일반 명사 문서가 지점 행에 붙는 것을 막는다
            # (교토 검증에서 '예수 그리스도 후기 성도 교회' 문서가 동네 교회 세 곳에 붙어 2~4위로 올라왔다).
            if w['lat'] is None:
                continue
            dist = km(d['_lat'][i], d['_lon'][i], w['lat'], w['lon'])
            tp = d['tps'][d['t'][i]] if 0 <= d['t'][i] < len(d['tps']) else ''
            if dist > lim or not (is_sight(t, w.get('p31') or [], tp) if only_sights else is_place(t, w.get('p31') or [])):
                continue
            got.append((i, t, w, dist))
        # 한 문서는 한 행에만: 같은 제목이 여러 행에 맞으면 Wikidata 좌표에 가장 가까운 행만 남긴다(나머지는 팩 점수 그대로).
        nearest = {}
        for g in got:
            if g[1] not in nearest or g[3] < nearest[g[1]][3]:
                nearest[g[1]] = g
        got = [(i, t, w) for i, t, w, _ in nearest.values()]
        pv = pageviews_batch([t for _, t, _ in got])
        best = {}
        for i, t, w in got:
            pop = pop_score(pv[t], w['sl'])
            rid = d['id'][i]
            rows[rid] = {'q': w['q'], 't': t, 'pv': pv[t], 'sl': w['sl'], 'pop': pop, 's': merge_score(d['s'][i], pop)}
            best[rid] = (i, w)
        if n_photos:
            top = sorted(best, key=lambda r: -rows[r]['pop'])[:n_photos]
            ph = photos([best[r][1]['p18'] for r in top if best[r][1]['p18']])
            for r in top:
                f = best[r][1]['p18']
                if f and f in ph:
                    rows[r]['photo'] = ph[f]
        report[os.path.basename(fn)] = {'city': d.get('city_ko'), 'rows': d['n'], 'candidates': len(cand), 'matched': len(got), 'titles': len(pv),
                                        'with_photo': sum(1 for r in best if 'photo' in rows[r])}
        print(os.path.basename(fn), report[os.path.basename(fn)], STATS, flush=True)
    return {'schema': 'popularity-1', 'built': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'months': list(rng),
            'window_days': 60,
            'attribution': '인기 점수: 위키백과 조회수(Wikimedia, CC0) · Wikidata(CC0). 사진: 위키미디어 공용 — 사진마다 저작자·라이선스 표기',
            'formula': 'pop = 100*(0.7*log(1+pv)/log(1+20000) + 0.3*log(1+sitelinks)/log(1+150)); s = 76 + round(0.24*pop) if pop >= 10 else pack_s',
            'report': report, 'rows': rows}


def main(argv):
    args = [a for a in argv[1:] if not a.startswith('--')]
    def opt(name, default):
        if name not in argv:
            return default
        v = argv[argv.index(name) + 1]
        args.remove(v)
        return int(v)
    n_photos, min_s = opt('--photos', 0), opt('--min-s', 0)
    only_sights = '--only-sights' in argv  # 명소·자연 행만(검증·시범용 — 역·행정구역 문서를 빼 요청 수를 줄인다)
    if len(args) < 2:
        print(__doc__)
        return 2
    try:
        doc = build(args[1:], n_photos, only_sights, min_s)
    except Stop as e:
        print('멈춤(요청 실패가 이어짐):', e, STATS)
        return 1
    with gzip.open(args[0], 'wt', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, separators=(',', ':'))
    print('rows', len(doc['rows']), STATS, os.path.getsize(args[0]), 'bytes')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
