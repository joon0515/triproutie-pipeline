"""출처별 수집기. 모두 같은 모양: fn(ctx) — ctx.http(예절 HTTP)·ctx.st(이 조각 상태)·ctx.emit(dataset, rows)·ctx.store·ctx.shard·ctx.src.
결과 원본은 ctx.emit → Storage raw/<source>/<날짜>/ 에만. 저장소(GitHub)에는 상태 요약만 남는다."""
import bz2, csv, datetime, gzip, hashlib, html.parser, io, json, os, re, shutil, subprocess, sys, tarfile, tempfile, time, urllib.parse, zipfile
import xml.etree.ElementTree as ET

from common import LANGS, Stop, Blocked, city_rank, forbidden, host_of, license_open, place, refresh_days, region_rank, today, valid_coord

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, '..', '..', 'tools')


def shard_of(key, n):
    return int(hashlib.sha1(key.encode()).hexdigest(), 16) % int(n)


def _due(st, key, days):
    d = st.get(key)
    return not d or (datetime.date.fromisoformat(today()) - datetime.date.fromisoformat(d)).days >= days


# ---------- Wikivoyage ----------
LISTING = re.compile(r'\{\{\s*(see|do|eat|drink|sleep|buy|go|listing|marker|vcard)\s*\|', re.I)


def _template_params(text, start):
    """'{{see|' 다음부터 짝 맞는 '}}' 까지의 매개변수 dict(중첩 틀·링크 안의 | 는 무시)."""
    depth, i, cur, parts = 1, start, [], []
    while i < len(text) and depth:
        two = text[i:i + 2]
        if two in ('{{', '[['):
            depth += 1; cur.append(two); i += 2; continue
        if two in ('}}', ']]'):
            depth -= 1
            if depth == 0:
                break
            cur.append(two); i += 2; continue
        ch = text[i]
        if ch == '|' and depth == 1:
            parts.append(''.join(cur)); cur = []
        else:
            cur.append(ch)
        i += 1
    parts.append(''.join(cur))
    out = {}
    for p in parts:
        if '=' in p:
            k, v = p.split('=', 1)
            out[k.strip().lower()] = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]*)\]\]', r'\1', v.strip())
    return out, i


def parse_wikivoyage(xml_stream, src, lang, emit_rows):
    """덤프 XML(bz2 푼 흐름) → listing 장소. 본문 설명(content·description)은 버린다."""
    rows, title = [], None
    for ev, el in ET.iterparse(xml_stream, events=('end',)):
        tag = el.tag.rsplit('}', 1)[-1]
        if tag == 'title':
            title = el.text
        elif tag == 'text' and el.text and title and ':' not in title:
            t = el.text
            for m in LISTING.finditer(t):
                p, _ = _template_params(t, m.end())
                kind = m.group(1).lower()
                if kind in ('listing', 'vcard', 'marker'):
                    kind = p.get('type', 'other')
                lat, lon = p.get('lat'), p.get('long') or p.get('lon')
                r = place(src, f"{lang}:{title}:{p.get('name', '')}"[:200], p.get('name'), lat, lon, cat=kind,
                          names={'en': p.get('name')} if lang == 'en' else ({'ja': p.get('name')} if lang == 'ja' else ({'zh': p.get('name')} if lang == 'zh' else None)),
                          hours=p.get('hours'), url=p.get('url'), addr=p.get('address'), price=p.get('price'), wd=p.get('wikidata'),
                          extra={'city': title[:80], 'ref': f'https://{lang}.wikivoyage.org/wiki/{urllib.parse.quote(title.replace(" ", "_"))}'})
                if r:
                    rows.append(r)
            if len(rows) >= 20000:
                emit_rows(rows); rows = []
        if tag == 'page':
            el.clear()
    if rows:
        emit_rows(rows)


def wikivoyage(ctx):
    lang = ctx.shard
    url = f'https://dumps.wikimedia.org/{lang}wikivoyage/latest/{lang}wikivoyage-latest-pages-articles.xml.bz2'
    status = ctx.http.get(f'https://dumps.wikimedia.org/{lang}wikivoyage/latest/{lang}wikivoyage-latest-sha1sums.txt')
    ver = hashlib.sha1(status).hexdigest()[:12]  # 덤프가 바뀌었을 때만 다시 받는다
    if ctx.st.get('ver') == ver:
        ctx.note('unchanged'); return
    body = ctx.http.get(url)
    parse_wikivoyage(bz2.BZ2File(io.BytesIO(body)), ctx.src, lang, lambda rs: ctx.emit('places', rs))
    ctx.st['ver'] = ver


# ---------- Mobility Database → GTFS ----------
def gtfs_feeds(csv_bytes, extra_licenses=()):
    """카탈로그 → 받을 피드. extra_licenses = 등록부에서 대표가 따로 허락한 라이선스 주소 조각(예 IDFM 'Licence Mobilités')."""
    out = []
    for r in csv.DictReader(io.StringIO(csv_bytes.decode('utf-8', 'replace'))):
        if r.get('data_type') != 'gtfs' or (r.get('status') or 'active') not in ('active', '') or r.get('redirect.id'):
            continue
        if (r.get('urls.authentication_type') or '0') not in ('0', ''):
            continue  # 키가 필요한 피드는 받지 않음
        lic, u = r.get('urls.license') or '', r.get('urls.latest') or r.get('urls.direct_download') or ''
        if not u or forbidden(u) or not (license_open(lic) or (lic and any(x in lic for x in extra_licenses))):
            continue
        bb = []
        for k in ('minimum_longitude', 'minimum_latitude', 'maximum_longitude', 'maximum_latitude'):
            try:
                bb.append(float(r.get('location.bounding_box.' + k) or ''))
            except ValueError:
                bb = None
                break
        out.append({'id': r['id'], 'url': u, 'license': lic, 'provider': r.get('provider', '')[:80], 'cc': r.get('location.country_code', ''),
                    'city': r.get('location.municipality', '')[:60], 'bbox': bb})
    return out


