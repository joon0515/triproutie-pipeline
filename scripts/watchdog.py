#!/usr/bin/env python3
"""상시 가동 감시(watchdog) — 매시간 각 워크플로의 마지막 성공 시각을 보고, 기준을 넘기면 workflow_dispatch 로 다시 건다.

  python3 scripts/watchdog.py [--config sources/watchdog.json] [--dry-run] [--keepalive] [--md 요약.md]

GitHub REST API 만 쓴다(GITHUB_TOKEN — actions: write · issues: write). 하루 호출 수는 한 번에 30회 안팎(토큰 한도 1,000회/시간).
판단(워크플로마다, 시험: tests/test_watchdog.py):
  - 상태가 disabled_inactivity(공개 저장소 60일 무활동 규칙)면 enable 로 되살린다. disabled_manually(사람이 끔)는 건드리지 않는다.
  - 하루 한 번(keepalive_hour_utc) 켜진 워크플로 전부에 enable 을 다시 불러 60일 무활동 시계를 되돌린다(규칙: GitHub 문서 'Disabling and enabling a workflow').
  - 돌고 있거나 대기 중인 실행이 있으면 걸지 않는다(concurrency 묶음의 대기 실행은 1개뿐 — 새로 걸면 기존 대기 실행이 취소된다).
  - 마지막 성공이 max_age_h 보다 오래되었고, 마지막 실행이 cooldown_min 안이 아니면 다시 건다. 연속 실패 중이면 fail_backoff_h 간격으로만.
  - 연속 실패가 fail_streak_alert(3)회 이상이면 이슈 '자동화 실패 모음'에 한 번 올린다(같은 실패 묶음은 한 번만 — 숨은 표시로 중복 막음).
  - GitHub Actions 캐시·아티팩트 용량이 경고선을 넘으면 이슈 '저장 용량·비용 경고'에 올린다.
값·비밀은 출력하지 않는다(워크플로 이름·시각·개수만).
"""
import argparse, datetime, json, os, sys, urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
ACTIVE = ('queued', 'in_progress', 'waiting', 'pending', 'requested')
FAIL = ('failure', 'timed_out', 'startup_failure')
NEUTRAL = ('cancelled', 'skipped', 'neutral', 'stale', 'action_required', None)


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def ts(s):
    return datetime.datetime.fromisoformat(s.replace('Z', '+00:00')) if s else None


class Rest:
    """GitHub REST 아주 작은 묶음. 시험은 같은 모양(call)의 가짜를 넣는다."""

    def __init__(self, repo, token, api=None, opener=urllib.request.urlopen):
        self.base = f"{(api or 'https://api.github.com').rstrip('/')}/repos/{repo}"
        self.token, self.opener, self.calls = token, opener, 0

    def call(self, method, path, body=None, query=None):
        url = self.base + path + ('?' + urllib.parse.urlencode(query) if query else '')
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'triproutie-watchdog',
            **({'Authorization': f'Bearer {self.token}'} if self.token else {}), **({'Content-Type': 'application/json'} if data else {})})
        self.calls += 1
        try:
            with self.opener(req, timeout=60) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            return e.code, None


# ---------- 판단(네트워크 없음) ----------
def streak(runs):
    """완료된 실행(최신 먼저)에서 연속 실패 수와 그 묶음의 첫 실패 실행 id. 취소·건너뜀은 세지 않고 지나간다."""
    n, first = 0, None
    for r in runs:
        if r.get('status') != 'completed' or r.get('conclusion') in NEUTRAL:
            continue
        if r.get('conclusion') in FAIL:
            n, first = n + 1, r.get('id')
            continue
        break
    return n, first


def decide(wf, runs, now, cfg):
    """wf = 설정 한 줄, runs = 그 워크플로의 main 실행(최신 먼저). 돌려줌 dict(action, why, …)."""
    runs = [r for r in runs if r.get('event') != 'pull_request']
    ok = next((r for r in runs if r.get('status') == 'completed' and r.get('conclusion') == 'success'), None)
    last_ok = ts(ok.get('updated_at') or ok.get('created_at')) if ok else None
    age_h = (now - last_ok).total_seconds() / 3600 if last_ok else None
    n_fail, first_fail = streak(runs)
    out = {'file': wf['file'], 'last_ok': last_ok.strftime('%Y-%m-%dT%H:%MZ') if last_ok else None, 'age_h': round(age_h, 1) if age_h is not None else None,
           'max_age_h': wf['max_age_h'], 'fail_streak': n_fail, 'first_fail': first_fail, 'alert': n_fail >= int(cfg.get('fail_streak_alert', 3))}
    if any(r.get('status') in ACTIVE for r in runs):
        return {**out, 'action': 'running', 'why': '실행 중·대기 중 — 새로 걸지 않음'}
    if age_h is not None and age_h <= wf['max_age_h']:
        return {**out, 'action': 'ok', 'why': f'마지막 성공 {age_h:.1f}시간 전'}
    last = ts(runs[0].get('created_at')) if runs else None
    since_last_min = (now - last).total_seconds() / 60 if last else None
    gap_min = float(cfg.get('fail_backoff_h', 6)) * 60 if n_fail >= int(cfg.get('fail_streak_alert', 3)) else float(cfg.get('cooldown_min', 50))
    if since_last_min is not None and since_last_min < gap_min:
        return {**out, 'action': 'wait', 'why': f'마지막 실행 {since_last_min:.0f}분 전 — {gap_min:.0f}분 지나야 다시 건다'}
    why = '성공 기록 없음' if age_h is None else f'마지막 성공 {age_h:.1f}시간 전(기준 {wf["max_age_h"]}시간)'
    return {**out, 'action': 'dispatch', 'why': why}


