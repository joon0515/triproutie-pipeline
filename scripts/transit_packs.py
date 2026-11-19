#!/usr/bin/env python3
"""257기 대중교통 팩 — 앱이 오프라인·키 없이 쓰는 요금표와 시간표(GTFS 정적·ODPT 공개)를 만든다.

  python3 scripts/transit_packs.py fares --out fares.json
  python3 scripts/transit_packs.py timetable --city tokyo --gtfs a.zip [--gtfs b.zip] [--odpt-toei] --out tokyo.json

저장 위치(워크플로 transit-packs.yml): places/v1/transit/fares.json · places/v1/transit/tt/<city>.json.gz
  — storage.rules 의 places/{ver}/**(로그인 사용자 읽기) 그대로라 규칙 변경 없음.

원칙
- 공식 개방 자료만: 운영사·당국 공개 파일/API(키 없는 것) + 운영사 공식 요금 안내 숫자(출처 URL·기준일을 같이 적음).
- 숫자를 손으로 적은 곳(STATIC_RULES)은 공식 페이지에서 확인한 값만, 확인 날짜를 'checked' 에 적는다.
- 시간표는 GTFS calendar.txt 요일만 쓴다(calendar_dates 예외일은 반영하지 않음 → 앱이 '시간표 기준' 이라고 표시).
"""
import argparse
import csv
import gzip
import io
import json
import sys
import urllib.request
import zipfile

CHECKED = '2026-11-20'
UA = 'triproutie-pipeline/transit-packs'

# ---------------------------------------------------------------------------
# 1. 손으로 적은 공식 요금 규칙(출처·확인일 포함)
# ---------------------------------------------------------------------------
STATIC_RULES = {
    'seoul': {
        'cur': 'KRW', 'card': 'T-money',
        'rule': {'type': 'distance_kr', 'subway': 1550, 'bus': 1500, 'base_km': 10, 'step_km': 5, 'step_add': 100, 'transfer_min': 30, 'transfer_min_night': 60},
        'passes': [
            {'id': 'climate_1d', 'days': 1, 'price': 5000, 'card_fee': 3000},
            {'id': 'climate_2d', 'days': 2, 'price': 8000, 'card_fee': 3000},
            {'id': 'climate_3d', 'days': 3, 'price': 10000, 'card_fee': 3000},
            {'id': 'climate_5d', 'days': 5, 'price': 15000, 'card_fee': 3000},
            {'id': 'climate_7d', 'days': 7, 'price': 20000, 'card_fee': 3000},
        ],
        'pass_scope': 'seoul_only',  # 서울 시내 지하철·서울 버스(광역·신분당선 제외)
        'src': [
            {'what': 'rule', 'url': 'https://news.seoul.go.kr/traffic/traffic_price', 'checked': CHECKED, 'note': '통합환승 기본 10km·5km마다 100원·30분(21~07시 60분), 시내버스 1,500원'},
            {'what': 'subway', 'url': 'https://www.etoday.co.kr/news/view/2480312', 'checked': CHECKED, 'note': '2025-06-28 수도권 지하철 기본 1,550원(교통카드)'},
            {'what': 'passes', 'url': 'https://mediahub.seoul.go.kr/archives/2012397', 'checked': CHECKED, 'note': '기후동행카드 단기권 1·2·3·5·7일, 카드 3,000원 별도'},
        ],
    },
    'nyc': {
        'cur': 'USD', 'card': 'OMNY',
        'rule': {'type': 'flat', 'fare': 3.00},
        'cap': {'rides': 12, 'days': 7, 'price': 35.00},
        'src': [{'what': 'rule', 'url': 'https://www.mta.info/press-release/mta-board-adopts-fare-and-toll-increases-take-effect-january-2026', 'checked': CHECKED, 'note': '2026-01 기본 $3, 7일 12회 뒤 무료($35 상한)'}],
    },
    'seattle': {
        'cur': 'USD', 'card': 'ORCA',
        'rule': {'type': 'flat', 'fare': 3.00},
        'src': [
            {'what': 'link', 'url': 'https://soundtransit.org/get-to-know-us/news-events/news-releases/link-1-2-line-fares-changing-august-30', 'checked': CHECKED, 'note': 'Link 1·2 Line 성인 $3 균일(2025-08-30)'},
            {'what': 'bus', 'url': 'https://kingcountymetro.blog/2025/07/28/king-county-metro-water-taxi-and-seattle-streetcar-adult-fares-change-sept-1-2025/', 'checked': CHECKED, 'note': 'Metro 버스·스트리트카 성인 $3(2025-09-01)'},
        ],
    },
    'paris': {
        'cur': 'EUR', 'card': 'Navigo Easy',
        'rule': {'type': 'flat_mode', 'rail': 2.55, 'bus': 2.05, 'airport': 14.00},
        'passes': [
            {'id': 'navigo_jour', 'days': 1, 'price': 12.30, 'note': 'zones 1-5, airports excluded'},
            {'id': 'paris_visite_1', 'days': 1, 'price': 30.60},
            {'id': 'paris_visite_2', 'days': 2, 'price': 45.40},
            {'id': 'paris_visite_3', 'days': 3, 'price': 63.80},
            {'id': 'paris_visite_5', 'days': 5, 'price': 78.00},
            {'id': 'navigo_semaine', 'days': 7, 'price': 32.40, 'note': 'Monday-Sunday'},
        ],
        'src': [{'what': 'all', 'url': 'https://www.iledefrance-mobilites.fr/tarifs-titre-de-transport-en-commun-2026', 'checked': CHECKED, 'note': '2026-01-01 시행'}],
    },
    'london': {
        'cur': 'GBP', 'card': 'contactless/Oyster',
        'rule': {'type': 'london', 'bus': 1.85, 'bus_cap': 5.55, 'cap_z12': 8.90, 'rail': 'tfl_api'},
        'src': [
            {'what': 'bus', 'url': 'https://www.london.gov.uk/md3517-july-2026-fare-changes', 'checked': CHECKED, 'note': '버스 £1.85·하루 상한 £5.55(2026-11-01부터)'},
            {'what': 'cap', 'url': 'https://tfl.gov.uk/campaign/new-fares', 'checked': CHECKED, 'note': '1-2존 하루 상한 £8.90, 2027년 3월까지 동결(TfL 보도)'},
        ],
    },
    'busan': {
        'cur': 'KRW', 'card': 'T-money/Cashbee',
        'rule': {'type': 'link_only'},
        'src': [{'what': 'rule', 'url': 'https://www.data.go.kr/data/3057225/fileData.do', 'checked': CHECKED, 'note': '부산교통공사 운임정보(파일) — 이 팩에는 숫자를 넣지 않음'}],
    },
}

