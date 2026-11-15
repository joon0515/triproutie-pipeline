"""출처별 수집기. 모두 같은 모양: fn(ctx) — ctx.http(예절 HTTP)·ctx.st(이 조각 상태)·ctx.emit(dataset, rows)·ctx.store·ctx.shard·ctx.src.
결과 원본은 ctx.emit → Storage raw/<source>/<날짜>/ 에만. 저장소(GitHub)에는 상태 요약만 남는다."""
import bz2, csv, datetime, hashlib, html.parser, io, json, os, re, sys, time, urllib.parse, zipfile
import xml.etree.ElementTree as ET

from common import LANGS, Stop, Blocked, forbidden, host_of, license_open, place, today, valid_coord

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
def gtfs_feeds(csv_bytes):
    out = []
    for r in csv.DictReader(io.StringIO(csv_bytes.decode('utf-8', 'replace'))):
        if r.get('data_type') != 'gtfs' or (r.get('status') or 'active') not in ('active', '') or r.get('redirect.id'):
            continue
        if (r.get('urls.authentication_type') or '0') not in ('0', ''):
            continue  # 키가 필요한 피드는 받지 않음
        lic, u = r.get('urls.license') or '', r.get('urls.latest') or r.get('urls.direct_download') or ''
        if not u or forbidden(u) or not license_open(lic):
            continue
        out.append({'id': r['id'], 'url': u, 'license': lic, 'provider': r.get('provider', '')[:80], 'cc': r.get('location.country_code', ''),
                    'city': r.get('location.municipality', '')[:60]})
    return out


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
    feeds = [f for f in gtfs_feeds(ctx.http.get(ctx.src['url'])) if shard_of(f['id'], ctx.nshards) == int(ctx.shard)]
    done = ctx.st.setdefault('done', {})
    feeds.sort(key=lambda f: done.get(f['id'], ''))  # 오래된 것부터
    summary = []
    for f in feeds:
        if not _due(done, f['id'], 7):
            continue
        try:
            rows, info = parse_gtfs(ctx.http.get(f['url']), ctx.src, f)
        except (Blocked, zipfile.BadZipFile, IOError, KeyError) as e:
            ctx.note(f"feed {f['id']} skip: {type(e).__name__}"); done[f['id']] = today(); continue
        if rows:
            ctx.emit('transit', rows)
        summary.append({'id': f'mobility_gtfs:{f["id"]}', 'feed': f['id'], 'cc': f['cc'], 'city': f['city'], 'prov': f['provider'], 'flic': f['license'][:120], **info})
        done[f['id']] = today()
    if summary:
        ctx.emit('transit_feeds', summary)


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
                          tel=_ci(d, 'Tel', 'Phone'), price=_ci(d, 'Ticketinfo', 'TicketInfo'), extra={'cc': 'TW'})
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
    sel = ' union all '.join(f"""select id, names.primary, names.common, coalesce(basic_category, categories.primary), confidence, websites[1],
        addresses[1].freeform, sources[1].dataset, sources[1].license, (bbox.xmin+bbox.xmax)/2, (bbox.ymin+bbox.ymax)/2
        from {ovt_common.SRC} where bbox.xmin between {b[0]} and {b[2]} and bbox.ymin between {b[1]} and {b[3]}
        and addresses[1].country = '{cc}' and confidence >= 0.6 and coalesce(operating_status, 'open') = 'open'""" for b in boxes)
    cur = c.execute(sel)
    while True:
        batch = cur.fetchmany(50000)
        if not batch:
            break
        rows = []
        for i, nm, common, cat, conf, web, addr, ds, lic, lon, lat in batch:
            r = place(ctx.src, i, nm, lat, lon, cat=cat, names=_lang_names(common), url=web, addr=addr,
                      extra={'cc': cc, 'cf': round(conf or 0, 2), 'via': ds, 'lic': lic})
            if r:
                r['lic'] = lic or 'CDLA-Permissive-2.0'
                rows.append(r)
        ctx.emit('places', rows)
        ctx.tick()
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
    sel = ' union all '.join(f"""select fsq_place_id, name, latitude, longitude, address, website, fsq_category_labels[1]
        from places.datasets.places_os where latitude between {b[1]} and {b[3]} and longitude between {b[0]} and {b[2]}
        and country = '{cc}' and date_closed is null and name is not null""" for b in boxes)
    cur = c.execute(sel)
    while True:
        batch = cur.fetchmany(50000)
        if not batch:
            break
        rows = [r for r in (place(ctx.src, i, nm, lat, lon, cat=(cat or '').rsplit('>', 1)[-1].strip(), url=web, addr=addr, extra={'cc': cc})
                            for i, nm, lat, lon, addr, web, cat in batch) if r]
        ctx.emit('places', rows)
        ctx.tick()
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


def crawl_site(ctx, qid, seed, max_pages=15):
    """씨앗 사이트 하나: 같은 사이트 안 링크를 따라 최대 max_pages 쪽. 구조화 사실값만, 페이지 주소는 링크로."""
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
        except IOError:
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
            if host_of(a) == h0 and a.startswith(('http://', 'https://')) and a not in seen and not re.search(r'\.(jpg|jpeg|png|gif|pdf|zip|mp4|webp|svg)$', a, re.I):
                queue.append(a)
    return best, events[:50]


def crawl(ctx):
    seeds = ctx.store.get_json(f'seeds/sites_{ctx.shard}.json.gz', {}) or {}
    done = ctx.st.setdefault('done', {})
    order = sorted(seeds, key=lambda q: done.get(q, ''))
    rows = []
    for qid in order:
        if not _due(done, qid, 30):
            break
        seed = seeds[qid]
        try:
            best, events = crawl_site(ctx, qid, seed)
        except Stop:
            break
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
    low = {c.lower().strip(): c for c in cols if not PERSONAL.search(c)}
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
        if not _due(done, k, 30):
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


REGISTRY = {f.__name__: f for f in (wikivoyage, gtfs, taiwan, advisories_ca, advisories_uk, holidays_lib, overture, fsq, wikidata_seeds, crawl, discover, open_dataset)}
DISCOVERY = {'wikidata_seeds', 'crawl', 'discover'}  # 매시 예산의 30% 몫