def keepalive_due(now, cfg, force=False):
    return force or now.hour == int(cfg.get('keepalive_hour_utc', 3))


def marker(file, first_fail):
    return f'<!-- watchdog:{file}:{first_fail} -->'


# ---------- 실행 ----------
def find_issue(api, title):
    st, items = api.call('GET', '/issues', query={'state': 'open', 'per_page': 100})
    for i in (items or []) if st == 200 else []:
        if i.get('title') == title and 'pull_request' not in i:
            return i['number']
    return None


def post_issue(api, title, body, mark=None, dry=False, intro=''):
    """제목이 같은 열린 이슈 하나에 댓글로 누적(없으면 만든다). mark 가 최근 댓글·본문에 이미 있으면 건너뜀."""
    n = find_issue(api, title)
    if n and mark:
        since = (utcnow() - datetime.timedelta(days=30)).strftime('%Y-%m-%dT%H:%M:%SZ')
        st, cs = api.call('GET', f'/issues/{n}/comments', query={'since': since, 'per_page': 100})
        if any(mark in (c.get('body') or '') for c in (cs or [])):
            return 'dup'
    text = body + (f'\n{mark}' if mark else '')
    if dry:
        return 'dry'
    if n:
        api.call('POST', f'/issues/{n}/comments', {'body': text})
        return f'comment #{n}'
    api.call('POST', '/issues', {'title': title, 'body': (intro + '\n\n' if intro else '') + text})
    return 'new'


def capacity(api, cfg):
    """Actions 캐시·아티팩트 용량(읽기만). 돌려줌 (값 dict, 경고 list)."""
    v, warn = {}, []
    st, c = api.call('GET', '/actions/cache/usage')
    if st == 200 and c:
        v['cache_gib'] = round(c.get('active_caches_size_in_bytes', 0) / 2 ** 30, 2)
        v['cache_n'] = c.get('active_caches_count', 0)
    tot, n = 0, 0
    for page in (1, 2, 3):
        st, a = api.call('GET', '/actions/artifacts', query={'per_page': 100, 'page': page})
        arts = (a or {}).get('artifacts') or []
        tot += sum(x.get('size_in_bytes', 0) for x in arts if not x.get('expired'))
        n += sum(1 for x in arts if not x.get('expired'))
        if len(arts) < 100:
            break
    v['artifact_mib'], v['artifact_n'] = round(tot / 2 ** 20, 1), n
    if v.get('cache_gib', 0) >= float(cfg.get('cache_warn_gib', 8)):
        warn.append(f"Actions 캐시 {v['cache_gib']} GiB — 저장소 한도 10 GiB 의 경고선 {cfg.get('cache_warn_gib', 8)} GiB 넘음(오래된 캐시는 7일 미사용 시 GitHub 가 지움)")
    if v['artifact_mib'] >= float(cfg.get('artifact_warn_mib', 400)):
        warn.append(f"Actions 아티팩트 {v['artifact_mib']} MiB({n}개) — 경고선 {cfg.get('artifact_warn_mib', 400)} MiB 넘음(retention-days 줄이기)")
    return v, warn