def lic_class(lic):
    """GTFS 보관 폴더(라이선스별): cc0 | cc-by | cc-by-sa | odbl | etalab | other-open."""
    s = (lic or '').lower()
    for key, cls in (('publicdomain/zero', 'cc0'), ('/by-sa/', 'cc-by-sa'), ('/by/', 'cc-by'), ('odbl', 'odbl'), ('etalab', 'etalab'), ('licence-ouverte', 'etalab')):
        if key in s:
            return cls
    return 'other-open'


def gtfs_target(feed, targets):
    """보관 대상 도시(등록부 mobility_gtfs.targets) 중 피드 bbox 가 겹치는 첫 도시 이름. 없으면 None."""
    bb = feed.get('bbox')
    if not bb:
        return None
    for t in targets or []:
        w, s_, e, n = t['bbox']
        if not (bb[2] < w or bb[0] > e or bb[3] < s_ or bb[1] > n):
            return t['city']
    return None


def parse_gtfs(zbytes, src, feed):
    z = zipfile.ZipFile(io.BytesIO(zbytes))
    names = {n.rsplit('/', 1)[-1]: n for n in z.namelist()}
    if 'stops.txt' not in names:
        return [], {}
    stops = list(csv.DictReader(io.TextIOWrapper(z.open(names['stops.txt']), encoding='utf-8-sig', errors='replace')))
    stations = [s for s in stops if (s.get('location_type') or '0') == '1'] or stops[:20000]
    rows = []
    for s in stations:
        r = place(src, f"{feed['id']}:{s.get('stop_id')}", s.get('stop_name'), s.get('stop_lat'), s.get('stop_lon'), cat='station',
                  extra={'feed': feed['id'], 'prov': feed['provider'], 'flic': feed['license'][:120], 'cc': feed['cc'] or None})
        if r:
            rows.append(r)
    info = {'stops': len(stops), 'stations': len(rows)}
    if 'routes.txt' in names:
        rt = list(csv.DictReader(io.TextIOWrapper(z.open(names['routes.txt']), encoding='utf-8-sig', errors='replace')))
        info['routes'] = len(rt)
        info['types'] = sorted({x.get('route_type', '') for x in rt})[:10]
    return rows, info


def gtfs(ctx):
    targets = ctx.src.get('targets') or []
    feeds = [f for f in gtfs_feeds(ctx.http.get(ctx.src['url']), ctx.src.get('extra_licenses') or ()) if shard_of(f['id'], ctx.nshards) == int(ctx.shard)]
    done = ctx.st.setdefault('done', {})
    for f in feeds:
        f['target'] = gtfs_target(f, targets)
    feeds.sort(key=lambda f: (f['target'] is None, done.get(f['id'], '')))  # 보관 대상 도시(서울·부산·교토·도쿄·오사카·파리·방콕) 먼저, 그다음 오래된 것부터
    summary, idx_key = [], f"gtfs/index/{ctx.shard}.json"
    idx = ctx.store.get_json(idx_key, {}) or {}
    try:
        _gtfs_loop(ctx, feeds, done, idx, summary)
    finally:  # 시간·한도로 멈춰도 보관 목록·요약은 남긴다
        if idx:
            ctx.store.put_json(idx_key, idx)
        if summary:
            ctx.emit('transit_feeds', summary)


def _gtfs_loop(ctx, feeds, done, idx, summary):
    for f in feeds:
        ctx.tick()
        if not _due(done, f['id'], refresh_days('gtfs', 7)):
            continue
        try:
            z = ctx.http.get(f['url'])
            rows, info = parse_gtfs(z, ctx.src, f)
        except (Blocked, zipfile.BadZipFile, IOError, KeyError) as e:
            ctx.note(f"feed {f['id']} skip: {type(e).__name__}"); done[f['id']] = today(); continue
        if rows:
            ctx.emit('transit', rows)
        if f['target']:  # (11/15) 원본 zip 을 라이선스별 폴더에 보관 — 앱 팀이 구간 '노선 근거'·'시간표' 등급에 쓴다(docs/TRANSIT_GRADES.md)
            key = f"gtfs/{lic_class(f['license'])}/{f['id']}/{today()}.zip"
            ctx.store.put(key, z)
            idx[f['id']] = {'city': f['target'], 'key': key, 'license': f['license'][:200], 'lic_class': lic_class(f['license']), 'provider': f['provider'],
                            'cc': f['cc'], 'bbox': f['bbox'], 'bytes': len(z), 'sha256': hashlib.sha256(z).hexdigest(), 'fetched': today(), **info}
            ctx.counts['gtfs_archived'] = ctx.counts.get('gtfs_archived', 0) + 1
        summary.append({'id': f'mobility_gtfs:{f["id"]}', 'feed': f['id'], 'cc': f['cc'], 'city': f['city'], 'prov': f['provider'], 'flic': f['license'][:120],
                        **({'target': f['target']} if f['target'] else {}), **info})
        done[f['id']] = today()


# ---------- 대만 관광서 ----------
def _ci(d, *keys):
    low = {k.lower(): v for k, v in d.items()}
    for k in keys:
        v = low.get(k.lower())
        if v not in (None, ''):
            return v
    return None


def _dict_lists(o):
    if isinstance(o, list) and o and isinstance(o[0], dict):
        yield o
    if isinstance(o, dict):
        for v in o.values():
            yield from _dict_lists(v)
    elif isinstance(o, list):
        for v in o:
            yield from _dict_lists(v)


def _tw_desc(d):
    """(11/17) 장소 소개 층(scripts/place_desc.py desc-tw)용: 관광서 소개 글(Description → Toldescribe)의 첫 문장만, 원문 그대로(번체 zh_Hant).
    첫 문장이 120자를 넘거나 4자 미만이면 싣지 않는다(중간에서 자르지 않음). 긴 본문·사진 주소는 담지 않는다."""
    sys.path.insert(0, os.path.join(HERE, '..'))
    import place_desc as PD
    for k in ('Description', 'Toldescribe', 'DescriptionDetail'):
        t = PD.first_sentence(_ci(d, k))
        t = t and PD.ok_text(t, [_ci(d, 'Name', 'AttractionName', 'ScenicSpotName')])
        if t:
            return {'dsc': t}
    return {}


