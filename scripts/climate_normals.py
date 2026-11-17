#!/usr/bin/env python3
"""도시별 월별 평년값(1991–2020) 표 만들기 — NASA POWER 일별 API(키 없음)에서 계산.

쓰는 법:  python3 scripts/climate_normals.py --cities sources/climate_cities.csv --out climate/out [--limit N] [--dart]
출력:     <out>/climate_normals.json (작은 정적 파일) · <out>/ATTRIBUTION.txt · (--dart) <out>/tl_p7_data.dart
이어받기: <out>/cache/<id>.json (도시 하나의 계산 결과만 — 원본 일별 자료는 디스크에 남기지 않는다)

자료: NASA POWER Daily API, community=AG, T2M_MAX·T2M_MIN·PRECTOTCORR (MERRA-2 격자, 위도 0.5° × 경도 0.625°).
      값은 격자 평균이다(관측소 값이 아니다). 표기: "NASA POWER (MERRA-2), 1991–2020 평균".
계산(월마다, 30년):
  tx = 일 최고기온의 평균 · tn = 일 최저기온의 평균 (°C, 소수 1자리)
  pr = 월 강수량의 연평균 (mm, 정수) = 그 달 일 강수량 합 ÷ 자료가 있는 해 수
  wd = 일 강수량 1.0 mm 이상인 날 수의 연평균 (소수 1자리) — 격자 평균이라 관측소보다 많이 나온다(docs/CLIMATE.md). 앱 표시는 기본 끔.
빠진 날(-999)은 빼고 세며, 한 달에 자료가 90% 미만인 해는 그 달 계산에서 뺀다. 남은 해가 24년 미만이면 그 달은 null.
"""
import argparse, calendar, csv, json, os, sys, time, urllib.request

API = 'https://power.larc.nasa.gov/api/temporal/daily/point'
PARAMS = 'T2M_MAX,T2M_MIN,PRECTOTCORR'
Y0, Y1 = 1991, 2020
FILL = -999.0
WET_MM = 1.0
MIN_YEARS = 24
MIN_DAY_RATIO = 0.9
UA = 'triproutie-pipeline climate-normals (github.com/joon0515/triproutie-pipeline)'
MAX_BYTES = 400_000          # 출력 JSON 한도(작은 정적 파일)
SOURCE = {
    'name': 'NASA POWER (MERRA-2)',
    'api': 'POWER Daily API',
    'url': 'https://power.larc.nasa.gov/',
    'license': 'No restrictions on use; cite NASA POWER (listed as CC BY 4.0 on the Registry of Open Data on AWS)',
    'license_url': 'https://registry.opendata.aws/nasa-power/',
    'grid': '0.5 x 0.625 deg grid average (not a station observation)',
    'cities': 'GeoNames (CC BY 4.0)',
}


def url_for(lat, lon):
    return (f'{API}?parameters={PARAMS}&community=AG&longitude={lon:.2f}&latitude={lat:.2f}'
            f'&start={Y0}0101&end={Y1}1231&format=JSON')


def http_get(url, timeout=90):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def aggregate(param):
    """param = {'T2M_MAX': {'19910101': v, ...}, 'T2M_MIN': {...}, 'PRECTOTCORR': {...}} → {'tx','tn','pr','wd'} 각 12칸(없으면 None)."""
    tmax, tmin, prec = param['T2M_MAX'], param['T2M_MIN'], param['PRECTOTCORR']
    out = {'tx': [], 'tn': [], 'pr': [], 'wd': []}
    for m in range(1, 13):
        sx = nx = sn = nn = 0.0
        psum, wsum, pyears = 0.0, 0.0, 0
        for y in range(Y0, Y1 + 1):
            nd = calendar.monthrange(y, m)[1]
            got, ps, ws = 0, 0.0, 0
            for d in range(1, nd + 1):
                k = f'{y}{m:02d}{d:02d}'
                a, b, p = tmax.get(k), tmin.get(k), prec.get(k)
                if a is not None and a > FILL + 1:
                    sx += a; nx += 1
                if b is not None and b > FILL + 1:
                    sn += b; nn += 1
                if p is not None and p > FILL + 1:
                    got += 1; ps += p; ws += 1 if p >= WET_MM else 0
            if got >= nd * MIN_DAY_RATIO:
                psum += ps * nd / got; wsum += ws * nd / got; pyears += 1   # 빠진 날만큼 비례 보정(달 길이 기준)
        need = MIN_YEARS * 28 * MIN_DAY_RATIO
        out['tx'].append(round(sx / nx, 1) if nx >= need else None)
        out['tn'].append(round(sn / nn, 1) if nn >= need else None)
        out['pr'].append(int(round(psum / pyears)) if pyears >= MIN_YEARS else None)
        out['wd'].append(round(wsum / pyears, 1) if pyears >= MIN_YEARS else None)
    return out


