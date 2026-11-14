#!/usr/bin/env python3
"""places-world-v1: 지역(나라 묶음)을 한 번씩 스캔 → 적응형 H3 타일(r3 → r4 → r5, 타일당 추정 6만 행 ≈ 2.5 MB 이하) → 타일별 원본 parquet.
  python3 world_region.py <지역> [--stage extract|tile|all]
이후 tools/build_pack.py w-<타일> … 로 빌드(같은 필터·병합; PACK_TARGET_MB 로 파트 크기). 타일 목록 = raw/world/tiles_<지역>.json
도시 팩 = 타일 묶음(타일 id 목록), 지명 → 타일 = 좌표의 r5 셀 → 그 셀을 덮는 타일."""
import duckdb, json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.join(HERE, '..')
W = os.path.join(ROOT, 'raw', 'world'); os.makedirs(W, exist_ok=True)
REGIONS = json.load(open(os.path.join(HERE, 'world_regions.json'), encoding='utf-8'))
# 필터·추정 규칙은 공개 코드에 두지 않음: Actions 에서 private_enc 를 풀면 tools/rules.json 이 생김(원본은 저장소 밖 rules_src/)
RULES = json.load(open(os.path.join(HERE, 'rules.json')))
EST_OVT, EST_FSQ, TILE_ROWS = RULES['est_ovt'], RULES['est_fsq'], RULES['tile_rows']
BAD = '[' + ','.join("'%s'" % x for x in RULES['fsq_bad_flags']) + ']'


def con_ovt():
    sys.path.insert(0, HERE)
    from ovt_common import con, SRC  # 저장소 안의 tools/ovt_common.py
    c = con()
    c.execute(f"SET memory_limit='{os.environ.get('DUCK_MEM', '2GB')}'"); c.execute('SET preserve_insertion_order=false')
    c.execute(f"SET threads={os.environ.get('DUCK_THREADS', '4')}")
    return c, SRC


def con_fsq():
    c = duckdb.connect()
    for s in ('INSTALL httpfs', 'LOAD httpfs', 'INSTALL iceberg', 'LOAD iceberg'):
        c.execute(s)
    if os.environ.get('HTTPS_PROXY'):
        c.execute(f"SET http_proxy='{os.environ['HTTPS_PROXY'].replace('http://', '')}'")
    if os.path.exists('/root/.ccr/ca-bundle.crt'):
        c.execute("SET ca_cert_file='/root/.ccr/ca-bundle.crt'")
    c.execute(f"SET memory_limit='{os.environ.get('DUCK_MEM', '2GB')}'"); c.execute('SET preserve_insertion_order=false')
    tok = os.environ['FSQ_TOKEN'].strip().strip('"').strip("'")
    c.execute(f"CREATE TEMPORARY SECRET fsq (TYPE ICEBERG, TOKEN '{tok}')"); del tok
    c.execute("ATTACH 'places' AS places (TYPE ICEBERG, ENDPOINT 'https://catalog.h3-hub.foursquare.com/iceberg', SECRET fsq)")
    return c


def extract(r):
    reg = REGIONS[r]; cc = "','".join(reg['cc'])
    fo, ff = os.path.join(W, f'{r}_ovt.parquet'), os.path.join(W, f'{r}_fsq.parquet')
    if not os.path.exists(fo):
        c, SRC = con_ovt(); t = time.time()
        sel = ' union all '.join(f"""select id, names.primary nm, names.common nc, names.rules nr, basic_category bc, taxonomy.primary tp, taxonomy.hierarchy th,
          confidence conf, len(coalesce(websites,[])) nweb, len(coalesce(socials,[])) nsoc, len(coalesce(phones,[])) nph,
          brand.names.primary brand, brand.names.common brand_nc, brand.wikidata brand_wd,
          addresses[1].freeform addr, addresses[1].locality loc, addresses[1].country actry, operating_status st,
          list_transform(sources, lambda s: struct_pack(d := s.dataset, l := s.license, c := s.confidence)) srcs,
          (bbox.xmin+bbox.xmax)/2 lon, (bbox.ymin+bbox.ymax)/2 lat
          from {SRC} where bbox.xmin between {b[0]} and {b[2]} and bbox.ymin between {b[1]} and {b[3]}
          and confidence >= {RULES['ovt_min_conf']} and coalesce(operating_status,'open') = 'open' and addresses[1].country in ('{cc}')""" for b in reg['bbox'])
        q = sel if len(reg['bbox']) == 1 else f'select distinct on (id) * from ({sel})'  # 상자 1개면 중복 없음 → 메모리 절약(DISTINCT 는 전부 메모리에 올림)
        c.execute(f"copy ({q}) to '{fo}.tmp' (format parquet, compression zstd)"); os.replace(fo + '.tmp', fo)
        print(r, 'ovt', c.execute(f"select count(*) from '{fo}'").fetchone()[0], round(time.time() - t), 's', flush=True)
    if not os.path.exists(ff) and os.environ.get('OVT_ONLY') != '1':  # OVT_ONLY=1: 로컬 시험(FSQ 토큰 없음)
        c = con_fsq(); t = time.time()
        sel = ' union all '.join(f"""select fsq_place_id, name, latitude, longitude, address, locality, country, date_refreshed, tel, website, instagram,
              fsq_category_ids, fsq_category_labels, unresolved_flags from places.datasets.places_os
            where latitude between {b[1]} and {b[3]} and longitude between {b[0]} and {b[2]} and country in ('{cc}') and date_closed is null
              and name is not null and date_refreshed >= '{RULES['fsq_min_refreshed']}'
              and not list_has_any(coalesce(unresolved_flags, []), {BAD})""" for b in reg['bbox'])
        q = sel if len(reg['bbox']) == 1 else f'select distinct on (fsq_place_id) * from ({sel})'
        c.execute(f"copy ({q}) to '{ff}.tmp' (format parquet, compression zstd)"); os.replace(ff + '.tmp', ff)
        print(r, 'fsq', c.execute(f"select count(*) from '{ff}'").fetchone()[0], round(time.time() - t), 's', flush=True)


