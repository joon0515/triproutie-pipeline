#!/usr/bin/env python3
"""기후 평년값을 만들 도시 목록(sources/climate_cities.csv)을 GeoNames cities15000 에서 고른다.
쓰는 법: python3 scripts/climate_cities.py <cities15000.txt> > sources/climate_cities.csv
GeoNames: CC BY 4.0 (https://download.geonames.org/export/dump/readme.txt) — 출처 표기 "GeoNames".
고르는 기준(사람이 정한 규칙, 추측 없음): 수도 전부 + 인구 기준(가까운 여행권은 낮게) + 손으로 넣은 여행 도시.
"""
import csv, sys

NEAR = {'JP': 200_000, 'KR': 200_000, 'TW': 200_000, 'HK': 0, 'MO': 0, 'SG': 0}          # 일본·한국·대만권
SEA = {c: 300_000 for c in ('TH', 'VN', 'MY', 'PH', 'ID', 'KH', 'LA', 'MM')}               # 동남아
WORLD_POP = 1_500_000
# 인구는 작지만 여행지로 자주 찾는 곳(이름은 GeoNames asciiname, 나라 코드)
EXTRA = {('Chiang Mai', 'TH'), ('Phuket', 'TH'), ('Da Lat', 'VN'), ('Siem Reap', 'KH'), ('Luang Prabang', 'LA'),
         ('Takayama', 'JP'), ('Hakodate', 'JP'), ('Otaru', 'JP'), ('Beppu', 'JP'), ('Ishigaki', 'JP'), ('Kamakura', 'JP'), ('Nikko', 'JP'),
         ('Gyeongju', 'KR'), ('Sokcho', 'KR'), ('Gangneung', 'KR'), ('Yeosu', 'KR'), ('Jeonju', 'KR'), ('Seogwipo', 'KR'),
         ('Hualien City', 'TW'), ('Venice', 'IT'), ('Florence', 'IT'), ('Nice', 'FR'), ('Honolulu', 'US'), ('Las Vegas', 'US'),
         ('Cairns', 'AU'), ('Gold Coast', 'AU'), ('Denpasar', 'ID'), ('Kota Kinabalu', 'MY'), ('George Town', 'MY'),
         ('Cebu City', 'PH'), ('Zurich', 'CH'), ('Salzburg', 'AT'), ('Granada', 'ES'), ('Seville', 'ES'),
         ('Porto', 'PT'), ('Edinburgh', 'GB'), ('Dubrovnik', 'HR'), ('Cancun', 'MX'), ('Cusco', 'PE'), ('Marrakesh', 'MA')}


def pick(rows):
    """rows: GeoNames 탭 구분 줄들 → [(id, name, cc, lat, lon)] (id 오름차순, 같은 0.25도 칸의 중복은 인구 큰 쪽)."""
    best = {}
    for ln in rows:
        f = ln.rstrip('\n').split('\t')
        if len(f) < 15:
            continue
        gid, name, lat, lon, fcode, cc, pop = f[0], f[2], float(f[4]), float(f[5]), f[7], f[8], int(f[14] or 0)
        ok = fcode == 'PPLC' or (name, cc) in EXTRA or pop >= WORLD_POP \
            or (cc in NEAR and pop >= NEAR[cc]) or (cc in SEA and pop >= SEA[cc])
        if not ok or fcode.startswith('PPLX'):
            continue
        cell = (round(lat * 4), round(lon * 4))
        if cell not in best or pop > best[cell][0]:
            best[cell] = (pop, int(gid), name, cc, round(lat, 2), round(lon, 2))
    return sorted((b[1], b[2], b[3], b[4], b[5]) for b in best.values())


if __name__ == '__main__':
    w = csv.writer(sys.stdout, lineterminator='\n')
    w.writerow(['id', 'name', 'cc', 'lat', 'lon'])
    with open(sys.argv[1], encoding='utf-8') as fh:
        for r in pick(fh):
            w.writerow(r)
