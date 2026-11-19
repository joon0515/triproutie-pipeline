"""일정 패턴 학습기 — 허용된 출처에서 '숫자 분포'만 모은다.
  python3 scripts/patterns/learn.py run   --store gs://<bucket>/patterns/v1 [--gazetteer-dir DIR] [--only ID]
  python3 scripts/patterns/learn.py apply --model itinerary_model.json --patch itinerary_model_patch.json --out new_model.json
저장하는 것: patterns.<class>.json(분포) · itinerary_model_patch[.by-sa].json(생성기 모델 모양) · samples/*.json.gz(하루 숫자 표본 — 다음 실행 때 다시 받지 않기 위한 것,
도시 열쇠·나라·유형·숫자뿐) · state.json. 글·주소·ID·제목은 어디에도 저장하지 않는다.
"""
import argparse, glob, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'collect'))
sys.path.insert(0, HERE)
from common import Blocked, Http, Stop, Store, today  # noqa: E402
import sources as S  # noqa: E402
import stats as T  # noqa: E402
from places import Cities, Gazetteer  # noqa: E402

CLASSES = ('clean', 'by-sa')


def collect(store, srcs, gaz, cities, opener=None, sleep=time.sleep, budget_s=1500, log=print):
    """출처마다 표본을 새로 받거나(바뀐 것만) 앞 표본을 읽어 {출처 id: [표본]} 으로. 한 출처가 실패해도 나머지는 계속."""
    state = store.get_json('state.json', {}) or {}
    hosts, deadline, got = state.setdefault('hosts', {}), time.time() + budget_s, {}
    for src in srcs:
        if not S.usable(src):
            log(f"- {src['id']}: 건너뜀({src.get('status')}/{src.get('verdict')})")
            continue
        fn, all_s = S.FETCHERS[src['kind']], []
        for shard in src.get('langs') or ['-']:
            key = f"samples/{src['id']}~{shard}.json.gz"
            st = state.setdefault('src', {}).setdefault(f"{src['id']}~{shard}", {})
            http = Http(src, st, hosts, opener=opener, sleep=sleep, deadline=deadline)
            try:
                new = fn(src, http, gaz, cities, st, shard)
            except (Stop, Blocked, IOError, OSError, ValueError) as e:
                log(f"- {src['id']}~{shard}: 실패({type(e).__name__}: {e}) — 앞 표본 유지")
                new = None
            if new is None:
                new = store.get_json(key, []) or []
            else:
                store.put_json(key, new)
                st['at'] = today()
            log(f"- {src['id']}~{shard}: 표본 {len(new)}편 · {sum(len(s['days']) for s in new)}일")
            all_s += new
        got[src['id']] = all_s
    store.put_json('state.json', state)
    return got


def brand_snapshot(store, pat, keep=400, day=None):
    """(256기) 나라별 브랜드 언급 누적 수를 날짜별로 쌓는다(brand_hist.json) → shop_index.py 가 30일 증가율(트렌드)을 낸다. 숫자만."""
    snap = {cc: t['all']['brand_n'] for cc, t in (pat.get('groups') or {}).get('cc', {}).items()
            if isinstance(t.get('all'), dict) and t['all'].get('brand_n')}
    h = store.get_json('brand_hist.json', {}) or {}
    h[day or pat.get('built') or today()] = snap
    for d in sorted(h)[:-keep]:
        h.pop(d)
    store.put_json('brand_hist.json', h)
    return h


def build(store, srcs, got, min_n=T.MIN_N, log=print):
    by_id, outs = {s['id']: s for s in srcs}, {}
    for cls in CLASSES:
        ids = [i for i in got if by_id[i].get('license_class', 'clean') == cls]
        samples = [s for i in ids for s in got[i]]
        pat = T.aggregate(samples, cls, sorted({by_id[i]['attribution'] for i in ids if by_id[i].get('attribution')}),
                          {i: {'docs': len(got[i]), 'days': sum(len(s['days']) for s in got[i])} for i in ids})
        patch = T.to_model_patch(pat, min_n)
        store.put_json(f'patterns.{cls}.json', pat)
        if cls == 'clean':
            brand_snapshot(store, pat)
        store.put_json('itinerary_model_patch.json' if cls == 'clean' else f'itinerary_model_patch.{cls}.json', patch)
        outs[cls] = (pat, patch)
        log(f"## {cls}: 글 {len(samples)}편 · 덧댈 칸 — 전체 {len(patch['global'])} · 나라 {len(patch['regions'])} · 도시 {len(patch['cities'])} (표본 {min_n}개 이상만)")
    return outs


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run')
    r.add_argument('--store', required=True); r.add_argument('--gazetteer-dir'); r.add_argument('--only'); r.add_argument('--min-n', type=int, default=T.MIN_N)
    a = sub.add_parser('apply')
    a.add_argument('--model', required=True); a.add_argument('--patch', required=True); a.add_argument('--out', required=True); a.add_argument('--min-n', type=int, default=T.MIN_N)
    args = ap.parse_args()
    if args.cmd == 'apply':
        model, patch = json.load(open(args.model, encoding='utf-8')), json.load(open(args.patch, encoding='utf-8'))
        ch = T.apply_patch(model, patch, args.min_n)
        json.dump(model, open(args.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print(f'바뀐 칸 {len(ch)}개(나머지는 기존 값 유지)'); [print(' ', c) for c in ch]
        return
    gaz = Gazetteer()
    for p in sorted(glob.glob(os.path.join(args.gazetteer_dir or '/nonexistent', '**', '*.gz'), recursive=True)):
        try:
            gaz.load(p)
        except (OSError, ValueError):
            pass
    print(f'장소 자료 이름 {len(gaz.m)}개 · 브랜드 이름 {len(gaz.b)}개')
    srcs = [s for s in S.load_sources() if not args.only or s['id'] == args.only]
    store = Store(args.store)
    build(store, srcs, collect(store, srcs, gaz, Cities()), args.min_n)


if __name__ == '__main__':
    main()
