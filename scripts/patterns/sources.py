"""패턴 출처 읽기 — 공식 덤프(Wikivoyage)와 robots 허용 사이트만. 예절 HTTP·robots·금지 도메인은 scripts/collect/common.py 것을 그대로 쓴다.

여기에 없는 것(일부러): 유튜브·네이버·카카오를 부르는 코드. 판정이 '사용 안 함'인 출처는 받는 함수 자체를 두지 않는다(docs/PATTERNS_POLICY.md).
받은 글은 함수 안에서 숫자로 바꾸고 버린다. 돌려주는 표본에는 도시 열쇠·나라·여행 유형·하루 숫자만 있다.
"""
import bz2, html.parser, io, os, re, sys, urllib.error, urllib.parse
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'collect'))
import yaml  # noqa: E402
from common import Blocked, Stop, forbidden, host_of, map_cat  # noqa: E402
import parse as P  # noqa: E402
from places import _CAT  # noqa: E402

LISTING = re.compile(r'\{\{\s*(see|do|eat|drink|sleep|buy|go|listing|marker|vcard)\s*\|([^{}]*)', re.I)
HEAD = re.compile(r'^(=+)\s*(.*?)\s*=+\s*$', re.M)
_WIKI = (re.compile(r'\[\[(?:[^|\]]*\|)?([^\]]*)\]\]'), re.compile(r"'{2,}"), re.compile(r'<[^>]+>'), re.compile(r'\{\{[^{}]*\}\}'))


def load_sources(path=None):
    return yaml.safe_load(open(path or os.path.join(HERE, 'sources.yaml'), encoding='utf-8'))['sources']


def usable(src):
    """받아도 되는 출처인가 — active + 판정 ok|conditional + 받는 함수가 있음. 사이트는 사람이 약관을 확인한 날짜가 있어야 한다."""
    if src.get('status') != 'active' or src.get('verdict') not in ('ok', 'conditional') or src.get('kind') not in FETCHERS:
        return False
    if src['kind'] == 'site':
        seeds = src.get('seeds') or []
        return bool(src.get('terms_checked') and src.get('terms_url') and seeds) and not any(forbidden(u) for u in seeds)
    return True


def _clean(line):
    for rx in _WIKI[:3]:
        line = rx.sub(lambda m: m.group(1) if m.groups() else ' ', line)
    return line.lstrip('*#:; ').strip()


def wv_sample(title, text, gaz, cities):
    """Wikivoyage 문서 1편 → 표본 또는 None. 날 제목(Day 1 …)이 2개 이상인 문서만 일정으로 본다."""
    heads = [(m.start(), m.end(), P.day_no(h) if len(h) <= 60 else None) for m in HEAD.finditer(text) for h in [_clean(m.group(2))]]
    days = [(i, h) for i, h in enumerate(heads) if h[2] is not None]
    if len(days) < 2:
        return None
    out = []
    for i, (s, e, no) in days:
        body = text[e:heads[i + 1][0] if i + 1 < len(heads) else len(text)]
        cats, n = {}, 0
        for m in LISTING.finditer(body):
            kind = m.group(1).lower()
            if kind in ('listing', 'marker', 'vcard'):
                t = re.search(r'type\s*=\s*(\w+)', m.group(2))
                kind = t.group(1) if t else 'other'
            c = _CAT.get(map_cat(kind), 'sight')
            if c in ('lodging', 'transit'):
                continue
            cats[c] = cats.get(c, 0) + 1; n += 1
        timed = P.parse_text('\n'.join(_clean(x) for x in _WIKI[3].sub(' ', body).splitlines()), gaz)
        d = timed['days'][0] if timed and timed['days'] and timed['days'][0]['clock'] else ({'clock': False, 'stops': n, 'cats': cats} if 2 <= n <= 20 else None)
        if d:
            d['idx'] = no
            out.append(d)
    if not out:
        return None
    total = max(d[2] for _, d in days)
    for d in out:
        d['of'] = total
    city, cc = cities.find(title, text)
    return {'city': city, 'cc': cc, 'type': P.trip_type(title), 'days': out}


def wv_parse(xml_stream, gaz, cities):
    out, title = [], None
    for ev, el in ET.iterparse(xml_stream, events=('end',)):
        tag = el.tag.rsplit('}', 1)[-1]
        if tag == 'title':
            title = el.text
        elif tag == 'text' and el.text and title and ':' not in title:
            s = wv_sample(title, el.text, gaz, cities)
            if s:
                out.append(s)
        if tag == 'page':
            el.clear()
    return out


