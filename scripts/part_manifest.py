#!/usr/bin/env python3
"""지역 부분 manifest: 타일 파일마다 id·r5 셀·장소 수·크기·SHA-256(장소 데이터 없음)"""
import gzip, glob, hashlib, json, os, sys
R = sys.argv[1]; T = json.load(open(f'raw/world/tiles_{R}.json'))
out = []
for fn in sorted(glob.glob('packs/w-*.json.gz')):
    b = open(fn, 'rb').read(); p = json.loads(gzip.decompress(b))
    slug = p['pack'].split('-p')[0] if p.get('part') else p['pack']
    out.append({'file': os.path.basename(fn), 'tile': slug[2:], 'part': p.get('part'), 'parts': p.get('parts'), 'cc': p['cc'],
                'h3r5': p['h3r5'], 'n': p['n'], 'size': len(b), 'sha256': hashlib.sha256(b).hexdigest()})
json.dump({'region': R, 'tiles': out}, sys.stdout, ensure_ascii=False)
