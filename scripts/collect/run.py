#!/usr/bin/env python3
"""수집 1단: 계획(plan) → 조각 실행(job) → 요약(summary).

  python3 scripts/collect/run.py plan    --store gs://<bucket>/collect/v1            # GITHUB_OUTPUT 에 matrix 를 쓴다
  python3 scripts/collect/run.py job     --store ... --source wikivoyage --shard en  # 조각 하나(50분 안)
  python3 scripts/collect/run.py summary --store ... --out collect/status.json       # 상태 모으기 + 실패 3회 연속 경보

계획: 매시 최대 18개 조각(동시 실행 18 — 무료 플랜 20 안쪽). 칸의 70% 는 출처 수집, 30% 는 발견(발견기·씨앗·웹 탐색기).
  같은 몫 안에서는 마지막 성공이 가장 오래된 조각부터(라운드로빈). 갱신 주기가 안 된 조각, 키(Secret)가 없는 출처는 넣지 않는다.
상태: 조각마다 Storage state/<출처>/<조각>.json (하루 요청 수·이어받기 위치·연속 실패). 저장소에는 summary 의 작은 status.json 만 커밋.
"""
import argparse, datetime, json, os, subprocess, sys, tempfile, time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import Blocked, Http, Stop, Store, now, region_rank, shard_cc, today  # noqa: E402
import collectors as C  # noqa: E402

REG = os.path.join(HERE, '..', '..', 'sources', 'registry.yaml')
MAX_JOBS, DISC_SHARE = int(os.environ.get('COLLECT_MAX_JOBS') or 18), 0.3  # 저장소 변수 COLLECT_MAX_JOBS 로 조절(가공 조각과 합쳐 동시 20 안쪽 — docs/THROUGHPUT.md)
RETRY_FAST, RETRY_SLOW_H = 3, 6  # 연속 실패 3회 미만 = 다음 회차에 맨 먼저 다시. 3회 이상 = 6시간에 한 번만(다른 조각 자리를 막지 않게)
REFRESH_DAYS = {'hourly': 0, 'daily': 1, 'weekly': 7, 'monthly': 1}  # monthly 는 날마다 새 릴리스만 확인(수집기가 같으면 곧 끝냄)
OVT_FIRST = int(os.environ.get('COLLECT_OVT_FIRST') or 6)  # 매시 Overture 먼저 줄 자리(저장소 변수로 조절) — 229 ÷ 6 ≈ 38시간
HEAVY_CAP = {'overture_places': 8, 'fsq_os_places': 6, 'wikivoyage': 2, 'commons_photos': 1}  # 덤프 2종: 러너 1대 = 조각 1개라 디스크·메모리는 서로 무관. 229개 나라 조각 ÷ 8 ≈ 29시간  # 사진: 위키미디어 '한 번에 한 요청' — 한 시간에 조각 1개  # 한 시간에 같은 큰 출처 조각 상한(러너 디스크·메모리)


def load_registry(path=REG):
    import yaml
    return yaml.safe_load(open(path, encoding='utf-8'))


def shards(src):
    s = src.get('shards')
    if isinstance(s, list):
        return [str(x) for x in s]
    if isinstance(s, int):
        return [str(i) for i in range(s)]
    if s == 'countries':
        reg = json.load(open(os.path.join(C.TOOLS, 'world_regions.json'), encoding='utf-8'))
        return [f'{r}:{cc}' for r, v in reg.items() for cc in v['cc']]
    return ['0']


def _days_since(d):
    if not d:
        return 10 ** 6
    return (datetime.date.fromisoformat(today()) - datetime.date.fromisoformat(d[:10])).days


def _hours_since(ts):
    if not ts:
        return 10 ** 6
    return (now() - datetime.datetime.fromisoformat(ts)).total_seconds() / 3600


