#!/usr/bin/env python3
"""트립루티 fx-daily — 참고 환율을 하루 1번 받아 JSON 1장으로 만든다(표준 라이브러리만, 키 없음).

출처: Frankfurter v2 (https://api.frankfurter.dev/v2/rates) — 여러 중앙은행이 공시한 기준 환율. 실시간 시세가 아니다.
출력: {schema, base:'KRW', date, krw_per:{통화: 그 통화 1 당 원}, held:[...], fetched_at, source}
  앱(tl_fx.dart)은 date(기준일)와 krw_per 만 읽고, 화면에 '참고 환율 · 기준일 ○○'으로 적는다.
방어: 통화가 MIN_QUOTES 보다 적게 오면 저장하지 않는다(앱은 어제 파일을 계속 쓴다).
      어제 값과 MAX_JUMP 넘게 다른 통화는 어제 값을 유지하고 held 에 적는다.
쓰는 법: python3 scripts/fx_daily.py --out latest.json [--prev prev.json] [--from-file resp.json]
종료 코드: 0 저장함 / 2 받기·검사 실패(파일을 만들지 않음)
"""
import argparse
import datetime as dt
import json
import re
import sys
import urllib.request

URL = 'https://api.frankfurter.dev/v2/rates'
BASE = 'KRW'
QUOTES = ['JPY', 'USD', 'EUR', 'CNY', 'TWD', 'HKD', 'MOP', 'THB', 'VND', 'SGD', 'PHP', 'MYR', 'IDR', 'GBP', 'AUD', 'CAD', 'CHF', 'NZD', 'TRY', 'CZK', 'MNT']
MIN_QUOTES = 5
MAX_JUMP = 0.3
MAX_AGE_DAYS = 10  # 출처의 기준일이 이보다 오래면 고장으로 본다
SOURCE = 'Frankfurter v2 (api.frankfurter.dev) — 중앙은행 공시 기준 환율, 실시간 시세 아님'
_YMD = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def parse(rows):
    """Frankfurter 응답(list) → (date, {통화: 원}) 또는 None. 순수 함수."""
    if not isinstance(rows, list):
        return None
    krw_per, dates = {}, []
    for r in rows:
        if not isinstance(r, dict) or r.get('base') != BASE:
            continue
        q, rate, d = r.get('quote'), r.get('rate'), r.get('date')
        if q not in QUOTES or isinstance(rate, bool) or not isinstance(rate, (int, float)) or not rate > 0:
            continue
        if not isinstance(d, str) or not _YMD.match(d):
            continue
        v = 1.0 / rate
        if v != v or v in (float('inf'), float('-inf')):
            continue
        krw_per[q] = float('%.6g' % v)
        dates.append(d)
    if len(krw_per) < MIN_QUOTES:
        return None
    return max(dates), krw_per


def guard(krw_per, prev):
    """어제 값과 MAX_JUMP 넘게 다른 통화는 어제 값을 유지. → (krw_per, held)"""
    old = prev.get('krw_per') if isinstance(prev, dict) else None
    out, held = dict(krw_per), []
    if isinstance(old, dict):
        for q, v in krw_per.items():
            o = old.get(q)
            if isinstance(o, (int, float)) and not isinstance(o, bool) and o > 0 and abs(v - o) / o > MAX_JUMP:
                out[q] = o
                held.append(q)
    return out, sorted(held)


def build(rows, prev, now):
    """→ 문서(dict) 또는 None."""
    p = parse(rows)
    if p is None:
        return None
    date, krw_per = p
    try:
        age = (now.date() - dt.date.fromisoformat(date)).days
    except ValueError:
        return None
    if age > MAX_AGE_DAYS or age < -1:
        return None
    if isinstance(prev, dict) and isinstance(prev.get('date'), str) and prev['date'] > date:
        return None  # 이미 더 새 기준일을 갖고 있다
    krw_per, held = guard(krw_per, prev)
    return {
        'schema': 1, 'base': BASE, 'date': date,
        'krw_per': dict(sorted(krw_per.items())), 'held': held,
        'fetched_at': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'source': SOURCE,
    }


def fetch():
    req = urllib.request.Request('%s?base=%s&quotes=%s' % (URL, BASE, ','.join(QUOTES)), headers={'accept': 'application/json', 'user-agent': 'fx-daily/1.0'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read(200000).decode('utf-8'))


def _load(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--prev')
    ap.add_argument('--from-file')
    a = ap.parse_args(argv)
    try:
        rows = _load(a.from_file) if a.from_file else fetch()
    except Exception as e:  # 받기 실패: 이름만 적는다
        print('fx: fetch failed (%s)' % type(e).__name__)
        return 2
    doc = build(rows, _load(a.prev) if a.prev else None, dt.datetime.now(dt.timezone.utc).replace(tzinfo=None))
    if doc is None:
        print('fx: 검사 실패 — 저장하지 않음')
        return 2
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, separators=(',', ':'))
    print('fx: date=%s n=%d held=%s' % (doc['date'], len(doc['krw_per']), ','.join(doc['held']) or '-'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
