#!/usr/bin/env python3
"""뿌리 목록 크기 가드 (263기). 층 수를 1~200배로 늘린 합성 목록으로 split_manifest 를 돌려, 뿌리(서명 칸 포함)가 48 KiB 를 넘으면 실패한다.
실제 게시(publish_layers.py)도 같은 48 KiB 가드를 서명 전에 건다. 네트워크·키 없음.
    python3 scripts/guards/root_size.py            # 실패 시 종료 코드 1
"""
import hashlib, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools'))
import publish_layers as P
from canonical_fix import canon_value, canonical

BASE = {'amenities': 200, 'hours': 34, 'tourapi': 16, 'names': 8, 'photos': 2, 'desc': 40, 'names5': 20, 'rec': 10, 'shop': 5, 'events': 5}
SCALES = (1, 3, 10, 40, 100, 200)


def _ent(kind, i, cc=None):
    e = {'file': f'{kind}-region-{cc or "xx"}-{i:05d}.json.gz', 'kind': kind, 'n': 1000 + i, 'size': 100000 + i,
         'sha256': hashlib.sha256(f'{kind}{i}'.encode()).hexdigest(), 'bbox': [126.1234 + i % 7, 33.1234, 129.5678, 38.6543], 'license': 'ODbL-1.0'}
    if kind == 'collect':
        e.update(cc=cc, dataset='places', license='mixed')
    return e


def root_bytes(scale):
    ents = [_ent(k, i) for k, n in BASE.items() for i in range(int(n * scale))]
    ccs = [chr(65 + a) + chr(65 + b) for a in range(26) for b in range(26)]
    ents += [_ent('collect', i, ccs[i % 200]) for i in range(int(462 * scale))]
    flat = canon_value(P.build_manifest('c20261120', [], extra=ents))
    root, _ = P.split_manifest(flat, canonical)
    return len(canonical(canon_value(root)).encode()) + P.SIG_ROOM


def check(scales=SCALES, limit=None):
    """[(배수, bytes)] 를 돌려주고, 한도(기본 48 KiB)를 넘는 배수가 있으면 ValueError."""
    limit = limit or P.ROOT_GUARD
    out = [(s, root_bytes(s)) for s in scales]
    bad = [(s, n) for s, n in out if n > limit]
    if bad:
        raise ValueError(f'뿌리 목록 {limit:,} bytes 초과: {bad}')
    return out


if __name__ == '__main__':
    try:
        for s, n in check():
            print(f'x{s:<4} 뿌리 {n:>7,} bytes')
    except ValueError as e:
        print('FAIL', e)
        sys.exit(1)
    print(f'OK: 모두 {P.ROOT_GUARD:,} bytes(48 KiB) 이하')