# 공식 개방 파일·API(키 없음)
MTR_FARES = 'https://opendata.mtr.com.hk/data/mtr_lines_fares.csv'
SG_FARES = 'https://data.gov.sg/api/action/datastore_search?resource_id=d_2ca29f673b89e0cfddbb7c74516fa4d3&limit=500'
ODPT_PUBLIC = 'https://api-public.odpt.org/api/v4/'


def get(url, timeout=60):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------------------
# 2. 요금 — 공개 파일 해석(순수 함수, 시험 대상)
# ---------------------------------------------------------------------------
def mtr_pairs(csv_bytes):
    """MTR 공식 요금 CSV → {'stations': [[id, name]], 'fares': {src_id: {dst_id: 성인 옥토퍼스 요금}}}."""
    rows = csv.DictReader(io.StringIO(csv_bytes.decode('utf-8-sig')))
    st, fares = {}, {}
    for r in rows:
        a, b = r['SRC_STATION_ID'].strip(), r['DEST_STATION_ID'].strip()
        st[a] = r['SRC_STATION_NAME'].strip()
        st[b] = r['DEST_STATION_NAME'].strip()
        if a == b:
            continue
        try:
            f = float(r['OCT_ADT_FARE'])
        except ValueError:
            continue
        fares.setdefault(a, {})[b] = f
    return {'stations': sorted([[k, v] for k, v in st.items()], key=lambda x: int(x[0]) if x[0].isdigit() else 0), 'fares': fares}


def sg_bands(js):
    """data.gov.sg 'Fares for MRT and LRT' → [[상한 km, 센트]] (성인 카드, 평소 시간)."""
    recs = js['result']['records']
    out = []
    for r in recs:
        if r.get('fare_type') != 'Adult card fare' or r.get('applicable_time') != 'All other timings':
            continue
        d = r['distance'].replace('km', '').strip()
        if d.lower().startswith('up to'):
            hi = float(d.split()[-1])
        elif d.lower().startswith('over'):
            hi = 999.0
        else:
            hi = float(d.split('-')[-1])
        out.append([hi, int(float(r['fare_per_ride']))])
    out.sort()
    return out


def odpt_fares(items):
    """ODPT odpt:RailwayFare(도영) → {from_station: {to_station: IC 요금}}."""
    out = {}
    for x in items:
        a, b, f = x.get('odpt:fromStation'), x.get('odpt:toStation'), x.get('odpt:icCardFare')
        if a and b and isinstance(f, (int, float)):
            out.setdefault(a, {})[b] = f
    return out