def tile(r):
    """11/14 16:00 수정: 타일을 (나라, 셀) 단위로 나눈다.
    전에는 타일 하나에 cc = 지역의 첫 나라(sea_mainland → TH)만 적었고, build_pack 이 `actry != cc` 행을 버려
    여러 나라 지역(17개)에서 첫 나라 말고는 전부 빠졌다(방비엥 LA·팀부 BT·호이안 VN·카즈베기 GE·우유니 BO …).
    이제 나라마다 따로 적응형 타일(r3 → r4 → r5)을 만들고 슬러그를 w-<셀>_<나라> 로 한다 → build_pack 은 손대지 않아도 된다."""
    reg = REGIONS[r]
    fo, ff = os.path.join(W, f'{r}_ovt.parquet'), os.path.join(W, f'{r}_fsq.parquet')
    c = duckdb.connect(); c.execute('INSTALL h3 FROM community'); c.execute('LOAD h3')
    c.execute(f"SET memory_limit='{os.environ.get('DUCK_MEM', '2GB')}'")
    fsq_sql = f"union all select h3_latlng_to_cell(latitude, longitude, 5) c5, country cc, {EST_FSQ} w from read_parquet('{ff}')" if os.path.exists(ff) else ''
    c.execute(f"""create table cnt as select c5, cc, sum(w) w from (
        select h3_latlng_to_cell(lat, lon, 5) c5, actry cc, {EST_OVT} w from read_parquet('{fo}')
        {fsq_sql}) where cc is not null group by 1, 2""")
    rows = c.execute("select c5, cc, w, h3_cell_to_parent(c5, 4), h3_cell_to_parent(c5, 3) from cnt").fetchall()
    s3, s4 = {}, {}
    for c5, cc, w, p4, p3 in rows:
        s3[(cc, p3)] = s3.get((cc, p3), 0) + w; s4[(cc, p4)] = s4.get((cc, p4), 0) + w
    tmap = {}
    for c5, cc, w, p4, p3 in rows:
        tmap[(c5, cc)] = p3 if s3[(cc, p3)] <= TILE_ROWS else (p4 if s4[(cc, p4)] <= TILE_ROWS else c5)
    c.execute('create table tm (c5 ubigint, cc varchar, tile ubigint, tkey varchar)')
    keys = {}
    for (c5, cc), t in tmap.items():
        keys.setdefault((t, cc), []).append(c5)
    tstr = {t: c.execute('select h3_h3_to_string(?)', [t]).fetchone()[0] for t in {t for t, _ in keys}}
    c.executemany('insert into tm values (?, ?, ?, ?)', [(c5, cc, t, f'{tstr[t]}_{cc}') for (c5, cc), t in tmap.items()])
    out = {}
    for (t, cc), c5s in keys.items():
        tid = f'{tstr[t]}_{cc}'
        cs = sorted(c.execute('select h3_h3_to_string(?)', [x]).fetchone()[0] for x in c5s)
        la, lo = c.execute('select h3_cell_to_lat(?), h3_cell_to_lng(?)', [t, t]).fetchone()
        res = c.execute('select h3_get_resolution(?)', [t]).fetchone()[0]
        out[f'w-{tid}'] = {'region': r, 'res': res, 'cells': cs, 'center': [round(la, 4), round(lo, 4)], 'cc': cc, 'name_ko': f"{reg['name_ko']} 타일 {tid}"}
    # 타일별 원본(셀·나라 → 타일 표로 붙여 한 번에 나눠 씀: 타일 폴더 hive 분할, 폴더 이름 tile=<셀>_<나라>)
    c.execute(f"""copy (select o.*, tm.tkey tile from read_parquet('{fo}') o join tm on h3_latlng_to_cell(o.lat, o.lon, 5) = tm.c5 and o.actry = tm.cc)
        to '{W}/{r}_ovt_t' (format parquet, partition_by (tile), overwrite_or_ignore, compression zstd)""")
    if os.path.exists(ff):
        c.execute(f"""copy (select f.*, tm.tkey tile from read_parquet('{ff}') f join tm on h3_latlng_to_cell(f.latitude, f.longitude, 5) = tm.c5 and f.country = tm.cc)
            to '{W}/{r}_fsq_t' (format parquet, partition_by (tile), overwrite_or_ignore, compression zstd)""")
    json.dump(out, open(os.path.join(W, f'tiles_{r}.json'), 'w'), ensure_ascii=False)
    print(r, 'tiles', len(out), {k: sum(1 for v in out.values() if v['res'] == k) for k in (3, 4, 5)},
          'by cc', dict(sorted({cc: sum(1 for v in out.values() if v['cc'] == cc) for cc in reg['cc']}.items())), flush=True)


if __name__ == '__main__':
    r = sys.argv[1]; stage = sys.argv[3] if len(sys.argv) > 3 and sys.argv[2] == '--stage' else 'all'
    if stage in ('extract', 'all'):
        extract(r)
    if stage in ('tile', 'all'):
        tile(r)