def parse_taiwan(zbytes, src):
    rows = []
    z = zipfile.ZipFile(io.BytesIO(zbytes))
    for n in z.namelist():
        if not n.lower().endswith('.json'):
            continue
        j = json.loads(z.read(n).decode('utf-8-sig', 'replace'))
        for lst in _dict_lists(j):
            for d in lst:
                lon, lat = _ci(d, 'Px', 'PositionLon'), _ci(d, 'Py', 'PositionLat')
                nm = _ci(d, 'Name', 'AttractionName', 'ScenicSpotName')
                r = place(src, _ci(d, 'Id', 'AttractionID', 'ScenicSpotID') or nm, nm, lat, lon, cat=_ci(d, 'Class1', 'Class') or 'attraction',
                          names={'zh': nm}, hours=_ci(d, 'Opentime', 'OpenTime'), url=_ci(d, 'Website', 'WebsiteUrl'), addr=_ci(d, 'Add', 'Address'),
                          tel=_ci(d, 'Tel', 'Phone'), price=_ci(d, 'Ticketinfo', 'TicketInfo'), extra={'cc': 'TW', **_tw_desc(d)})
                if r:
                    rows.append(r)
    return rows


def taiwan(ctx):
    rows = parse_taiwan(ctx.http.get(ctx.src['url']), ctx.src)
    ctx.emit('places', rows)


# ---------- 여행 권고 ----------
def advisories_ca(ctx):
    j = json.loads(ctx.http.get(ctx.src['url']))
    rows = []
    for cc, d in (j.get('data') or {}).items():
        rows.append({'id': f'advisories_ca:{cc}', 'cc': cc, 'level': d.get('advisory-state'), 'name_en': d.get('country-eng'),
                     'updated': (d.get('date-published') or {}).get('date'), 'src': 'advisories_ca', 'lic': ctx.src['license']})
    ctx.emit('advisory', rows)


def advisories_uk(ctx):
    idx = json.loads(ctx.http.get(ctx.src['url']))
    kids = (idx.get('links') or {}).get('children') or []
    seen, rows = ctx.st.setdefault('seen', {}), []
    for k in kids:
        bp, up = k.get('base_path', ''), k.get('public_updated_at', '')
        if not bp.startswith('/foreign-travel-advice/') or seen.get(bp) == up:
            continue
        d = json.loads(ctx.http.get('https://www.gov.uk/api/content' + bp))
        det = d.get('details') or {}
        rows.append({'id': f'advisories_uk:{bp.rsplit("/", 1)[-1]}', 'name_en': (det.get('country') or {}).get('name') or k.get('title'),
                     'alert': det.get('alert_status') or [], 'updated': d.get('public_updated_at'), 'ref': 'https://www.gov.uk' + bp,
                     'src': 'advisories_uk', 'lic': ctx.src['license']})
        seen[bp] = up
    if rows:
        ctx.emit('advisory', rows)


# ---------- 공휴일(라이브러리) ----------
def holidays_lib(ctx):
    import holidays
    y = int(today()[:4]); rows = []
    for cc in sorted(holidays.list_supported_countries()):
        try:
            try:
                h = holidays.country_holidays(cc, years=[y, y + 1], language='en_US')
            except Exception:
                h = holidays.country_holidays(cc, years=[y, y + 1])
            rows.append({'id': f'holidays_lib:{cc}', 'cc': cc, 'days': [[d.isoformat(), n] for d, n in sorted(h.items())], 'src': 'holidays_lib', 'lic': 'MIT'})
        except Exception:
            continue
    ctx.emit('holidays', rows)


# ---------- Overture·Foursquare(나라별) ----------
def _shard_box(shard):
    region, cc = shard.split(':')
    reg = json.load(open(os.path.join(TOOLS, 'world_regions.json'), encoding='utf-8'))[region]
    return reg['bbox'], cc


SLICE_MAX = 64


def lon_strips(boxes, n):
    """지역 상자들을 경도 방향 n 조각씩으로 자른다 → [(x0, y0, x1, y1, last)]. last 가 아니면 x1 은 포함하지 않는다(조각끼리 겹치지 않음)."""
    out = []
    for b in boxes:
        w = (b[2] - b[0]) / n
        for i in range(n):
            out.append((round(b[0] + w * i, 6), b[1], b[2] if i == n - 1 else round(b[0] + w * (i + 1), 6), b[3], i == n - 1))
    return out


def xcond(col, s):
    return f"{col} >= {s[0]} and {col} {'<=' if s[4] else '<'} {s[2]}"


def sliced(ctx, key, boxes, run_strip):
    """큰 덤프를 지역 조각으로 잘라 읽고 이어받는다. 처음에는 자르지 않는다(n=1, 예전과 같은 질의).
    한 조각도 못 끝내고 시간(TIME)에 걸리면 다음 회차부터 4배로 잘게(최대 64) 자르고, 끝낸 조각은 상태(slices.done)에 적어 건너뛴다.
    key(릴리스·달)가 바뀌면 처음부터. 중간에 끊긴 조각의 행은 다시 받지만 id 가 같아 가공에서 한 행으로 합쳐진다."""
    sl = ctx.st.get('slices') or {}
    if sl.get('key') != key:
        sl = {'key': key, 'n': int(sl.get('n', 1)), 'done': []}
    ctx.st['slices'] = sl
    strips, did = lon_strips(boxes, sl['n']), False
    for i, strip in enumerate(strips):
        if i in sl['done']:
            continue
        try:
            run_strip(strip)
        except Stop as e:
            if str(e) == 'TIME' and not did and sl['n'] < SLICE_MAX:
                sl['n'], sl['done'] = min(sl['n'] * 4, SLICE_MAX), []
                ctx.note(f"slice → {sl['n']}")
            raise
        sl['done'].append(i); did = True
    ctx.st['slices'] = {'key': key, 'n': sl['n'], 'done': []}  # 다 끝남 — n 은 기억(다음 릴리스도 같은 크기로)


