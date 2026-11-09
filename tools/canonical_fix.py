"""remote_data/make_remote.py canonical() 수정안 (11/9 글로벌 팩 작업 중 발견).
문제: 앱 tl_sig.dart tlCanonicalJson 은 정수 값 double(145.0)을 '145' 로, Python json.dumps 는 '145.0' 으로 써서
      manifest 에 그런 값이 있으면 서명 대상 문자열이 달라져 앱이 '서명 틀림'으로 버린다(조용한 갱신 실패).
      그 밖에 지수 표기(1e-07 vs 1e-7, 1e+20 vs 100000000000000000000.0), NaN/Infinity, BMP 밖 글자 키 정렬(코드포인트 vs UTF-16)도 다를 수 있다.
수정: 서명 전에 값을 '양쪽이 똑같이 쓰는 모양'으로 고정하고, 고정할 수 없는 값은 서명을 거부(예외)한다."""
import json, math, re

_SAFE_FLOAT = re.compile(r'-?\d+\.\d+')


def canon_value(o):
    if isinstance(o, bool) or o is None or isinstance(o, str):
        return o
    if isinstance(o, int):
        if abs(o) >= 2 ** 53:
            raise ValueError(f'서명 불가: 2^53 이상 정수({o}) — 웹(JS)에서 정밀도 손실')
        return o
    if isinstance(o, float):
        if math.isnan(o) or math.isinf(o):
            raise ValueError('서명 불가: NaN/Infinity')
        if o.is_integer() and abs(o) < 1e15:
            return int(o)  # Dart: v == v.truncateToDouble() && v.abs() < 1e15 → 정수로 씀
        r = repr(o)
        if not _SAFE_FLOAT.fullmatch(r):
            raise ValueError(f'서명 불가: 지수 표기 실수({r}) — Python/Dart 표기가 다름. 문자열이나 정수(×10^n)로 바꿀 것')
        return o
    if isinstance(o, (list, tuple)):
        return [canon_value(v) for v in o]
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            k = str(k)
            if any(ord(ch) > 0xFFFF for ch in k):
                raise ValueError(f'서명 불가: BMP 밖 글자가 든 키({k!r}) — 정렬 순서가 Python(코드포인트)·Dart(UTF-16) 다름')
            out[k] = canon_value(v)
        return out
    raise ValueError(f'서명 불가: 형식 {type(o)}')


def canonical(o):
    """make_remote.canonical 대체. 서명할 때·검사할 때 모두 이 함수."""
    return json.dumps(canon_value(o), sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
