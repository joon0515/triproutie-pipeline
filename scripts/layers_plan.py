#!/usr/bin/env python3
"""입력(groups) → 빌드할 지역 matrix·판 이름(tag)·방식(mode)을 GITHUB_OUTPUT 형식으로 낸다. tag 형식도 여기서 확인한다.
(11/15) tag 를 비우면: full = L-<오늘 UTC>, merge = IN_LATEST(지금 places/v1/layers.json 의 version). mode = full | merge."""
import datetime, json, os, re, sys

R = {k: v for k, v in json.load(open('tools/layers_regions.json', encoding='utf-8')).items() if not k.startswith('_')}


def plan(groups):
    want = [g.strip() for g in (groups or 'priority').split(',') if g.strip()]
    if 'all' in want:
        return sorted(R)
    sel = [k for k, v in R.items() if v.get('group') in want or k in want]
    return sorted(set(sel))


def resolve(env):
    """→ (tag, mode, 지역 목록). 틀리면 SystemExit."""
    mode = (env.get('IN_MODE') or 'full').strip()
    if mode not in ('full', 'merge'):
        sys.exit('mode 는 full | merge')
    tag = (env.get('IN_TAG') or '').strip()
    if not tag:
        tag = env.get('IN_LATEST', '').strip() if mode == 'merge' else 'L-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')
    if not re.fullmatch(r'[0-9A-Za-z._-]{1,40}', tag):
        sys.exit('tag 은 영문·숫자·._- 40자 이내' + (' (merge: 최신 판을 못 찾음)' if mode == 'merge' else ''))
    sel = plan(env.get('IN_GROUPS', 'priority')) if mode == 'full' else []
    if mode == 'full' and not sel:
        sys.exit('고른 지역이 없음')
    return tag, mode, sel


if __name__ == '__main__':
    tag, mode, sel = resolve(os.environ)
    print('tag=' + tag)
    print('mode=' + mode)
    print('matrix=' + json.dumps({'region': sel or ['none']}))