def wikivoyage_dump(src, http, gaz, cities, state, shard):
    """shard = 언어. 덤프가 그대로면 None(앞 표본을 다시 씀). 덤프 본문은 메모리에서만 읽는다."""
    base = f'https://dumps.wikimedia.org/{shard}wikivoyage/latest/{shard}wikivoyage-latest-'
    import hashlib
    ver = hashlib.sha1(http.get(base + 'sha1sums.txt')).hexdigest()[:12]
    if state.get('ver') == ver:
        return None
    samples = wv_parse(bz2.BZ2File(io.BytesIO(http.get(base + 'pages-articles.xml.bz2'))), gaz, cities)
    state['ver'] = ver
    return samples


class _Text(html.parser.HTMLParser):
    BLOCK = {'p', 'br', 'li', 'tr', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'dt', 'dd', 'section', 'article', 'td', 'th'}
    DROP = {'script', 'style', 'noscript', 'template', 'svg'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.links, self.title, self.optout, self._drop, self._in_title = [], [], '', False, 0, False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.DROP:
            self._drop += 1
        elif tag in self.BLOCK:
            self.out.append('\n')
        elif tag == 'title':
            self._in_title = True
        elif tag == 'a' and a.get('href'):
            self.links.append(a['href'])
        elif tag == 'meta':
            name, content = (a.get('name') or '').lower(), (a.get('content') or '').lower()
            if (name in ('robots', 'googlebot', 'bingbot') and re.search(r'noai|noimageai|noml|none', content)) or (name == 'tdm-reservation' and content.strip() == '1'):
                self.optout = True

    def handle_endtag(self, tag):
        if tag in self.DROP and self._drop:
            self._drop -= 1
        elif tag in self.BLOCK:
            self.out.append('\n')
        elif tag == 'title':
            self._in_title = False

    def handle_data(self, d):
        if self._in_title:
            self.title += d
        elif not self._drop:
            self.out.append(d)


def page_sample(body, gaz, cities):
    """HTML 1쪽 → (표본 또는 None, 링크들, 거부 표시 여부). 글은 여기서 버린다."""
    p = _Text()
    p.feed(body.decode('utf-8', 'replace'))
    if p.optout:
        return None, [], True
    text = ''.join(p.out)
    r = P.parse_text(text, gaz)
    if not r:
        return None, p.links, False
    city, cc = cities.find(p.title, text)
    return {'city': city, 'cc': cc, 'type': P.trip_type(p.title + '\n' + text[:3000]), 'days': r['days']}, p.links, False


def site(src, http, gaz, cities, state, shard=None):
    """robots 허용 사이트 — 씨앗 쪽에서 같은 사이트의 일정 쪽(path_re)만 max_pages 까지. robots·간격·한도는 common.Http 가 요청마다 지킨다."""
    if not usable(src):
        raise Blocked('terms not checked')
    hosts = {host_of(u) for u in src['seeds']}
    for h in hosts:  # TDM 유보 선언(tdmrep)이 있으면 그 사이트는 읽지 않는다
        try:
            http.get(f'https://{h}/.well-known/tdmrep.json')
            found = True
        except (urllib.error.HTTPError, IOError, Blocked):
            found = False  # 없음(404)·robots 가 막음 — 뒤쪽은 쪽마다 robots 가 다시 판정
        if found:
            raise Blocked('tdm reservation')
    want = re.compile(src.get('path_re') or '.')
    queue, seen, samples = list(src['seeds']), set(), []
    while queue and len(seen) < int(src.get('max_pages', 40)):
        u = queue.pop(0)
        if u in seen or forbidden(u) or host_of(u) not in hosts:
            continue
        seen.add(u)
        try:
            s, links, opt = page_sample(http.get(u, accept='text/html'), gaz, cities)
        except Stop:
            break
        except (Blocked, IOError, urllib.error.HTTPError):
            continue
        if s:
            samples.append(s)
        for ln in links:
            v = urllib.parse.urljoin(u, ln).split('#')[0]
            if v not in seen and host_of(v) in hosts and want.search(urllib.parse.urlsplit(v).path):
                queue.append(v)
    return samples


FETCHERS = {'wikivoyage_dump': wikivoyage_dump, 'site': site}  # youtube_api·search_api 는 일부러 없음