def check(row):
    """말이 안 되는 값이면 이유를 돌려준다(없으면 '')."""
    for k in ('tx', 'tn', 'pr', 'wd'):
        if len(row[k]) != 12:
            return f'{k} 12칸 아님'
    if all(v is None for v in row['tx']) or all(v is None for v in row['pr']):
        return '자료 없음'
    for m in range(12):
        tx, tn, pr, wd = row['tx'][m], row['tn'][m], row['pr'][m], row['wd'][m]
        if tx is not None and tn is not None and tx < tn:
            return f'{m + 1}월 최고<최저'
        if tx is not None and not -60 <= tx <= 60 or tn is not None and not -75 <= tn <= 50:
            return f'{m + 1}월 기온 범위 밖'
        if pr is not None and not 0 <= pr <= 3000 or wd is not None and not 0 <= wd <= 31:
            return f'{m + 1}월 강수 범위 밖'
    return ''


def city_normals(city, fetch=http_get, tries=3, sleep=time.sleep):
    """도시 하나: 받아서 계산. 실패하면 예외."""
    last = None
    for i in range(tries):
        try:
            d = json.loads(fetch(url_for(city['lat'], city['lon'])))
            row = aggregate(d['properties']['parameter'])
            bad = check(row)
            if bad:
                raise ValueError(bad)
            return row
        except ValueError:
            raise
        except Exception as e:          # 네트워크·한도(429)·형식
            last = e
            sleep(5 * (i + 1))
    raise RuntimeError(f'받기 실패: {type(last).__name__}')


def read_cities(path):
    with open(path, encoding='utf-8') as fh:
        return [{'id': int(r['id']), 'name': r['name'], 'cc': r['cc'], 'lat': float(r['lat']), 'lon': float(r['lon'])}
                for r in csv.DictReader(fh)]


def build(cities, out_dir, fetch=http_get, pause=1.0, sleep=time.sleep, log=print, max_fail=20):
    """도시마다 계산해 cache 에 두고(이어받기), 전부 모아 JSON 을 쓴다. 돌려주는 값: (된 수, 실패 목록)."""
    cache = os.path.join(out_dir, 'cache'); os.makedirs(cache, exist_ok=True)
    rows, failed = [], []
    for c in cities:
        p = os.path.join(cache, f"{c['id']}.json")
        row = None
        if os.path.exists(p):
            try:
                with open(p, encoding='utf-8') as fh:
                    row = json.load(fh)
                if row.get('at') != [c['lat'], c['lon']] or check(row):
                    row = None
            except Exception:
                row = None
        if row is None:
            if len(failed) >= max_fail:
                failed.append(c['id']); continue
            try:
                row = city_normals(c, fetch=fetch, sleep=sleep)
                row['at'] = [c['lat'], c['lon']]
                with open(p + '.tmp', 'w', encoding='utf-8') as fh:
                    json.dump(row, fh)
                os.replace(p + '.tmp', p)
                sleep(pause)
            except Exception as e:
                failed.append(c['id']); log(f"실패 {c['id']} {c['name']}: {e}"); continue
        rows.append({'id': c['id'], 'n': c['name'], 'cc': c['cc'], 'la': c['lat'], 'lo': c['lon'],
                     'tx': row['tx'], 'tn': row['tn'], 'pr': row['pr'], 'wd': row['wd']})
    return rows, failed


