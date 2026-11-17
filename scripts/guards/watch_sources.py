#!/usr/bin/env python3
"""출처 약관 변경·깨진 링크 감시 — 주 1회.

  python3 scripts/guards/watch_sources.py --state gs://<bucket>/collect/v1/guards/watch.json --md out.md

등록부(active·candidate)의 license_url 과 http(s) url 을 예절 HTTP(common.Http: 식별 UA·robots 실시간 확인·사이트당 1초 이상·금지 도메인 차단)로 받아
본문 글자만 남긴 sha256 을 지난번 값과 견준다. 바뀜 = 약관을 사람이 다시 읽어야 함, 4xx·5xx·접속 실패 = 깨진 링크, robots 금지 = 확인 못 함(받지 않음).
본문은 저장하지 않는다(해시·길이·상태만). 템플릿(<release> 등)이 든 주소는 건너뛴다.
"""
import argparse, datetime, hashlib, html, json, os, re, sys, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..', '..')
sys.path.insert(0, os.path.join(ROOT, 'scripts', 'collect'))
from common import Blocked, Http, Stop, Store  # noqa: E402

_TAG = re.compile(r'<(script|style|noscript)\b.*?</\1>|<[^>]+>', re.S | re.I)
_NOISE = re.compile(r'\b\d{1,2}:\d{2}(:\d{2})?\b|\b20\d\d-\d\d-\d\dT[\d:.Z+-]+|nonce="[^"]*"|csrf[^\s]*', re.I)


def norm_text(b):
    t = b.decode('utf-8', 'replace')
    t = _TAG.sub(' ', t)
    t = _NOISE.sub(' ', html.unescape(t))
    return re.sub(r'\s+', ' ', t).strip()


def targets(reg):
    out = {}
    for s in reg.get('sources', []):
        if s.get('status') not in ('active', 'candidate'):
            continue
        for k in ('license_url', 'url'):
            u = str(s.get(k) or '').split()[0] if s.get(k) else ''
            if u.startswith(('http://', 'https://')) and '<' not in u and not u.endswith(('.zip', '.bz2', '.gz', '.csv', '.xml', '.json', '.parquet')):
                out.setdefault(u, []).append(f"{s['id']}.{k}")
    return out


def watch(reg, prev, opener=None, sleep=None, today=None):
    """돌려줌 = (새 상태, 보고 dict)."""
    today = today or datetime.date.today().isoformat()
    kw = {'opener': opener} if opener else {}
    if sleep:
        kw['sleep'] = sleep
    http = Http({'id': 'watch', 'daily_limit': 400, 'interval_s': 2, 'timeout_s': 30, 'retries': 2}, {}, {}, **kw)
    state, rep = dict(prev or {}), {'changed': [], 'broken': [], 'robots': [], 'new': [], 'ok': 0}
    for url, who in sorted(targets(reg).items()):
        p = prev.get(url) if prev else None
        try:
            body = http.get(url)
        except Blocked as e:
            rep['robots' if str(e) in ('robots', 'forbidden domain') else 'broken'].append((url, who, str(e)))
            continue
        except urllib.error.HTTPError as e:
            rep['broken'].append((url, who, f'HTTP {e.code}'))
            state[url] = {**(p or {}), 'last_err': f'HTTP {e.code}', 'checked': today}
            continue
        except (IOError, Stop) as e:
            rep['broken'].append((url, who, type(e).__name__ + ':' + str(e)[:40]))
            continue
        t = norm_text(body)
        h = hashlib.sha256(t.encode()).hexdigest()[:16]
        if not p or not p.get('sha'):
            rep['new'].append((url, who, h))
        elif p['sha'] != h:
            rep['changed'].append((url, who, f"{p['sha']} → {h}, 글자 {p.get('len')} → {len(t)}"))
        else:
            rep['ok'] += 1
        state[url] = {'sha': h, 'len': len(t), 'checked': today, 'since': p.get('since') if p and p.get('sha') == h else today}
    return state, rep


def to_md(rep, today):
    L = [f'## 출처 약관·링크 감시 {today}', f"- 그대로 {rep['ok']} · 바뀜 {len(rep['changed'])} · 깨짐 {len(rep['broken'])} · robots/금지로 안 받음 {len(rep['robots'])} · 처음 봄 {len(rep['new'])}"]
    for name, k in (('약관·페이지가 바뀜 — 사람이 다시 읽고 LICENSE_AUDIT 갱신', 'changed'), ('깨진 링크', 'broken'), ('확인 못 함(robots·금지 도메인)', 'robots')):
        if rep[k]:
            L.append(f'### {name}')
            L += [f"- {u} ({', '.join(w)}) — {x}" for u, w, x in rep[k]]
    return '\n'.join(L) + '\n'


def main():
    import yaml
    ap = argparse.ArgumentParser()
    ap.add_argument('--registry', default=os.path.join(ROOT, 'sources', 'registry.yaml'))
    ap.add_argument('--state', required=True, help='gs://…/watch.json 또는 로컬 파일')
    ap.add_argument('--md', default='')
    a = ap.parse_args()
    root, key = a.state.rsplit('/', 1)
    store = Store(root)
    prev = store.get_json(key, {}) or {}
    reg = yaml.safe_load(open(a.registry, encoding='utf-8'))
    today = datetime.date.today().isoformat()
    state, rep = watch(reg, prev, today=today)
    store.put_json(key, state)
    md = to_md(rep, today)
    print(md)
    if a.md:
        open(a.md, 'w', encoding='utf-8').write(md)
    return 0


if __name__ == '__main__':
    sys.exit(main())