def odpt_stations(items):
    """ODPT odpt:Station → [[id, ja, en, lat, lon]]."""
    out = []
    for x in items:
        lat, lon = x.get('geo:lat'), x.get('geo:long')
        if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float))):
            continue
        t = x.get('odpt:stationTitle') or {}
        out.append([x.get('owl:sameAs'), x.get('dc:title') or t.get('ja', ''), t.get('en', ''), round(lat, 6), round(lon, 6)])
    return sorted(out)


def build_fares(fetch=get):
    cities = json.loads(json.dumps(STATIC_RULES))
    try:
        cities['hongkong'] = {'cur': 'HKD', 'card': 'Octopus', 'rule': {'type': 'pair', **mtr_pairs(fetch(MTR_FARES))},
                              'src': [{'what': 'mtr', 'url': MTR_FARES, 'checked': CHECKED, 'note': 'MTR 공식 요금(DATA.GOV.HK), 성인 옥토퍼스'}]}
    except Exception as e:  # 한 도시 실패는 그 도시만 뺀다
        print('hk fares fail', e, file=sys.stderr)
    try:
        cities['singapore'] = {'cur': 'SGD', 'card': 'contactless/SimplyGo', 'rule': {'type': 'distance_table', 'cents': sg_bands(json.loads(fetch(SG_FARES)))},
                               'src': [{'what': 'mrt', 'url': 'https://data.gov.sg/datasets/d_2ca29f673b89e0cfddbb7c74516fa4d3/view', 'checked': CHECKED, 'note': 'LTA 성인 카드 요금, 2025-12-27 시행'}]}
    except Exception as e:
        print('sg fares fail', e, file=sys.stderr)
    try:
        sts = json.loads(fetch(ODPT_PUBLIC + 'odpt:Station?odpt:operator=odpt.Operator:Toei'))
        st = odpt_stations(sts)
        fares = {}
        for sid in [s[0] for s in st if s[0] and 'Toei.Arakawa' not in s[0]]:
            fares.update(odpt_fares(json.loads(fetch(ODPT_PUBLIC + 'odpt:RailwayFare?odpt:operator=odpt.Operator:Toei&odpt:fromStation=' + sid))))
        cities['tokyo_toei'] = {'cur': 'JPY', 'card': 'Suica/PASMO', 'rule': {'type': 'pair_geo', 'stations': st, 'fares': fares},
                                'src': [{'what': 'toei', 'url': 'https://ckan.odpt.org/dataset/r_fare-toei', 'checked': CHECKED, 'note': '東京都交通局 公共交通オープンデータ(CC BY 4.0), IC 運賃'}],
                                'attr': '© 東京都交通局 (CC BY 4.0) / 公共交通オープンデータセンター'}
    except Exception as e:
        print('toei fares fail', e, file=sys.stderr)
    return {'v': 1, 'asof': CHECKED, 'cities': cities}


# ---------------------------------------------------------------------------
# 3. 시간표 — GTFS 정적 · ODPT 공개 역 시간표
# ---------------------------------------------------------------------------
DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']


def _hm(s):
    p = s.strip().split(':')
    return int(p[0]) * 60 + int(p[1]) if len(p) >= 2 and p[0].isdigit() else None


def gtfs_departures(zbytes, feed_id, max_stops=20000):
    """GTFS zip → (stops[[id,name,lat,lon]], deps{stop_id: [[route, headsign, daymask, [분...]]]})."""
    z = zipfile.ZipFile(io.BytesIO(zbytes))
    rd = lambda n: csv.DictReader(io.TextIOWrapper(z.open(n), encoding='utf-8-sig')) if n in z.namelist() else []
    mask = {}
    for c in rd('calendar.txt'):
        mask[c['service_id']] = sum((1 << i) for i, d in enumerate(DAYS) if c.get(d, '0').strip() == '1')
    routes = {r['route_id']: (r.get('route_short_name') or r.get('route_long_name') or '').strip() for r in rd('routes.txt')}
    trips = {}
    for t in rd('trips.txt'):
        m = mask.get(t['service_id'], 0)
        if m:
            trips[t['trip_id']] = (routes.get(t['route_id'], ''), (t.get('trip_headsign') or '').strip(), m)
    stops = {}
    for s in rd('stops.txt'):
        try:
            stops[s['stop_id']] = [f"{feed_id}:{s['stop_id']}", s.get('stop_name', '').strip(), round(float(s['stop_lat']), 6), round(float(s['stop_lon']), 6)]
        except (ValueError, KeyError):
            pass
    deps = {}
    for st in rd('stop_times.txt'):
        tr = trips.get(st['trip_id'])
        if not tr or st.get('pickup_type', '0').strip() == '1':
            continue
        m = _hm(st.get('departure_time') or st.get('arrival_time') or '')
        if m is None or st['stop_id'] not in stops:
            continue
        deps.setdefault(st['stop_id'], {}).setdefault(tr, []).append(m)
    out_stops, out_deps = [], {}
    for sid in sorted(deps)[:max_stops]:
        k = f"{feed_id}:{sid}"
        out_stops.append(stops[sid])
        out_deps[k] = [[r, h, m, sorted(set(v))] for (r, h, m), v in sorted(deps[sid].items())]
    return out_stops, out_deps


