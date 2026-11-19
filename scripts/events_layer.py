#!/usr/bin/env python3
"""행사·축제·팝업 층(events-1) — 공식·공공 자료만. (2026-11-20, 256기) 문서 = docs/SHOP_EVENTS.md

  python3 scripts/events_layer.py taiwan --zip <Event-json.zip> --out <폴더>      # 대만 觀光署 활동(등록부 status 가 active 일 때만 워크플로가 부름)
  python3 scripts/events_layer.py official --rows <공식 사이트 수집 행 JSON(.gz)> --out <폴더>

출처(사실 값만 — 이름·기간·좌표·주소·공식 쪽 주소. 소개 글은 싣지 않는다):
  kto      한국관광공사 TourAPI 4.0 searchFestival2 — tourapi_daily.py 가 이 파일의 kto_rows·doc·write 로 events_kto.json.gz 를 쓴다.
  tw_event 交通部觀光署 觀光資訊資料庫 活動(EventList.json) — 등록부 taiwan_events(라이선스 확인 전 candidate).
  official 운영사·시설 공식 사이트의 schema.org Event(collectors.crawl_site 가 robots 를 지키며 이미 모으는 'ev') — 새 수집기 없음.
행 = {id: {n 이름, s 시작 YYYYMMDD, e 끝 YYYYMMDD, la, lo, a 주소?, k festival|event|popup, src, u 공식 쪽?, cc}}
  k = popup: 이름에 팝업 낱말(POPUP_RE)이 있는 것. 사용자 제보 팝업은 앱 검수 화면에서 승인된 것만 앱이 따로 보인다(이 층과 섞지 않음).
끝난 행사(끝 < 오늘)는 싣지 않는다. 앱은 여행 날짜와 겹치는 것만 보인다(tl_shop256 tlShop256Overlap).
"""
import argparse, datetime, gzip, io, json, os, re, sys, zipfile

VERSION = 'events-1.0'
POPUP_RE = re.compile(r'pop[\s\-]?up|팝업|ポップアップ|快閃|快闪|期間限定\s*ストア|限定店', re.I)
FEST_RE = re.compile(r'축제|[가-힣]제$|festival|祭|節|节|フェス|fiesta|fête|fest\b', re.I)
LICENSES = {
    'tourapi': {'license': 'KOGL (사실 값)', 'credit': '한국관광공사 TourAPI 4.0', 'url': 'https://www.data.go.kr/data/15101578/openapi.do'},
    'tw_event': {'license': 'OGDL-Taiwan-1.0', 'credit': '交通部觀光署 觀光資訊資料庫 (政府資料開放授權條款-第1版)', 'url': 'https://data.gov.tw/dataset/7778'},
    'official': {'license': 'facts', 'credit': '각 운영사·시설 공식 사이트(행마다 u)', 'url': None},
}


def today():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y%m%d')


def ymd(v):
    """'2026-11-20T…' · '20261120' · '2026/11/20' → '20261120' 또는 ''."""
    m = re.match(r'\s*(\d{4})[-/.]?(\d{1,2})[-/.]?(\d{1,2})', str(v or ''))
    return f'{int(m.group(1)):04d}{int(m.group(2)):02d}{int(m.group(3)):02d}' if m else ''


def kind_of(name, default='event'):
    if POPUP_RE.search(name or ''):
        return 'popup'
    return 'festival' if FEST_RE.search(name or '') else default


def _row(n, s, e, la, lo, src, cc, a=None, u=None, k=None, now=None):
    s, e = ymd(s), ymd(e) or ymd(s)
    if not n or not s or e < s or e < (now or today()):
        return None
    r = {'n': str(n)[:80], 's': s, 'e': e, 'k': k or kind_of(n), 'src': src, 'cc': cc}
    try:
        if la is not None and lo is not None and -90 <= float(la) <= 90 and -180 <= float(lo) <= 180 and (float(la), float(lo)) != (0.0, 0.0):
            r['la'], r['lo'] = round(float(la), 5), round(float(lo), 5)
    except (TypeError, ValueError):
        pass
    if a:
        r['a'] = str(a)[:60]
    if u and str(u).startswith(('http://', 'https://')):
        r['u'] = str(u)[:300]
    return r