def plan(reg, index, env=os.environ, max_jobs=MAX_JOBS, demand=None):
    """demand = {cc: 사용자 검색 수}(Storage demand.json, 없으면 None) — 같은 날짜끼리는 많이 찾는 나라 조각부터(sources/region_priority.json)."""
    col, disc = [], []
    for s in reg['sources']:
        if s.get('status') != 'active' or s.get('script') not in C.REGISTRY:
            continue
        if s.get('secret') and env.get('HAS_' + s['secret']) != 'true':
            continue
        need = REFRESH_DAYS.get(s.get('refresh', 'daily'), 1)
        for rk, sh in enumerate(shards(s)):
            st = index.get(f"{s['id']}/{sh}", {})
            if st.get('req_date') == today() and st.get('req', 0) >= int(s.get('daily_limit', 500)):
                continue  # 오늘 한도 다 씀
            if need and _days_since(st.get('last_ok')) < need:
                continue
            fs = int(st.get('fail_streak') or 0)
            if fs >= RETRY_FAST and _hours_since(st.get('last_run')) < RETRY_SLOW_H:
                continue  # 계속 실패하는 조각은 6시간 쉬고 다시(실패 기록은 status·이슈에 남는다)
            first = '' if 0 < fs < RETRY_FAST else (st.get('last_ok') or ' ')  # 방금 실패한 조각을 맨 앞에(자동 재시도)
            pr = region_rank(shard_cc(sh), demand)  # 11/18: 같은 날짜면 사용자가 많이 찾는 나라 먼저, 그다음 전 세계
            (disc if s['script'] in C.DISCOVERY else col).append((first, pr, s['id'], rk, sh))  # rk: 등록부에 적힌 조각 순서(첫 회차에 앞쪽부터)
    nd = max(1, int(max_jobs * DISC_SHARE)) if disc else 0
    pick, used = [], {}

    def take(lst, n):
        for _, _pr, sid, _rk, sh in sorted(lst):
            if n <= 0:
                break
            if used.get(sid, 0) >= HEAVY_CAP.get(sid, 99):
                continue
            used[sid] = used.get(sid, 0) + 1
            pick.append({'source': sid, 'shard': sh}); n -= 1

    take(disc, nd)
    # (11/18 252기) Overture 는 229조각 중 3조각(11/17) — 같은 날짜·같은 나라 순위면 이름순으로 fsq 가 앞서 Overture 는 남는 자리만 받았다.
    #  아직 받지 못한 Overture 조각이 있으면 매시 OVT_FIRST 자리를 먼저 준다(러너 1대 = 조각 1개라 디스크는 조각끼리 무관, 동시 실행 수는 그대로).
    ovt = [x for x in col if x[2] == 'overture_places' and x[0] in ('', ' ')]  # '' = 방금 실패해 다시 하는 조각
    if ovt:
        take(ovt, min(OVT_FIRST, max_jobs - len(pick)))
    take([x for x in col if {'source': x[2], 'shard': x[4]} not in pick], max_jobs - len(pick))
    if len(pick) < max_jobs:  # 수집 몫이 남으면 발견에 더 준다(놀리지 않음)
        rest = [x for x in disc if {'source': x[2], 'shard': x[4]} not in pick]
        take(rest, max_jobs - len(pick))
    return pick


class Ctx:
    def __init__(self, reg, src, shard, store, st, hosts, deadline, http=None):
        self.reg, self.src, self.shard, self.store, self.st, self.deadline = reg, src, shard, store, st, deadline
        self.nshards = len(shards(src))
        self.http = http or Http(src, st, hosts, deadline=deadline)
        self.counts, self.notes, self.seq, self.skips = {}, [], 0, 0  # skips: 건너뛴 사이트 수(관측)
        self.stamp = now().strftime('%H%M%S')

    def emit(self, dataset, rows):
        rows = [r for r in rows if r]
        if not rows:
            return
        self.seq += 1
        key = f"raw/{self.src['id']}/{today()}/{self.shard.replace(':', '~')}/{self.stamp}-{self.seq:04d}.json.gz"
        self.store.put_json(key, {'schema': 'collect-raw-1', 'source': self.src['id'], 'dataset': dataset, 'shard': self.shard,
                                  'license': self.src.get('license'), 'attribution': self.src.get('attribution'), 'fetched': now().isoformat(), 'rows': rows})
        self.counts[dataset] = self.counts.get(dataset, 0) + len(rows)

    def tick(self):
        if time.time() > self.deadline:
            raise Stop('TIME')

    def note(self, m):
        self.notes.append(str(m)[:200])


def job(reg, store, source, shard, budget_min=50, opener=None, sleep=time.sleep):
    src = next(s for s in reg['sources'] if s['id'] == source)
    if src.get('status') != 'active':
        raise SystemExit(f'{source}: status {src.get("status")} — 수집하지 않음')
    skey = f"state/{source}/{shard.replace(':', '~')}.json"
    st = store.get_json(skey, {}) or {}
    hosts = st.setdefault('hosts', {})
    dl = time.time() + budget_min * 60
    ctx = Ctx(reg, src, shard, store, st, hosts, dl, http=Http(src, st, hosts, opener=opener, sleep=sleep, deadline=dl))
    t0, err, stop = time.time(), None, None
    try:
        C.REGISTRY[src['script']](ctx)
    except Stop as e:
        stop = str(e)
    except Blocked as e:
        stop = 'BLOCKED:' + str(e)
    except Exception as e:  # 다른 출처는 계속(조각 하나만 실패)
        fr = traceback.extract_tb(e.__traceback__)  # 어디서 났는지(파일:줄)만 — 값·주소는 싣지 않음. 우리 코드의 마지막 줄을 고른다
        tb = [f for f in fr if os.path.dirname(os.path.abspath(f.filename)) == HERE][-1:] or fr[-1:]
        err = f'{type(e).__name__}: {str(e)[:160]}' + (f' @{os.path.basename(tb[0].filename)}:{tb[0].lineno}' if tb else '')
    st['last_run'] = now().isoformat()
    st['req_date'] = st.get('date')
    if err:
        st['fail_streak'] = st.get('fail_streak', 0) + 1
        st['last_err'] = err
    else:
        st['fail_streak'] = 0
        if stop in (None, 'LIMIT'):  # 끝까지 했거나 오늘 한도를 다 씀. TIME·BLOCKED 는 다음 회차에 이어서
            st['last_ok'] = today()
    st['last'] = {'counts': ctx.counts, 'stop': stop, 'err': err, 'sec': round(time.time() - t0), 'notes': ctx.notes[-5:], 'skips': ctx.skips}
    store.put_json(skey, st)
    res = {'source': source, 'shard': shard, **st['last'], 'req': st.get('req', 0), 'fail_streak': st['fail_streak']}
    summ = os.environ.get('GITHUB_STEP_SUMMARY')
    if summ:
        with open(summ, 'a', encoding='utf-8') as f:
            f.write(f"### {source} / {shard}\n- 행: {ctx.counts or 0} · 요청 {res['req']} · {res['sec']}초 · 멈춤 {stop or '-'} · 오류 {err or '-'}\n")
    return res