def odpt_station_timetables(items, stations):
    """ODPT odpt:StationTimetable → deps{station: [[railway, 행선, daymask, [분...]]]}. Weekday=월~금, SaturdayHoliday=토·일."""
    cal = {'odpt.Calendar:Weekday': 0b0011111, 'odpt.Calendar:SaturdayHoliday': 0b1100000, 'odpt.Calendar:Saturday': 0b0100000, 'odpt.Calendar:Holiday': 0b1000000}
    names = {s[0]: (s[2] or s[1]) for s in stations}
    deps = {}
    for x in items:
        m = cal.get(x.get('odpt:calendar'))
        sid = x.get('odpt:station')
        if not m or not sid:
            continue
        line = (x.get('odpt:railway') or '').split(':')[-1]
        rdir = (x.get('odpt:railDirection') or '').split(':')[-1]  # 행선 없는 순환선(오에도선)은 방향 이름(InnerLoop 등)
        by = {}
        for o in x.get('odpt:stationTimetableObject') or []:
            t = _hm(o.get('odpt:departureTime') or '')
            if t is None:
                continue
            if t < 180:
                t += 1440  # 0~3시 = 전날 밤 운행
            dest = (o.get('odpt:destinationStation') or [''])[0]
            by.setdefault(names.get(dest, dest.split('.')[-1]) if dest else rdir, []).append(t)
        for dest, v in by.items():
            deps.setdefault(sid, []).append([line, dest, m, sorted(set(v))])
    return deps


def build_timetable(city, gtfs=(), odpt_toei=False, fetch=get, meta=None):
    stops, deps, src = [], {}, []
    for path, info in gtfs:
        with open(path, 'rb') as f:
            s, d = gtfs_departures(f.read(), info['id'])
        stops += s
        deps.update(d)
        src.append(info)
    if odpt_toei:
        st = odpt_stations(json.loads(fetch(ODPT_PUBLIC + 'odpt:Station?odpt:operator=odpt.Operator:Toei')))
        for s in st:
            if 'Toei.Arakawa' in s[0]:
                continue
            items = json.loads(fetch(ODPT_PUBLIC + 'odpt:StationTimetable?odpt:operator=odpt.Operator:Toei&odpt:station=' + s[0]))
            d = odpt_station_timetables(items, st)
            if d:
                stops.append([s[0], s[2] or s[1], s[3], s[4]])
                deps.update(d)
        src.append({'id': 'odpt-toei', 'provider': '東京都交通局', 'license': 'CC BY 4.0', 'attr': '© 東京都交通局 (CC BY 4.0) / 公共交通オープンデータセンター'})
    return {'v': 1, 'city': city, 'asof': (meta or {}).get('asof', CHECKED), 'basis': 'weekday_calendar', 'src': src, 'stops': stops, 'deps': deps}


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('fares')
    a.add_argument('--out', required=True)
    b = sub.add_parser('timetable')
    b.add_argument('--city', required=True)
    b.add_argument('--gtfs', action='append', default=[], help='zip 경로=피드id=라이선스=표기')
    b.add_argument('--odpt-toei', action='store_true')
    b.add_argument('--out', required=True)
    o = ap.parse_args(argv)
    if o.cmd == 'fares':
        doc = build_fares()
    else:
        g = []
        for x in o.gtfs:
            p = x.split('=', 3) + ['', '', '']
            g.append((p[0], {'id': p[1] or p[0], 'license': p[2], 'attr': p[3]}))
        doc = build_timetable(o.city, g, o.odpt_toei)
    raw = json.dumps(doc, ensure_ascii=False, separators=(',', ':')).encode()
    if o.out.endswith('.gz'):
        raw = gzip.compress(raw, 9)
    with open(o.out, 'wb') as f:
        f.write(raw)
    print(f'{o.cmd} → {o.out} {len(raw):,} bytes')


if __name__ == '__main__':
    main()