def overture_release(http):
    b = http.get('https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com/?list-type=2&prefix=release/&delimiter=/')
    rels = re.findall(rb'<Prefix>release/([0-9][0-9.\-]+)/</Prefix>', b)
    return max(x.decode() for x in rels) if rels else None


def _names_map(m):
    if isinstance(m, dict) and 'key' in m:
        return dict(zip(m['key'], m['value']))
    if isinstance(m, dict):
        return m
    if isinstance(m, list):
        return {k: v for k, v in m}
    return {}


def _lang_names(common):
    m = _names_map(common)
    out = {k: m.get(k) for k in ('ko', 'en', 'ja') if m.get(k)}
    zh = m.get('zh') or m.get('zh-Hans') or m.get('zh-Hant') or m.get('zh-CN') or m.get('zh-TW')
    if zh:
        out['zh'] = zh
    return out


def _rule_names(rules):
    """Overture names.rules(variant language) → ko·en·ja·zh 칸(collect-1 모양). 번역·변환 없음."""
    m = {}
    for r in rules or ():
        if isinstance(r, dict) and r.get('variant') == 'language' and r.get('language') and r.get('value'):
            m.setdefault(r['language'], r['value'])
    return _lang_names(m)


def overture(ctx):
    if ctx.st.get('rel_checked') != today():
        ctx.st['release'], ctx.st['rel_checked'] = overture_release(ctx.http) or os.environ.get('OVERTURE_RELEASE', '').replace('release/', ''), today()
    rel = ctx.st['release']
    if not rel or ctx.st.get('done_release') == rel:
        ctx.note('up to date'); return
    boxes, cc = _shard_box(ctx.shard)
    sys.path.insert(0, TOOLS)
    os.environ['OVERTURE_RELEASE'] = 'release/' + rel
    import importlib, ovt_common
    importlib.reload(ovt_common)
    c = ovt_common.con(); c.execute(f"SET memory_limit='{os.environ.get('DUCK_MEM', '10GB')}'")
    def run_strip(sp):
        # (11/18 245기) 2026-10 릴리스부터 categories 칸이 없어져(taxonomy 로 바뀜) 이 질의가 Binder Error 로 0행이었다 → taxonomy.primary.
        #  다국어 이름은 names.common 이 비어 있고 names.rules(variant language)에 있다.
        cur = c.execute(f"""select id, names.primary, names.rules, coalesce(basic_category, taxonomy.primary), confidence, websites[1],
            addresses[1].freeform, sources[1].dataset, sources[1].license, (bbox.xmin+bbox.xmax)/2, (bbox.ymin+bbox.ymax)/2
            from {ovt_common.SRC} where {xcond('bbox.xmin', sp)} and bbox.ymin between {sp[1]} and {sp[3]}
            and addresses[1].country = '{cc}' and confidence >= 0.6 and coalesce(operating_status, 'open') = 'open'""")
        while True:
            batch = cur.fetchmany(50000)
            if not batch:
                break
            rows = []
            for i, nm, rules, cat, conf, web, addr, ds, lic, lon, lat in batch:
                r = place(ctx.src, i, nm, lat, lon, cat=cat, names=_rule_names(rules), url=web, addr=addr,
                          extra={'cc': cc, 'cf': round(conf or 0, 2), 'via': ds, 'lic': lic})
                if r:
                    r['lic'] = lic or 'CDLA-Permissive-2.0'
                    rows.append(r)
            ctx.emit('places', rows)
            ctx.tick()

    sliced(ctx, rel, boxes, run_strip)
    ctx.st['done_release'] = rel


def fsq(ctx):
    tok = os.environ.get('FSQ_TOKEN', '').strip().strip('"').strip("'")
    if not tok:
        ctx.note('no FSQ_TOKEN — skip'); return
    if not _due(ctx.st, 'done', 28):
        ctx.note('up to date'); return
    import duckdb
    boxes, cc = _shard_box(ctx.shard)
    c = duckdb.connect()
    for s in ('INSTALL httpfs', 'LOAD httpfs', 'INSTALL iceberg', 'LOAD iceberg'):
        c.execute(s)
    c.execute(f"SET memory_limit='{os.environ.get('DUCK_MEM', '10GB')}'")
    c.execute(f"CREATE TEMPORARY SECRET fsq (TYPE ICEBERG, TOKEN '{tok}')"); del tok
    c.execute("ATTACH 'places' AS places (TYPE ICEBERG, ENDPOINT 'https://catalog.h3-hub.foursquare.com/iceberg', SECRET fsq)")
    def run_strip(sp):
        cur = c.execute(f"""select fsq_place_id, name, latitude, longitude, address, website, fsq_category_labels[1], tel
            from places.datasets.places_os where latitude between {sp[1]} and {sp[3]} and {xcond('longitude', sp)}
            and country = '{cc}' and date_closed is null and name is not null""")
        while True:
            batch = cur.fetchmany(50000)
            if not batch:
                break
            rows = [r for r in (place(ctx.src, i, nm, lat, lon, cat=(cat or '').rsplit('>', 1)[-1].strip(), url=web, addr=addr, tel=tel, extra={'cc': cc})
                                for i, nm, lat, lon, addr, web, cat, tel in batch) if r]
            ctx.emit('places', rows)
            ctx.tick()

    sliced(ctx, today()[:7], boxes, run_strip)
    ctx.st['done'] = today()


# ---------- Wikidata 씨앗(공식 사이트) ----------
WD_TYPES = {'Q33506': 'museum', 'Q207694': 'museum', 'Q1248784': 'airport', 'Q570116': 'sight'}
CRAWL_SHARDS = 6
# query.wikidata.org 는 robots.txt 가 /sparql 을 막는다(11/15 확인) → 같은 Wikidata(CC0)를 싣고 robots 가 막지 않는 QLever 공개 끝점을 쓴다.
PFX = ('PREFIX wd: <http://www.wikidata.org/entity/> PREFIX wdt: <http://www.wikidata.org/prop/direct/> '
       'PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> ')