def run(api, cfg, now=None, dry=False, force_keepalive=False, ref='main'):
    now = now or utcnow()
    st, wl = api.call('GET', '/actions/workflows', query={'per_page': 100})
    if st != 200:
        raise SystemExit(f'워크플로 목록을 못 읽음(HTTP {st})')
    by_path = {os.path.basename(w['path']): w for w in wl.get('workflows', [])}
    rep, acts = [], []
    keep = keepalive_due(now, cfg, force_keepalive)
    for name, w in sorted(by_path.items()):
        if w.get('state') == 'disabled_inactivity' or (keep and w.get('state') == 'active'):
            code = 204 if dry else api.call('PUT', f"/actions/workflows/{w['id']}/enable")[0]
            if w.get('state') == 'disabled_inactivity':
                acts.append(f'{name}: 60일 무활동으로 꺼져 있어 다시 켬(HTTP {code})')
                w['state'] = 'active' if code in (200, 204) else w['state']
            elif code not in (200, 204):
                acts.append(f'{name}: keepalive enable 실패(HTTP {code})')
    if keep:
        acts.append(f'keepalive: 켜진 워크플로 {sum(1 for w in by_path.values() if w.get("state") == "active")}개에 enable 다시 부름(60일 시계 되돌림)')
    for wf in cfg['workflows']:
        w = by_path.get(wf['file'])
        if not w:
            rep.append({'file': wf['file'], 'action': 'missing', 'why': '워크플로 파일 없음'}); continue
        if w.get('state') != 'active':
            rep.append({'file': wf['file'], 'action': 'off', 'why': f"상태 {w.get('state')} — 사람이 끈 것은 건드리지 않음"}); continue
        st, rr = api.call('GET', f"/actions/workflows/{w['id']}/runs", query={'branch': ref, 'per_page': 30, 'exclude_pull_requests': 'true'})
        if st != 200:
            rep.append({'file': wf['file'], 'action': 'error', 'why': f'실행 목록 HTTP {st}'}); continue
        d = decide(wf, (rr or {}).get('workflow_runs', []), now, cfg)
        if d['action'] == 'dispatch':
            body = {'ref': ref, **({'inputs': wf['inputs']} if wf.get('inputs') else {})}
            code = 204 if dry else api.call('POST', f"/actions/workflows/{w['id']}/dispatches", body)[0]
            d['why'] += f' → 다시 걸음(HTTP {code})'
            acts.append(f"{wf['file']}: {d['why']}")
        if d.get('alert') and d.get('first_fail'):
            line = (f"- {now.strftime('%Y-%m-%dT%H:%MZ')} · **watchdog** · `{wf['file']}` {d['fail_streak']}회 연속 실패"
                    f" (마지막 성공 {d.get('last_ok') or '없음'}) — 처리: {d['action']}")
            d['issue'] = post_issue(api, cfg.get('digest_title', '자동화 실패 모음'), line, marker(wf['file'], d['first_fail']), dry,
                                    intro='워크플로 실패를 한 줄씩 모은다(자동). 고친 뒤 닫으면 다음 실패 때 새로 열린다.')
        rep.append(d)
    cap, warn = capacity(api, cfg)
    if warn:
        body = f"- {now.strftime('%Y-%m-%d')} · watchdog(GitHub Actions 저장 공간)\n" + '\n'.join('  - ' + x for x in warn)
        cap['issue'] = post_issue(api, cfg.get('capacity_title', '저장 용량·비용 경고'), body, f"<!-- watchdog:capacity:{now.strftime('%Y-%m-%d')} -->", dry)
    return {'at': now.strftime('%Y-%m-%dT%H:%MZ'), 'dry': dry, 'workflows': rep, 'actions': acts, 'capacity': cap, 'capacity_warn': warn, 'api_calls': api.calls}


def to_md(res):
    L = [f"## watchdog {res['at']}{' (dry-run)' if res['dry'] else ''}", '| 워크플로 | 마지막 성공 | 경과(시간) | 기준 | 연속 실패 | 처리 | 이유 |', '|---|---|---|---|---|---|---|']
    for d in res['workflows']:
        L.append(f"| {d['file']} | {d.get('last_ok') or '-'} | {d.get('age_h') if d.get('age_h') is not None else '-'} | {d.get('max_age_h', '-')} | "
                 f"{d.get('fail_streak', '-')} | {d['action']} | {d['why']} |")
    L += ['', '- ' + ' · '.join(f'{k} {v}' for k, v in res['capacity'].items()), f"- API 호출 {res['api_calls']}회"]
    L += [f'- {a}' for a in res['actions']] + [f'- 경고: {w}' for w in res['capacity_warn']]
    return '\n'.join(L) + '\n'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=os.path.join(ROOT, 'sources', 'watchdog.json'))
    ap.add_argument('--repo', default=os.environ.get('GITHUB_REPOSITORY', 'joon0515/triproutie-pipeline'))
    ap.add_argument('--dry-run', action='store_true'); ap.add_argument('--keepalive', action='store_true')
    ap.add_argument('--md', default=''); ap.add_argument('--json', default='')
    a = ap.parse_args()
    cfg = json.load(open(a.config, encoding='utf-8'))
    api = Rest(a.repo, os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN'), os.environ.get('GITHUB_API_URL'))
    res = run(api, cfg, dry=a.dry_run, force_keepalive=a.keepalive)
    md = to_md(res)
    if a.md:
        open(a.md, 'w', encoding='utf-8').write(md)
    if a.json:
        json.dump(res, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(md)
    return 0


if __name__ == '__main__':
    sys.exit(main())