def load_states(store):
    """state/ 아래 모든 조각 상태 → {출처/조각: 상태}. gs 면 한 번에 내려받는다."""
    out = {}
    if store.gs:
        d = tempfile.mkdtemp()
        subprocess.run(['gcloud', 'storage', 'cp', '-r', '-q', f'{store.root}/state', d], capture_output=True)
        base = os.path.join(d, 'state')
        for dp, _, fs in os.walk(base):
            for f in fs:
                if f.endswith('.json') and dp != base:
                    out[f"{os.path.basename(dp)}/{f[:-5]}"] = json.load(open(os.path.join(dp, f)))
        return out
    for k in store.list('state/'):
        parts = k.split('/')
        if len(parts) == 3 and k.endswith('.json'):
            out[f'{parts[1]}/{parts[2][:-5]}'] = store.get_json(k, {})
    return out


def summary(reg, store, out_path, alert_path=None):
    states = load_states(store)
    index = {}
    for k, st in states.items():
        sid, sh = k.split('/', 1)
        index[f"{sid}/{sh.replace('~', ':')}"] = {
            'last_ok': st.get('last_ok'), 'req': st.get('req', 0), 'req_date': st.get('req_date'), 'fail_streak': st.get('fail_streak', 0), 'last_run': st.get('last_run')}
    store.put_json('index.json', index)
    by = {}
    for k, st in states.items():
        sid = k.split('/', 1)[0]
        b = by.setdefault(sid, {'shards': 0, 'ok_today': 0, 'rows_last': 0, 'failing': 0})
        b['shards'] += 1
        b['ok_today'] += st.get('last_ok') == today()
        b['rows_last'] += sum((st.get('last') or {}).get('counts', {}).values())
        b['failing'] += st.get('fail_streak', 0) >= 3
    status = {'updated': now().strftime('%Y-%m-%dT%H:%MZ'), 'sources': by,
              'registry': {'active': sum(1 for s in reg['sources'] if s.get('status') == 'active'),
                           'candidate': sum(1 for s in reg['sources'] if s.get('status') == 'candidate')}}
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    json.dump(status, open(out_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, sort_keys=True)
    bad = [f"- {k}: {st.get('fail_streak')}회 연속 실패 — {st.get('last_err')}" for k, st in sorted(states.items()) if st.get('fail_streak', 0) >= 3]
    if alert_path and bad:
        open(alert_path, 'w', encoding='utf-8').write('수집 조각이 3회 연속 실패했습니다(자동 생성).\n\n' + '\n'.join(bad[:50]) + '\n')
    return status, bad


def get_index(store):
    return store.get_json('index.json', None) or {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['plan', 'job', 'summary'])
    ap.add_argument('--store', required=True)
    ap.add_argument('--registry', default=REG)
    ap.add_argument('--source'); ap.add_argument('--shard', default='0')
    ap.add_argument('--budget-min', type=float, default=50)
    ap.add_argument('--out', default='collect/status.json'); ap.add_argument('--alert', default='')
    a = ap.parse_args()
    reg, store = load_registry(a.registry), Store(a.store)
    if a.cmd == 'plan':
        usage = store.get_json('guards/usage.json', None) or {}  # coverage-report 의 저장 용량·비용 측정(scripts/storage_guard.py)
        if usage.get('pause_collect'):
            print('matrix={"include":[]}'); print('any=false')
            print(f"저장 상한 도달({usage.get('total_gib')} GiB / {usage.get('cap_gib')} GiB · ${usage.get('est_usd_month')}/월) — 새 원본 수집 멈춤", file=sys.stderr)
            return 0
        p = plan(reg, get_index(store), demand=(store.get_json('demand.json', None) or {}).get('cc'))
        print(f"matrix={json.dumps({'include': p}, separators=(',', ':'))}")
        print(f"any={'true' if p else 'false'}")
        print(f'계획 {len(p)}개 조각', file=sys.stderr)
    elif a.cmd == 'job':
        r = job(reg, store, a.source, a.shard, a.budget_min)
        print(json.dumps({k: r[k] for k in ('source', 'shard', 'counts', 'stop', 'err', 'sec', 'req')}, ensure_ascii=False))
        return 1 if r['err'] else 0
    else:
        st, bad = summary(reg, store, a.out, a.alert or None)
        print(f"출처 {len(st['sources'])}개, 연속 실패 조각 {len(bad)}개")
    return 0


if __name__ == '__main__':
    sys.exit(main())
