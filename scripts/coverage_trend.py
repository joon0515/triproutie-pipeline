#!/usr/bin/env python3
"""docs/COVERAGE.md 의 '매일 채움률·추세' 표를 갱신한다 — coverage-report 가 하루 한 번 부르고 커밋한다.

  python3 scripts/coverage_trend.py --dir <coverage-*.json 폴더> --md docs/COVERAGE.md [--days 14]

입력: Storage collect/v1/coverage/<날짜>.json(coverage.py 결과, 개수·비율만). 장소 내용은 없다.
표 1 = 최근 N일 채움률(이름 5개 언어·영업시간·사진·소개). 표 2 = 오늘 값과 1·7·30일 전 대비 증감(%p)·장소 수 증가.
문서의 <!-- coverage-trend:start --> … <!-- coverage-trend:end --> 사이만 바꾼다(없으면 끝에 붙인다).
"""
import argparse, datetime, json, os, re, sys

START, END = '<!-- coverage-trend:start -->', '<!-- coverage-trend:end -->'
COLS = (('ko', '이름 ko'), ('en', '이름 en'), ('ja', '이름 ja'), ('zh_Hans', '이름 zh-Hans'), ('zh_Hant', '이름 zh-Hant'), ('h', '영업시간'), ('photo', '사진'), ('desc', '소개(1개 언어+)'))
DESC_LANGS = ('ko', 'en', 'ja', 'zh_Hans', 'zh_Hant')


BASES = (('itin', '일정 장소 기준(명소·식당·숙소·교통 거점 — 일정 생성기가 쓰는 범주)'), ('all', '전체 기준(벤치·정류장 등 포함 게시 장소 전부)'))


def point(rep):
    """보고 하나 → {'date', 'itin': {n, 칸: 비율}, 'all': {...}}. 일정 기준은 coverage.py 의 itinerary(11/18~), 없으면 None."""
    lay = rep.get('layers') or {}
    dsc = lay.get('desc') or {}
    p = {'date': rep.get('date')}
    for base, pk, dk in (('all', 'places', 'all'), ('itin', 'itinerary', 'itinerary')):
        pl, d = lay.get(pk) or {}, dsc.get(dk) or {}
        n = pl.get('n') or 0
        q = {'n': n if pl else None}
        for k, _ in COLS:
            if k == 'desc':
                q[k] = (d.get('any', 0) / d['n']) if d.get('n') else None
                for sl in DESC_LANGS:
                    q['desc_' + sl] = (d.get(sl, 0) / d['n']) if d.get('n') else None
            else:
                q[k] = (pl.get(k, 0) / n) if n else None
        p[base] = q
    return p


def load(d):
    pts = []
    for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not re.match(r'^\d{4}-\d{2}-\d{2}\.json$', f):
            continue
        try:
            rep = json.load(open(os.path.join(d, f), encoding='utf-8'))
        except (OSError, ValueError):
            continue
        rep.setdefault('date', f[:10])
        pts.append(point(rep))
    return sorted((p for p in pts if p['date']), key=lambda p: p['date'])


def pct(v):
    return f'{100 * v:.1f}%' if v is not None else '-'


def dpp(a, b):
    return f'{100 * (a - b):+.1f}%p' if a is not None and b is not None else '-'


def ago(pts, days):
    """마지막 점 기준 days 일 전(그날이 없으면 그 앞의 가장 가까운 날)."""
    if not pts:
        return None
    t = datetime.date.fromisoformat(pts[-1]['date']) - datetime.timedelta(days=days)
    cand = [p for p in pts if datetime.date.fromisoformat(p['date']) <= t]
    return cand[-1] if cand else None


def table(pts, days=14, now=None):
    L = [START, '## 8. 매일 채움률·추세 (자동 — coverage-report 가 날마다 바꾼다, 손으로 고치지 않는다)',
         f"갱신 {now or datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')} · 원천: Storage `collect/v1/coverage/<날짜>.json`(개수·비율만) · "
         '이름은 그 언어로 적힌 원천 값만(번역·간체↔번체 변환 안 셈) · 영업시간은 알아듣는 값만 · 소개는 1개 언어 이상. '
         '**주 지표는 일정 장소 기준**(11/18 — 전체 8천만여 곳에는 벤치·가로등·정류장이 섞여 비율이 0%대로 묻힌다). 전체 기준도 함께 남긴다.', '']
    if not pts:
        return '\n'.join(L + ['- 아직 측정값이 없다(coverage-report 첫 실행 뒤 채워진다).', END]) + '\n'
    cur = pts[-1]
    refs = [(f'{d}일 전', ago(pts, d)) for d in (1, 7, 30)]
    for i, (base, title) in enumerate(BASES, 1):
        L += [f'### 8-{i}. 최근 채움률 — {title}', '| 날짜 | 장소 | ' + ' | '.join(c for _, c in COLS) + ' |', '|---' * (len(COLS) + 2) + '|']
        for p in reversed(pts[-days:]):
            q = p[base]
            L.append(f"| {p['date']} | {format(q['n'], ',') if q['n'] is not None else '-'} | " + ' | '.join(pct(q[k]) for k, _ in COLS) + ' |')
        L.append('')
    hdr = ' | '.join(f'{nm} 대비' for nm, _ in refs)
    L += [f"### 8-3. 증가 추세 (오늘 {cur['date']} 기준)", f'| 칸 | 일정 기준 오늘 | {hdr} | 전체 기준 오늘 | {hdr} |', '|---' * (2 * len(refs) + 3) + '|']
    nd = lambda a, b: f"{a - b:+,}" if a is not None and b is not None else '-'
    cnt = lambda q: format(q['n'], ',') if q['n'] is not None else '-'
    row = ['장소 수']
    for base, _ in BASES:
        row += [cnt(cur[base])] + [nd(cur[base]['n'], r[base]['n'] if r else None) for _, r in refs]
    L.append('| ' + ' | '.join(row) + ' |')
    for k, c in COLS:
        row = [c]
        for base, _ in BASES:
            row += [pct(cur[base][k])] + [dpp(cur[base][k], r[base][k] if r else None) for _, r in refs]
        L.append('| ' + ' | '.join(row) + ' |')
    for base, title in BASES:
        q = cur[base]
        if any(q.get('desc_' + sl) is not None for sl in DESC_LANGS):
            L.append(f"- 소개 언어별(오늘, {title.split('(')[0]}): " + ' · '.join(f"{sl} {pct(q.get('desc_' + sl))}" for sl in DESC_LANGS))
    return '\n'.join(L + ['', END]) + '\n'


def update_md(text, block):
    if START in text and END in text:
        a, b = text.index(START), text.index(END) + len(END)
        return text[:a] + block.rstrip('\n') + text[b:]
    return text.rstrip('\n') + '\n\n' + block


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', required=True); ap.add_argument('--md', default='docs/COVERAGE.md'); ap.add_argument('--days', type=int, default=14)
    a = ap.parse_args()
    pts = load(a.dir)
    old = open(a.md, encoding='utf-8').read()
    new = update_md(old, table(pts, a.days))
    if new != old:
        open(a.md, 'w', encoding='utf-8').write(new)
    print(f'측정 {len(pts)}일 · 바뀜 {new != old}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
