#!/usr/bin/env python3
"""세계 타일 지역 manifest 에서, 층 지역(예 jp-kansai)의 범위와 겹치는 타일 파일의 gs:// 주소를 한 줄씩 낸다.
    python3 region_tiles.py <world 지역 이름> <world tag> <층 지역 이름>
지역 manifest: gs://<bucket>/places/v1/<tag>/regions/<world>.json (places-world-region-1, tiles[{file,bbox?}]).
타일 항목에 bbox 가 없으면 그 지역의 타일을 모두 낸다(이름 맞추기는 좌표로 하므로 결과는 같고 시간만 더 든다)."""
import json, os, subprocess, sys

BOX = {  # 층 지역 → [서, 남, 동, 북]
    'jp-hokkaido': [139.3, 41.3, 146.0, 45.6], 'jp-tohoku': [138.9, 36.7, 142.2, 41.6], 'jp-kanto': [138.3, 34.8, 141.0, 37.2],
    'jp-chubu': [135.4, 34.5, 139.2, 38.6], 'jp-kansai': [134.2, 33.4, 137.0, 35.8], 'jp-chugoku': [130.7, 33.7, 134.5, 35.7],
    'jp-shikoku': [132.0, 32.6, 134.9, 34.6], 'jp-kyushu': [122.9, 24.0, 132.2, 34.8],
}


def overlap(a, b):
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def main(world, tag, region):
    bucket = os.environ['GCS_BUCKET']
    base = f'gs://{bucket}/places/v1/{tag}'
    man = None
    for path in (f'{base}/regions/{world}.json', f'{base}/parts/{world}.json'):  # 2단 manifest 판 / 옛 부분 manifest 판
        r = subprocess.run(['gcloud', 'storage', 'cat', path], capture_output=True)
        if r.returncode == 0 and r.stdout:
            man = json.loads(r.stdout)
            break
    if man is None:
        return 1
    box = BOX.get(region)
    for t in man.get('tiles') or []:
        f, bb = t.get('file'), t.get('bbox')
        if not isinstance(f, str) or not f.startswith('w-') or '/' in f or '..' in f:
            continue
        if box and isinstance(bb, list) and len(bb) == 4 and not overlap(bb, box):
            continue
        print(f'{base}/tiles/{f}')


if __name__ == '__main__':
    sys.exit(main(*sys.argv[1:4]) or 0)
