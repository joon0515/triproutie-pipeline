#!/usr/bin/env python3
"""(270기 진단, 수동) 한국 3도시 '관광·명소 상위 50' 이 진짜 명소인지 보는 표 — 대표님 지시로 공개 장소 이름을 로그에 한 번 낸다.

    python3 scripts/diag_top.py --bucket <b> [--cities seoul,busan,jeju]

표: 순위 · 이름(nm/ko) · 분류(cats) · 점수 s · id 앞머리(출처) · 그 밖 칸 이름. 그리고 대표 명소 이름이 타일에 있는지·몇 위인지·분류가 무엇인지.
"""
import argparse, json, os, shutil, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, 'collect')); sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import photo_pri as PP  # noqa: E402
from common import norm_name  # noqa: E402

LANDMARKS = {'seoul': ['경복궁', '창덕궁', '덕수궁', 'N서울타워', '남산서울타워', '북촌한옥마을', '명동', '인사동', '롯데월드', '국립중앙박물관', '광장시장', '동대문디자인플라자'],
             'busan': ['해운대해수욕장', '해운대', '광안리해수욕장', '감천문화마을', '태종대', '해동용궁사', '자갈치시장', '용두산공원', '송도해수욕장', '광안대교'],
             'jeju': ['성산일출봉', '한라산', '만장굴', '우도', '천지연폭포', '정방폭포', '협재해수욕장', '섭지코지', '용두암', '사려니숲길', '함덕해수욕장', '오설록']}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bucket', required=True); ap.add_argument('--cities', default='seoul,busan,jeju'); ap.add_argument('--top', type=int, default=50)
    a = ap.parse_args()
    cities = {c['key']: c for c in PP.load_cities()}
    wd = tempfile.mkdtemp(dir=os.environ.get('RUNNER_TEMP') or None)
    try:
        ver, tiles_for = PP.gcs_tiles_for(a.bucket, wd)
        for k in a.cities.split(','):
            c = cities[k]; bb = c['bbox']
            rows = []
            keys = set()
            for d in tiles_for(bb):
                keys |= {x for x, v in d.items() if isinstance(v, list)}
                cats = d.get('cats') or []
                for i in range(min(len(d['id']), len(d['_lat']))):
                    if not (bb[0] <= d['_lon'][i] <= bb[2] and bb[1] <= d['_lat'][i] <= bb[3]):
                        continue
                    ci = d['c'][i] if isinstance(d.get('c'), list) else None
                    cat = cats[ci] if isinstance(ci, int) and 0 <= ci < len(cats) else '?'
                    g = lambda x: (d[x][i] if isinstance(d.get(x), list) and i < len(d[x]) else None)
                    rows.append({'id': d['id'][i], 'nm': g('nm'), 'ko': g('ko'), 'cat': cat, 's': g('s') or 0,
                                 'extra': {x: g(x) for x in ('t', 'sc', 'cs', 'src', 'k', 'tag', 'cl', 'sub') if g(x) is not None}})
            rows.sort(key=lambda r: -(r['s'] or 0))
            sight = [r for r in rows if r['cat'] in PP.SIGHT_CATS]
            print(f'\n## {k} — 타일 판 {ver} · 상자 안 행 {len(rows):,} · 관광·명소 {len(sight):,} · 칸 {sorted(keys)}')
            cc = {}
            for r in rows[:2000]:
                cc[r['cat']] = cc.get(r['cat'], 0) + 1
            print(f'- 점수 상위 2000 의 분류 분포: {dict(sorted(cc.items(), key=lambda x: -x[1]))}')
            print('| 관광 순위 | 전체 순위 | 이름 | ko | 분류 | 점수 | id 앞머리 | 그 밖 |')
            pos = {r['id']: n for n, r in enumerate(rows, 1)}
            for n, r in enumerate(sight[:a.top], 1):
                print(f"| {n} | {pos[r['id']]} | {r['nm']} | {r['ko']} | {r['cat']} | {r['s']} | {str(r['id']).split(':')[0][:12]} | {json.dumps(r['extra'], ensure_ascii=False)[:80]} |")
            print('| 대표 명소 | 찾은 행 수 | 가장 높은 행: 관광 순위 · 전체 순위 · 분류 · 점수 · 이름 |')
            spos = {r['id']: n for n, r in enumerate(sight, 1)}
            for lm in LANDMARKS.get(k, []):
                key = norm_name(lm)
                hit = [r for r in rows if key and any(key in norm_name(x or '') for x in (r['nm'], r['ko']))]
                b = hit[0] if hit else None
                print(f"| {lm} | {len(hit)} | " + (f"{spos.get(b['id'], '-')} · {pos[b['id']]} · {b['cat']} · {b['s']} · {b['nm']}" if b else '없음') + ' |')
    finally:
        shutil.rmtree(wd, ignore_errors=True)


if __name__ == '__main__':
    main()