def to_json(rows, made):
    doc = {'v': 1, 'period': [Y0, Y1], 'made': made, 'source': SOURCE,
           'fields': {'tx': 'mean daily max degC', 'tn': 'mean daily min degC', 'pr': 'mean monthly precipitation mm',
                      'wd': f'mean days per month with >= {WET_MM} mm (grid average)'},
           'cities': rows}
    return json.dumps(doc, ensure_ascii=False, separators=(',', ':'))


def to_dart(rows, made, wet=False):
    """앱에 붙이는 작은 dart 파일. 한 줄 = id|이름|나라|위도|경도|tx×12|tn×12|pr×12[|wd×12], 기온은 ×10 정수."""
    def t10(v): return '' if v is None else str(int(round(v * 10)))
    def i(v): return '' if v is None else str(v)
    lines = []
    for r in rows:
        parts = [str(r['id']), r['n'].replace('|', ' ').replace("'", ' ').replace('\\', ' ').replace('$', ' '), r['cc'], f"{r['la']:.2f}", f"{r['lo']:.2f}",
                 ','.join(t10(v) for v in r['tx']), ','.join(t10(v) for v in r['tn']), ','.join(i(v) for v in r['pr'])]
        if wet:
            parts.append(','.join(t10(v) for v in r['wd']))
        lines.append('|'.join(parts))
    body = '\n'.join(lines)
    return ("// 만든 파일 — 손으로 고치지 않는다. triproutie-pipeline scripts/climate_normals.py\n"
            f"// 자료: NASA POWER (MERRA-2) 일별 값의 {Y0}–{Y1} 평균(격자 평균, 관측소 값 아님). 도시 좌표: GeoNames (CC BY 4.0).\n"
            f"const String kTlP7DataMade = '{made}';\n"
            f"const int kTlP7Y0 = {Y0}, kTlP7Y1 = {Y1};\n"
            f"const bool kTlP7HasWet = {'true' if wet else 'false'};\n"
            f"const String kTlP7Data = '''\n{body}\n''';\n")


ATTRIB = (f"Monthly climate normals {Y0}-{Y1} computed by Trip Routie from NASA POWER daily data (MERRA-2), "
          "https://power.larc.nasa.gov/ - grid averages, not station observations.\n"
          "These data were obtained from the NASA Langley Research Center (LaRC) POWER Project funded through the NASA Earth Science/Applied Science Program.\n"
          "NASA does not endorse this product.\nCity names and coordinates: GeoNames (https://www.geonames.org/), CC BY 4.0.\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--cities', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--limit', type=int, default=0); ap.add_argument('--pause', type=float, default=1.0)
    ap.add_argument('--dart', action='store_true'); ap.add_argument('--wet', action='store_true', help='dart 파일에 비 온 날 수(wd)도 넣기')
    ap.add_argument('--allow-missing', type=int, default=10, help='이 수보다 많이 실패하면 오류로 끝낸다')
    a = ap.parse_args(argv)
    cities = read_cities(a.cities)
    if a.limit:
        cities = cities[:a.limit]
    rows, failed = build(cities, a.out, pause=a.pause)
    made = time.strftime('%Y-%m-%d', time.gmtime())
    js = to_json(rows, made)
    if len(js.encode('utf-8')) > MAX_BYTES:
        print(f'출력이 한도({MAX_BYTES}바이트)를 넘음: {len(js.encode("utf-8"))}'); return 2
    with open(os.path.join(a.out, 'climate_normals.json'), 'w', encoding='utf-8') as fh:
        fh.write(js)
    with open(os.path.join(a.out, 'ATTRIBUTION.txt'), 'w', encoding='utf-8') as fh:
        fh.write(ATTRIB)
    if a.dart:
        with open(os.path.join(a.out, 'tl_p7_data.dart'), 'w', encoding='utf-8') as fh:
            fh.write(to_dart(rows, made, wet=a.wet))
    print(f'도시 {len(rows)}/{len(cities)} · 실패 {len(failed)} · JSON {len(js.encode("utf-8"))}바이트')
    return 1 if len(failed) > a.allow_missing else 0


if __name__ == '__main__':
    sys.exit(main())