def wikidata_seeds(ctx):
    if _due(ctx.st, 'cycle', 7):
        ctx.st.update(cycle=today(), off={})
    off = ctx.st.setdefault('off', {})
    for q, cat in WD_TYPES.items():
        if off.get(q) == -1:
            continue
        o = off.get(q, 0)
        sparql = PFX + f"""SELECT ?item ?site ?coord ?cc ?ko ?en ?ja ?zh WHERE {{ ?item wdt:P31 wd:{q}; wdt:P856 ?site; wdt:P625 ?coord.
          OPTIONAL {{ ?item wdt:P17/wdt:P297 ?cc }} OPTIONAL {{ ?item rdfs:label ?ko FILTER(lang(?ko)='ko') }} OPTIONAL {{ ?item rdfs:label ?en FILTER(lang(?en)='en') }}
          OPTIONAL {{ ?item rdfs:label ?ja FILTER(lang(?ja)='ja') }} OPTIONAL {{ ?item rdfs:label ?zh FILTER(lang(?zh)='zh') }} }} LIMIT 2000 OFFSET {o}"""
        b = ctx.http.get(ctx.src['url'] + '?query=' + urllib.parse.quote(sparql), accept='application/sparql-results+json')
        res = json.loads(b)['results']['bindings']
        rows, seeds = [], {}
        for x in res:
            g = lambda k: (x.get(k) or {}).get('value')
            m = re.match(r'(?i)Point\(([-0-9.eE]+) ([-0-9.eE]+)\)', g('coord') or '')
            qid, site = g('item').rsplit('/', 1)[-1], g('site')
            if not m or not site or forbidden(site):
                continue
            nm = g('en') or g('ko') or g('ja') or g('zh')
            r = place(ctx.src, qid, nm, m.group(2), m.group(1), cat=cat, names={k: g(k) for k in LANGS}, url=site, wd=qid, extra={'cc': g('cc')})
            if r:
                rows.append(r)
                seeds[qid] = {'url': site, 'nm': nm, 'lat': r['lat'], 'lon': r['lon'], 'c': cat, 'cc': g('cc')}
        ctx.emit('places', rows)
        by = {}
        for qid, s in seeds.items():
            by.setdefault(shard_of(host_of(s['url']), CRAWL_SHARDS), {})[qid] = s
        for k, part in by.items():
            key = f'seeds/sites_{k}.json.gz'
            cur = ctx.store.get_json(key, {}) or {}
            cur.update(part)
            ctx.store.put_json(key, cur)
        off[q] = -1 if len(res) < 2000 else o + 2000
        ctx.tick()


# ---------- 공식 사이트 구조화 데이터 탐색기 ----------
KEEP_TYPES = {'place', 'localbusiness', 'touristattraction', 'museum', 'airport', 'landmarksorhistoricalbuildings', 'restaurant', 'foodestablishment',
              'cafeorcoffeeshop', 'lodgingbusiness', 'hotel', 'park', 'event', 'touristinformationcenter', 'civicstructure', 'zoo', 'aquarium',
              'artgallery', 'amusementpark', 'organization', 'governmentorganization', 'shoppingcenter', 'store'}


