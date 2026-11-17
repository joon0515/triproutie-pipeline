#!/usr/bin/env python3
"""영업시간 문자열 검사 — OSM opening_hours 문법의 흔한 부분만 알아듣고, 못 알아들으면 버린다(고쳐 쓰지 않는다). (2026-11-18, 223기)

    oh_ok('Mo-Fr 09:00-18:00; Sa 10:00-14:00; PH off')  → 'Mo-Fr 09:00-18:00; Sa 10:00-14:00; PH off'
    oh_ok('9시~6시')                                      → None

알아듣는 것: 24/7 · 요일(Mo..Su, 범위·쉼표, Su[1]·Mo[-1]) · PH/SH(+-n day[s]) · 달(Jan..Dec, 'Jan 05-Mar 10', 'Dec 25') · 해(2026) ·
  시각 HH:MM(시작 0~24시, 끝 0~48시) · 'HH:MM+' · sunrise/sunset/dawn/dusk · off/closed/open/unknown · 끝의 "설명" · 규칙 사이 ';' ',' '||'.
그 밖(주 번호 week 01-53, 쉬는 날 계산식 등)은 버린다 — 앱에 틀린 시간을 보여 주느니 비워 둔다.
앞뒤 공백·겹 공백만 정리한 원문을 돌려준다. 255자를 넘으면 버린다(중간에서 자르면 뜻이 바뀐다).
"""
import re

MAX_LEN = 255
_DAY = r'(?:Mo|Tu|We|Th|Fr|Sa|Su)'
_NTH = r'(?:\[-?[1-5](?:-[1-5])?(?:,-?[1-5](?:-[1-5])?)*\](?: ?[+-]\d+ days?)?)'
_WD = rf'{_DAY}(?:{_NTH}|-{_DAY})?'
_HOL = r'(?:PH|SH)(?: ?[+-]\d+ days?)?'
_WDSEL = rf'(?:{_WD}|{_HOL})(?:,(?:{_WD}|{_HOL}))*'
_MON = r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)'
_MD = rf'(?:\d{{4}} )?{_MON}(?: \d{{1,2}})?'
_MR = rf'{_MD}(?:-(?:{_MD}|\d{{1,2}}))?'
_YR = r'\d{4}(?:-\d{4})?'
_MSEL = rf'(?:{_MR}(?:,{_MR})*|{_YR}(?: {_MR}(?:,{_MR})*)?)'
_EV = r'(?:sunrise|sunset|dawn|dusk)'
_TM = rf'(?:\d{{1,2}}:\d\d|{_EV}|\({_EV}[+-]\d\d:\d\d\))'
_SPAN = rf'{_TM}(?:-{_TM}\+?|\+)?'
_TSEL = rf'{_SPAN}(?:,{_SPAN})*'
_STATE = r'(?:off|closed|open|unknown)'
_CMT = r'(?:"[^"]{1,80}")'
_RULE = re.compile(rf'^(?:{_MSEL}:? ?)?(?:{_WDSEL}:? ?)?(?:{_TSEL})?(?: ?{_STATE})?(?: ?{_CMT})?$')
_TIME = re.compile(r'(\d{1,2}):(\d\d)')
_HEAD = re.compile(rf'^(?:{_DAY}|PH|SH|{_MON}|\d{{4}} )')


def _times_ok(rule):
    """시각 숫자 범위: 분 < 60, 시작 시 ≤ 24, 끝 시 ≤ 48(밤샘 영업 표기)."""
    for span in re.findall(r'\d{1,2}:\d\d(?:-\d{1,2}:\d\d)?', rule):
        ts = _TIME.findall(span)
        for k, (h, m) in enumerate(ts):
            if int(m) > 59 or int(h) > (24 if k == 0 else 48):
                return False
    return True


def _rule_ok(r):
    r = r.strip()
    if not r:
        return False
    if r == '24/7':
        return True
    return bool(_RULE.match(r)) and _times_ok(r) and r not in (':',)


def oh_ok(s):
    """알아들으면 정리한 문자열, 아니면 None."""
    if not isinstance(s, str):
        return None
    s = re.sub(r'\s+', ' ', s).strip()
    if not s or len(s) > MAX_LEN or s.startswith('raw:'):
        return None
    for part in re.split(r'\s*(?:;|\|\|)\s*', s):
        if not part:
            continue
        if _rule_ok(part):
            continue
        # 쉼표가 규칙 사이 구분인 경우('Mo-Fr 08:00-12:00, Sa 10:00-12:00'): 뒤 조각이 요일·휴일·달로 시작할 때만 거기서 자른다
        pieces, cur = [], ''
        for bit in part.split(','):
            b = bit.strip()
            if cur and _HEAD.match(b) and _rule_ok(cur):
                pieces.append(cur); cur = b
            else:
                cur = f'{cur},{b}' if cur else b
        pieces.append(cur)
        if len(pieces) < 2 or not all(_rule_ok(p) for p in pieces):
            return None
    return s