def kto_rows(fest, now=None):
    """tourapi_daily store/festivals.json({cid: 카드}) → 행. TourAPI 행사(contentTypeId 15)는 모두 festival 로 둔다(이름에 팝업이 있으면 popup)."""
    out = {}
    for cid, c in (fest or {}).items():
        r = _row(c.get('name'), c.get('start'), c.get('end'), c.get('lat'), c.get('lon'), 'tourapi', 'KR', a=c.get('addr'),
                 k=kind_of(c.get('name'), 'festival'), now=now)
        if r:
            out['kto:' + str(cid)] = r
    return out


def taiwan_rows(zbytes, now=None):
    """EventList.json(v2.0) → 행. 상태가 취소·연기면 뺀다."""
    out = {}
    z = zipfile.ZipFile(io.BytesIO(zbytes))
    for nm in z.namelist():
        if not nm.lower().endswith('.json'):
            continue
        j = json.loads(z.read(nm).decode('utf-8-sig', 'replace'))
        for d in (j.get('Events') or []) if isinstance(j, dict) else []:
            if not isinstance(d, dict) or str(d.get('EventStatus') or '') in ('EventCancelled', 'EventPostponed'):
                continue
            pa = d.get('PostalAddress') if isinstance(d.get('PostalAddress'), dict) else {}
            addr = ''.join(str(pa.get(k) or '') for k in ('City', 'Town', 'StreetAddress'))
            r = _row(d.get('EventName'), d.get('StartDateTime'), d.get('EndDateTime'), d.get('PositionLat'), d.get('PositionLon'), 'tw_event', 'TW',
                     a=addr or None, u=d.get('WebsiteURL'), now=now)
            if r and d.get('EventID'):
                out['tw:' + str(d['EventID'])[:60]] = r
    return out


def official_rows(rows, now=None):
    """공식 사이트 수집 행(collectors.crawl 의 place 행 — extra.ev 또는 ev) → 행. 장소 좌표·나라를 행사에 물려준다."""
    out = {}
    for p in rows or []:
        evs = p.get('ev') or (p.get('extra') or {}).get('ev') or []
        cc = p.get('cc') or (p.get('extra') or {}).get('cc') or ''
        for i, e in enumerate(evs[:50]):
            r = _row(e.get('name'), e.get('start'), e.get('end'), p.get('lat'), p.get('lon'), 'official', cc, a=e.get('where'), u=e.get('page'), now=now)
            if r:
                out[f"off:{p.get('wd') or p.get('id')}:{i}"] = r
    return out


def doc(cc, src, rows):
    return {'schema': 'events-1', 'cc': cc, 'src': src, 'built': today()[:4] + '-' + today()[4:6] + '-' + today()[6:], 'version': VERSION,
            'licenses': {src: LICENSES[src]}, 'credit': LICENSES[src]['credit'],
            'kinds': {k: sum(1 for r in rows.values() if r['k'] == k) for k in ('festival', 'event', 'popup')}, 'rows': rows}


def write(path, obj):
    with open(path + '.tmp', 'wb') as f:
        f.write(gzip.compress(json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode(), 6))
    os.replace(path + '.tmp', path)


def _load(p):
    b = open(p, 'rb').read()
    return json.loads(gzip.decompress(b) if p.endswith('.gz') else b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['taiwan', 'official'])
    ap.add_argument('--zip')
    ap.add_argument('--rows')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    if a.cmd == 'taiwan':
        rows = taiwan_rows(open(a.zip, 'rb').read())
        write(os.path.join(a.out, 'events_tw.json.gz'), doc('TW', 'tw_event', rows))
    else:
        src = _load(a.rows)
        rows = official_rows(src.get('rows') if isinstance(src, dict) else src)
        write(os.path.join(a.out, 'events_official.json.gz'), doc('', 'official', rows))
    k = {x: sum(1 for r in rows.values() if r['k'] == x) for x in ('festival', 'event', 'popup')}
    print(f'events {a.cmd}: {len(rows)} {k}')  # 개수만
    return 0


if __name__ == '__main__':
    sys.exit(main())
