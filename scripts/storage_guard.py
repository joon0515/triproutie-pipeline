#!/usr/bin/env python3
"""저장 용량·비용 상한(읽기 전용 측정) — coverage-report 가 하루 한 번 부른다.

  python3 scripts/storage_guard.py --bucket <bucket> --storage out/storage.json --usage out/usage.json --alert out/alert.md [--measured du.json]

- Storage 사용량은 `gcloud storage du -s`(읽기만)로 잰다. 버킷·권한(IAM)은 바꾸지 않는다.
- 상한은 저장소 변수로: STORAGE_CAP_GIB(기본 500) · COST_CAP_USD_MONTH(기본 15) · STORAGE_USD_PER_GIB(기본 0.026, Cloud Storage Standard 단가 어림)
  · STORAGE_WARN_RATIO(기본 0.8) · STORAGE_PAUSE_RATIO(기본 1.0).
- 경고선(80%)을 넘으면 alert 파일을 만든다 → 워크플로가 이슈 '저장 용량·비용 경고'에 올린다.
- 상한(100%)을 넘으면 usage.json 의 pause_collect=true → collect 계획(run.py plan)이 새 원본 수집을 멈춘다(가공·게시·갱신 판단은 계속).
  상한을 올리거나 원본을 정리하면 다음 측정에서 풀린다. 비용은 저장 용량 어림값이다(요청·전송 요금은 Billing 에서만 정확히 보인다).
"""
import argparse, json, os, subprocess, sys

PREFIXES = (('수집 원본 raw', 'collect/v1/raw'), ('가공 띠 bands', 'collect/v1/processed/bands'), ('가공 층 out', 'collect/v1/processed/out'),
            ('게시 places/v1', 'places/v1'), ('TourAPI', 'tourapi/v1'), ('패턴 patterns', 'patterns/v1'))


def du(bucket, prefix=''):
    try:
        r = subprocess.run(['gcloud', 'storage', 'du', '-s', f'gs://{bucket}/{prefix}'.rstrip('/')], capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        return None
    try:
        return int(r.stdout.split()[0])
    except (IndexError, ValueError):
        return None


def measure(bucket):
    parts = {name: du(bucket, p) for name, p in PREFIXES}
    total = du(bucket)
    if total is None:  # 버킷 전체를 못 재면 잰 칸의 합(하한)
        total = sum(v for v in parts.values() if v) or None
    return parts, total


def evaluate(parts, total, env=os.environ):
    f = lambda k, d: float(env.get(k) or d)
    cap, cost_cap, unit = f('STORAGE_CAP_GIB', 500), f('COST_CAP_USD_MONTH', 15), f('STORAGE_USD_PER_GIB', 0.026)
    warn_r, pause_r = f('STORAGE_WARN_RATIO', 0.8), f('STORAGE_PAUSE_RATIO', 1.0)
    gib = total / 2 ** 30 if total is not None else None
    usd = gib * unit if gib is not None else None
    ratio = max(gib / cap, usd / cost_cap) if gib is not None else None
    u = {'schema': 'usage-1', 'total_gib': round(gib, 2) if gib is not None else None, 'cap_gib': cap, 'est_usd_month': round(usd, 2) if usd is not None else None,
         'cost_cap_usd_month': cost_cap, 'usd_per_gib': unit, 'ratio': round(ratio, 3) if ratio is not None else None,
         'warn': bool(ratio is not None and ratio >= warn_r), 'pause_collect': bool(ratio is not None and ratio >= pause_r),
         'parts_gib': {k: (round(v / 2 ** 30, 2) if v is not None else None) for k, v in parts.items()}}
    alert = None
    if u['warn']:
        alert = (f"- 저장 용량 {u['total_gib']} GiB / 상한 {cap:g} GiB · 비용 어림 ${u['est_usd_month']}/월 / 상한 ${cost_cap:g} — {100 * ratio:.0f}%"
                 + (" → **새 원본 수집 멈춤**(collect 계획이 조각을 고르지 않음, 가공·게시는 계속)" if u['pause_collect'] else f" (상한 {100 * pause_r:.0f}% 에서 새 원본 수집을 멈춤)")
                 + '\n  - 칸별: ' + ', '.join(f"{k} {v if v is not None else '미측정'} GiB" for k, v in u['parts_gib'].items())
                 + '\n  - 풀기: 저장소 변수 STORAGE_CAP_GIB·COST_CAP_USD_MONTH 를 올리거나 오래된 원본(collect/v1/raw/<출처>/<날짜>)을 정리. 권한·버킷 설정은 바꾸지 않음.')
    return u, alert


def human(parts, total):
    out = {k: (f'{v / 2 ** 30:.2f} GiB' if v is not None else '미측정') for k, v in parts.items()}
    out['버킷 전체'] = f'{total / 2 ** 30:.2f} GiB' if total is not None else '미측정'
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bucket', required=True)
    ap.add_argument('--measured', default='', help='시험용: {"parts": {이름: 바이트}, "total": 바이트}')
    ap.add_argument('--storage', default=''); ap.add_argument('--usage', default=''); ap.add_argument('--alert', default='')
    a = ap.parse_args()
    if a.measured:
        m = json.load(open(a.measured, encoding='utf-8')); parts, total = m['parts'], m.get('total')
    else:
        parts, total = measure(a.bucket)
    u, alert = evaluate(parts, total)
    if a.storage:
        json.dump(human(parts, total), open(a.storage, 'w', encoding='utf-8'), ensure_ascii=False)
    if a.usage:
        json.dump(u, open(a.usage, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    if a.alert and alert:
        open(a.alert, 'w', encoding='utf-8').write(alert + '\n')
    print(f"저장 {u['total_gib']} GiB / {u['cap_gib']:g} GiB · 비용 어림 ${u['est_usd_month']}/월 · 비율 {u['ratio']} · 경고 {u['warn']} · 수집 멈춤 {u['pause_collect']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