class _Page(html.parser.HTMLParser):
    """JSON-LD 블록, microdata(itemscope/itemprop), 같은 사이트 링크만 모은다. 본문 글은 모으지 않는다."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ld, self.links, self.items, self._in_ld, self._buf, self._stack, self._prop = [], [], [], False, [], [], None

    def handle_starttag(self, tag, a):
        a = dict(a)
        if tag == 'script' and (a.get('type') or '').lower() == 'application/ld+json':
            self._in_ld, self._buf = True, []
        if tag == 'a' and a.get('href'):
            self.links.append(a['href'])
        if 'itemscope' in a:
            self._stack.append({'@type': (a.get('itemtype') or '').rsplit('/', 1)[-1], '_prop': a.get('itemprop')})
        if 'itemprop' in a and self._stack:
            v = a.get('content') or a.get('datetime') or a.get('href')
            if v and 'itemscope' not in a:
                self._stack[-1].setdefault(a['itemprop'], v)
            elif 'itemscope' not in a:
                self._prop = a['itemprop']

    def handle_endtag(self, tag):
        if tag == 'script' and self._in_ld:
            self._in_ld = False
            self.ld.append(''.join(self._buf))
        if tag in ('div', 'section', 'article', 'span', 'li', 'body', 'main') and self._stack and self._prop is None:
            pass

    def handle_data(self, d):
        if self._in_ld:
            self._buf.append(d)
        elif self._prop and self._stack:
            if d.strip():
                self._stack[-1].setdefault(self._prop, d.strip()[:200])
            self._prop = None

    def close(self):
        super().close()
        while self._stack:
            it = self._stack.pop()
            p = it.pop('_prop', None)
            if p and self._stack:
                self._stack[-1].setdefault(p, it)
            else:
                self.items.append(it)


def _walk_ld(o):
    if isinstance(o, list):
        for x in o:
            yield from _walk_ld(x)
    elif isinstance(o, dict):
        if '@graph' in o:
            yield from _walk_ld(o['@graph'])
        yield o


def structured(html_text):
    """페이지 → (구조화 항목 list, 링크 list). 사람(Person) 항목은 버린다."""
    p = _Page()
    try:
        p.feed(html_text); p.close()
    except Exception:
        pass
    items = []
    for blk in p.ld:
        try:
            items.extend(_walk_ld(json.loads(blk.strip())))
        except ValueError:
            continue
    items.extend(p.items)
    out = []
    for it in items:
        t = it.get('@type')
        ts = [str(x).lower() for x in (t if isinstance(t, list) else [t])]
        if 'person' in ts or not any(x in KEEP_TYPES for x in ts):
            continue
        out.append((ts[0], it))
    return out, p.links


def facts_of(kind, it):
    def s(v):
        if isinstance(v, dict):
            return v.get('name') or v.get('@id')
        return v if isinstance(v, str) else None
    geo = it.get('geo') or {}
    addr = it.get('address')
    if isinstance(addr, dict):
        addr = ', '.join(str(addr[k]) for k in ('streetAddress', 'addressLocality', 'addressRegion', 'postalCode', 'addressCountry') if isinstance(addr.get(k), str))
    price = it.get('priceRange')
    offers = it.get('offers')
    if not price and isinstance(offers, (dict, list)):
        o = offers[0] if isinstance(offers, list) and offers else offers
        if isinstance(o, dict) and o.get('price') is not None:
            price = f"{o.get('price')} {o.get('priceCurrency', '')}".strip()
    f = {'kind': kind, 'name': s(it.get('name')), 'addr': addr if isinstance(addr, str) else None,
         'lat': geo.get('latitude') if isinstance(geo, dict) else None, 'lon': geo.get('longitude') if isinstance(geo, dict) else None,
         'hours': it.get('openingHoursSpecification') or it.get('openingHours'), 'price': price,
         'tel': it.get('telephone') if kind != 'person' and isinstance(it.get('telephone'), str) else None}
    if kind == 'event':
        loc = it.get('location')
        f.update(start=s(it.get('startDate')), end=s(it.get('endDate')), where=s(loc) if not isinstance(loc, list) else None)
    return f


# (11/18 252기) 수확률: 11/17 조각 1 은 한 시간에 요청 1,600여 번·0행(사이트마다 15쪽을 앞에서부터 다 읽음).
#  영업시간·방문 안내가 있을 법한 쪽을 먼저 읽고(주소·낱말), 사이트당 8쪽까지만 — 같은 한도로 더 많은 사이트를 본다. robots·쉼은 Http 가 그대로 지킨다.
HOURS_LINK = re.compile(r'hour|opening|open-?time|visit|plan|contact|about|info|access|admission|ticket|guide|horaire|oeffnung|öffnung|orari|horario|'
                        r'%EC%9A%B4%EC%98%81|운영|이용|관람|안내|営業|開館|利用|ご案内|アクセス|开放|開放|营业|營業|参观|參觀|時間|时间', re.I)


def crawl_site(ctx, qid, seed, max_pages=8):
    """씨앗 사이트 하나: 같은 사이트 안 링크를 따라 최대 max_pages 쪽(영업시간·방문 안내 쪽 먼저). 구조화 사실값만, 페이지 주소는 링크로."""
    root = seed['url']
    h0 = host_of(root)
    queue, seen, best, events = [root], set(), None, []
    while queue and len(seen) < max_pages:
        u = queue.pop(0)
        if u in seen or forbidden(u):
            continue
        seen.add(u)
        try:
            body = ctx.http.get(u, accept='text/html')
        except Blocked:
            return None, events  # robots·차단 → 이 사이트는 그만
        except (IOError, ValueError):  # ValueError = 깨진 주소(예: 잘못된 호스트) — 그 쪽만 건너뜀
            continue
        if len(body) > 3_000_000:
            continue
        items, links = structured(body.decode('utf-8', 'replace'))
        for kind, it in items:
            f = facts_of(kind, it)
            f['page'] = u
            if kind == 'event' and f.get('name') and f.get('start'):
                events.append({k: f[k] for k in ('name', 'start', 'end', 'where', 'page') if f.get(k)})
            elif best is None or (f.get('hours') and not best.get('hours')):
                best = f
        for l in links:
            a = urllib.parse.urljoin(u, l).split('#', 1)[0]
            if host_of(a) == h0 and a.startswith(('http://', 'https://')) and a not in seen and a not in queue and not re.search(r'\.(jpg|jpeg|png|gif|pdf|zip|mp4|webp|svg)$', a, re.I):
                queue.append(a)
        queue.sort(key=lambda a: 0 if HOURS_LINK.search(urllib.parse.urlsplit(a).path) else 1)  # 안정 정렬 — 같은 무리 안에서는 찾은 순서
        if best and best.get('hours'):
            break  # 영업시간을 찾았으면 이 사이트는 끝(행사 목록은 첫 쪽들에서 이미 모음)
    return best, events[:50]


def crawl(ctx):
    seeds = ctx.store.get_json(f'seeds/sites_{ctx.shard}.json.gz', {}) or {}
    done = ctx.st.setdefault('done', {})
    order = sorted(seeds, key=lambda q: (done.get(q, ''), city_rank(seeds[q].get('lat'), seeds[q].get('lon')), region_rank(seeds[q].get('cc'))))
    # 오래된 것부터, 같으면 상위 도시(sources/priority_cities.json) 먼저, 그다음 많이 찾는 나라
    ttl = refresh_days('hours', 14)  # 영업시간은 자주 바뀐다 — 사이트마다 14일(sources/refresh_policy.json)
    rows = []
    for qid in order:
        if not _due(done, qid, ttl):
            break
        seed = seeds[qid]
        try:
            best, events = crawl_site(ctx, qid, seed)
        except (Stop, Blocked):
            break
        except Exception as e:  # (263기) 사이트 하나의 뜻밖의 오류가 조각 전체를 죽이고 같은 사이트가 매번 맨 앞에서 또 죽는 고리(official_sites/1, 8회 연속)를 끊는다.
            ctx.notes.append(f'skip {qid}: {type(e).__name__}')  # 주소·값은 싣지 않음
            ctx.skips = getattr(ctx, 'skips', 0) + 1  # 관측: 건너뛴 사이트 수가 결과(counts)·상태에 남는다(조용히 늘어나면 눈에 띄게)
            done[qid] = today()
            continue
        done[qid] = today()
        if best is None and not events:
            continue
        b = best or {}
        r = place(ctx.src, qid, b.get('name') or seed['nm'], b.get('lat') or seed['lat'], b.get('lon') or seed['lon'], cat=b.get('kind') or seed['c'],
                  hours=b.get('hours'), url=b.get('page') or seed['url'], addr=b.get('addr'), tel=b.get('tel'), price=b.get('price'), wd=qid,
                  extra={'cc': seed.get('cc'), 'rank': 'official', **({'ev': events} if events else {})})
        if r:
            rows.append(r)
        if len(rows) >= 200:
            ctx.emit('official', rows); rows = []
        ctx.tick()
    if rows:
        ctx.emit('official', rows)


# ---------- 발견기(카탈로그) ----------
FMT_OK = ('csv', 'json', 'geojson')


def _ckan_entries(j, cat):
    for p in ((j.get('result') or {}).get('results') or []):
        lic = p.get('license_id') or p.get('license_title') or ''
        res = [{'url': r.get('url'), 'fmt': (r.get('format') or '').lower()} for r in p.get('resources') or []
               if (r.get('format') or '').lower() in FMT_OK and r.get('url') and not forbidden(r['url'])][:3]
        yield {'key': f"{cat}:{p.get('id')}", 'title': (p.get('title') or '')[:160], 'license': lic[:80], 'org': ((p.get('organization') or {}).get('title') or '')[:80],
               'res': res}


def _europa_entries(j):
    for p in ((j.get('result') or {}).get('results') or []):
        t = p.get('title') or {}
        title = t.get('en') or next(iter(t.values()), '') if isinstance(t, dict) else str(t)
        res, lic = [], ''
        for d in p.get('distributions') or []:
            fmt = ((d.get('format') or {}).get('id') or '').lower()
            u = (d.get('download_url') or [None])[0] if isinstance(d.get('download_url'), list) else d.get('download_url')
            u = u or ((d.get('access_url') or [None])[0] if isinstance(d.get('access_url'), list) else d.get('access_url'))
            lic = lic or ((d.get('license') or {}).get('id') or '') if isinstance(d.get('license'), dict) else lic
            if fmt in FMT_OK and u and not forbidden(u):
                res.append({'url': u, 'fmt': fmt})
        yield {'key': f"europa:{p.get('id')}", 'title': (title or '')[:160], 'license': lic[:80], 'org': '', 'res': res[:3]}


def discover(ctx):
    disc = ctx.store.get_json('discovered.json.gz', {}) or {}
    terms, pos = ctx.reg.get('discover_terms') or ['tourism'], ctx.st.setdefault('pos', {})
    added = active = 0
    for cat in ctx.reg.get('catalogs') or []:
        for term in terms:
            k = f"{cat['id']}:{term}"
            start = pos.get(k, 0)
            if start < 0:
                continue
            if cat['kind'] == 'ckan':
                u = f"{cat['url']}?q={urllib.parse.quote(term)}&rows=50&start={start}"
            else:
                u = f"{cat['url']}?q={urllib.parse.quote(term)}&filter=dataset&limit=50&page={start // 50}"
            try:
                j = json.loads(ctx.http.get(u, accept='application/json'))
            except (ValueError, IOError, Blocked):
                pos[k] = -1; continue
            ents = list(_ckan_entries(j, cat['id']) if cat['kind'] == 'ckan' else _europa_entries(j))
            for e in ents:
                if e['key'] in disc:
                    continue
                e['status'] = 'active' if license_open(e['license']) and e['res'] else 'candidate'
                e['found'] = today()
                disc[e['key']] = e
                added += 1; active += e['status'] == 'active'
            pos[k] = start + 50 if len(ents) == 50 and start < 1000 else -1
    if _due(ctx.st, 'cycle', 7):  # 일주일에 한 번 처음부터 다시(새로 올라온 데이터셋)
        ctx.st.update(cycle=today(), pos={})
    ctx.store.put_json('discovered.json.gz', disc)
    ctx.note(f'discovered +{added} (active +{active}), total {len(disc)}')
    ctx.st['n_total'], ctx.st['n_active'] = len(disc), sum(1 for e in disc.values() if e['status'] == 'active')


# ---------- 발견된 열린 데이터셋 일반 수집 ----------
PERSONAL = re.compile(r'(e-?mail|owner|contact_?name|person|first_?name|last_?name|surname|birth|gender|mobile)', re.I)
COLS = {'nm': ('name', 'nom', 'title', 'titel', 'nome', 'nombre', 'naam', 'site_name', 'attraction'), 'lat': ('lat', 'latitude', 'y', 'breitengrad'),
        'lon': ('lon', 'lng', 'long', 'longitude', 'x', 'laengengrad'), 'h': ('opening_hours', 'hours', 'horaires', 'oeffnungszeiten'),
        'url': ('website', 'url', 'web', 'site_web'), 'a': ('address', 'adresse', 'addr', 'indirizzo'), 'c': ('category', 'type', 'categorie', 'kategorie')}


def _pick(cols, want):
    low = {c.lower().strip(): c for c in cols if isinstance(c, str) and not PERSONAL.search(c)}  # 11/17: 열 이름 없는 칸(csv 남는 칸 = None) 건너뜀
    for w in want:
        if w in low:
            return low[w]
    return None


def parse_table(b, fmt):
    if fmt == 'geojson' or (fmt == 'json' and b[:200].find(b'FeatureCollection') >= 0):
        j = json.loads(b)
        for f in j.get('features') or []:
            g = f.get('geometry') or {}
            if g.get('type') == 'Point':
                p = dict(f.get('properties') or {}); p['lon'], p['lat'] = g['coordinates'][:2]
                yield p
        return
    if fmt == 'json':
        j = json.loads(b)
        for lst in _dict_lists(j):
            yield from lst
            return
        return
    txt = b.decode('utf-8-sig', 'replace')
    dialect = csv.Sniffer().sniff(txt[:5000], delimiters=',;\t') if txt else csv.excel
    yield from csv.DictReader(io.StringIO(txt), dialect=dialect)


def open_dataset(ctx):
    disc = ctx.store.get_json('discovered.json.gz', {}) or {}
    done = ctx.st.setdefault('done', {})
    mine = sorted((k for k, e in disc.items() if e['status'] == 'active' and shard_of(k, ctx.nshards) == int(ctx.shard)), key=lambda k: done.get(k, ''))
    for k in mine:
        if not _due(done, k, refresh_days('datasets', 30)):
            break
        e, rows = disc[k], []
        src = dict(ctx.src, license=e['license'], id='open_dataset')
        for res in e['res']:
            try:
                b = ctx.http.get(res['url'])
                if len(b) > 30_000_000:
                    continue
                recs = list(parse_table(b, res['fmt']))[:200000]
            except (Blocked, IOError, ValueError, csv.Error, KeyError, TypeError):
                continue
            if not recs:
                continue
            cols = list(recs[0].keys())
            m = {f: _pick(cols, w) for f, w in COLS.items()}
            if not (m['nm'] and m['lat'] and m['lon']):
                continue
            for i, r in enumerate(recs):
                x = place(src, f"{k}:{i}", r.get(m['nm']), r.get(m['lat']), r.get(m['lon']), cat=r.get(m['c']) if m['c'] else 'other',
                          hours=r.get(m['h']) if m['h'] else None, url=r.get(m['url']) if m['url'] else None, addr=r.get(m['a']) if m['a'] else None,
                          extra={'ds': k, 'dt': e['title'][:80]})
                if x:
                    rows.append(x)
        if rows:
            ctx.emit('places', rows)
        done[k] = today()
        ctx.tick()


# ---------- 사진 층(11/15) — Wikidata P18 → Commons(CC0·CC BY·CC BY-SA) + 한국 TourAPI firstimage(공공누리 1·3유형) ----------
def _gs(*args, stdin=None, timeout=1800):
    return subprocess.run(['gcloud', 'storage', *args], input=stdin, capture_output=True, timeout=timeout)


def _gs_json(path):
    r = _gs('cat', path)
    try:
        return json.loads(r.stdout) if r.returncode == 0 and r.stdout else None
    except ValueError:
        return None


def photos(ctx):
    """조각 = 세계 타일 지역(tools/world_regions.json 이름). 그 지역 타일 → tools/photo_layer.py → Storage collect/v1/layers/photos-<지역>.json.gz.
    위키미디어 요청은 photo_layer(pop_build) 가 직접 한다(1.2초 간격·429 Retry-After·캐시). 캐시는 조각마다 Storage cache/photos/ 에 두고 이어 쓴다."""
    if not ctx.store.gs:
        raise Stop('NO_GCS')
    bucket = ctx.store.root[5:].split('/', 1)[0]
    region, base = ctx.shard, f'gs://{bucket}/places/v1'
    if region != 'korea' and os.environ.get('TL_WIKI_API') != '1':
        # (11/18) 위키미디어 /w/api.php 는 robots Disallow → photo_layer 는 TourAPI(한국)만. 다른 지역 Commons 사진은 place-desc 단계 photos-wd(QLever SDC)
        ctx.note(f'{region}: Commons 사진은 place-desc 단계(photos-wd)로 — 건너뜀'); return
    ver = (_gs_json(f'{base}/manifest.json') or {}).get('version')
    man = (_gs_json(f'{base}/{ver}/regions/{region}.json') or _gs_json(f'{base}/{ver}/parts/{region}.json')) if ver else None
    files = [t.get('file') for t in (man or {}).get('tiles') or [] if isinstance(t.get('file'), str) and t['file'].startswith('w-') and '/' not in t['file']]
    if not files:
        ctx.note(f'{region}: 타일 없음(판 {ver})'); return
    w = tempfile.mkdtemp(prefix='ph_')
    for k in ('tiles', 'hours', 'kto', 'cache'):
        os.makedirs(os.path.join(w, k))
    _gs('cp', '-q', '-I', os.path.join(w, 'tiles') + '/', stdin='\n'.join(f'{base}/{ver}/tiles/{f}' for f in files).encode())
    lt = (_gs_json(f'{base}/layers.json') or {}).get('version')
    regs = json.load(open(os.path.join(TOOLS, 'layers_regions.json'), encoding='utf-8'))
    hs = [f'{base}/{lt}/hours_src/hours-{k}.json.gz' for k, v in regs.items() if not k.startswith('_') and v.get('world') == region] if lt else []
    if hs:
        _gs('cp', '-q', '-I', os.path.join(w, 'hours') + '/', stdin='\n'.join(hs).encode())
    if region == 'korea':
        _gs('cp', '-q', f'{base}/layers_extra/kto_*.json.gz', os.path.join(w, 'kto') + '/')
    ck = f'cache/photos/{region}.tar.gz'
    b = ctx.store.get(ck)
    if b:
        tarfile.open(fileobj=io.BytesIO(b)).extractall(os.path.join(w, 'cache'), filter='data')
    out = os.path.join(w, f'photos-{region}.json.gz')
    left = max(60, int(ctx.deadline - time.time()) - 240)
    env = dict(os.environ, POP_CACHE=os.path.join(w, 'cache'), TL_WIKI_UA=ctx.reg.get('user_agent') or 'TripRoutie-collector/1.0')
    cmd = [sys.executable, os.path.join(TOOLS, 'photo_layer.py'), out, region, os.path.join(w, 'tiles'), '--hours', os.path.join(w, 'hours'), '--kto', os.path.join(w, 'kto')]
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=left)
        ctx.note(((r.stdout or r.stderr or '').strip().splitlines() or ['no output'])[-1])
        done = r.returncode == 0
    except subprocess.TimeoutExpired:
        done = False
        ctx.note('TIME — 캐시만 저장, 다음 회차에 이어서')
    bio = io.BytesIO()
    with tarfile.open(fileobj=bio, mode='w:gz') as t:
        t.add(os.path.join(w, 'cache'), arcname='.')
    ctx.store.put(ck, bio.getvalue())
    if done and os.path.exists(out):
        ctx.store.put(f'layers/photos-{region}.json.gz', open(out, 'rb').read())
        ctx.counts['photos'] = len(json.loads(gzip.decompress(open(out, 'rb').read())).get('rows') or {})
    shutil.rmtree(w, ignore_errors=True)
    if not done:
        raise Stop('TIME')


REGISTRY = {f.__name__: f for f in (wikivoyage, gtfs, taiwan, advisories_ca, advisories_uk, holidays_lib, overture, fsq, wikidata_seeds, crawl, discover, open_dataset, photos)}
DISCOVERY = {'wikidata_seeds', 'crawl', 'discover'}  # 매시 예산의 30% 몫
