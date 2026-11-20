"""공용 관측·재시도(275기). 새 스크립트는 print 대신 이것을 쓴다(옛 스크립트는 고칠 때 옮긴다 — docs/PIPELINE_CONVENTIONS.md).

event('photo.fetched', city='seoul', n=12)  → stderr 에 JSON 한 줄(ts·event·필드)
metric('rows_written', 1923, job='ai-fill') → Step Summary 표 한 줄 + $RUNNER_TEMP/metrics.jsonl 한 줄
retry(fn, tries=3, base=2.0, retry_on=(OSError,)) → 실패하면 base·2^n 초 쉬고 다시. 마지막 실패는 그대로 올린다.
http_get(url, ua, timeout=60, tries=1) → bytes. tries 1 = 옛 urlopen 한 번과 같다.
키·토큰·메일은 필드에 넣지 않는다(event 가 이름에 key/token/secret/password 가 든 필드를 '***' 로 가린다).
"""
import json, os, sys, time, urllib.request

_HIDE = ('key', 'token', 'secret', 'password')


def event(name, **fields):
    rec = {'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'event': name}
    for k, v in fields.items():
        rec[k] = '***' if any(h in k.lower() for h in _HIDE) else v
    print(json.dumps(rec, ensure_ascii=False, default=str), file=sys.stderr, flush=True)
    return rec


def metric(name, value, **labels):
    rec = {'metric': name, 'value': value, **labels}
    tmp = os.environ.get('RUNNER_TEMP')
    if tmp:
        with open(os.path.join(tmp, 'metrics.jsonl'), 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    summ = os.environ.get('GITHUB_STEP_SUMMARY')
    if summ:
        with open(summ, 'a', encoding='utf-8') as f:
            f.write(f"| {name} | {value} | {' '.join(f'{k}={v}' for k, v in labels.items())} |\n")
    return rec


def retry(fn, tries=3, base=2.0, retry_on=(OSError,), sleep=time.sleep):
    for i in range(1, tries + 1):
        try:
            return fn()
        except retry_on as e:
            if i == tries:
                raise
            event('retry.attempt_failed', attempt=i, error=type(e).__name__)
            sleep(base * (2 ** (i - 1)))


def http_get(url, ua, timeout=60, tries=1):
    def once():
        req = urllib.request.Request(url, headers={'User-Agent': ua})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    return retry(once, tries=tries)
