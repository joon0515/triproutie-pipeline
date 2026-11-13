#!/usr/bin/env python3
"""입력(groups) → 빌드할 지역 matrix 를 GITHUB_OUTPUT 형식으로 낸다. tag 형식도 여기서 확인한다."""
import json, os, re, sys

R = {k: v for k, v in json.load(open('tools/layers_regions.json', encoding='utf-8')).items() if not k.startswith('_')}


def plan(groups):
    want = [g.strip() for g in (groups or 'priority').split(',') if g.strip()]
    if 'all' in want:
        return sorted(R)
    sel = [k for k, v in R.items() if v.get('group') in want or k in want]
    return sorted(set(sel))


if __name__ == '__main__':
    tag = os.environ.get('IN_TAG', '')
    if not re.fullmatch(r'[0-9A-Za-z._-]{1,40}', tag):
        sys.exit('tag 은 영문·숫자·._- 40자 이내')
    sel = plan(os.environ.get('IN_GROUPS', 'priority'))
    if not sel:
        sys.exit('고른 지역이 없음')
    print('matrix=' + json.dumps({'region': sel}))
