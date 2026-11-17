#!/usr/bin/env python3
"""층 지역(tools/layers_regions.json 의 cc)의 수집 층(col_<cc>_*.json.gz) gs:// 주소를 한 줄씩 낸다. (2026-11-18, 223기)
    python3 region_cols.py <층 지역>          # GCS_BUCKET 환경 변수, 가공 최신 판 collect/v1/processed/latest.json 의 paths
names5_osm.py·hours_osm.py --cols 가 이 목록으로 게시 장소(수집 층) id 에도 OSM 이름·영업시간을 맞춘다."""
import json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def urls(latest, ccs, base):
    paths = dict(latest.get('paths') or {})
    for f in latest.get('files') or []:
        paths.setdefault(f, f"processed/out/{latest.get('run')}/{f}")
    return [f'{base}/{v}' for k, v in sorted(paths.items()) if k.startswith('col_') and not k.startswith('col_info_') and k.split('_')[1] in ccs]


def main(region):
    reg = json.load(open(os.path.join(HERE, '..', 'tools', 'layers_regions.json'), encoding='utf-8')).get(region) or {}
    ccs = {c.upper() for c in reg.get('cc') or []}
    if not ccs:
        return 1
    base = f"gs://{os.environ['GCS_BUCKET']}/collect/v1"
    r = subprocess.run(['gcloud', 'storage', 'cat', f'{base}/processed/latest.json'], capture_output=True)
    if r.returncode != 0:
        return 1
    print('\n'.join(urls(json.loads(r.stdout), ccs, base)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
