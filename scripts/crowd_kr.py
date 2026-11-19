#!/usr/bin/env python3
"""한국 관광지 혼잡 예측 층(crowd-1) — 한국관광공사 '관광지 집중률 방문자 추이 예측 정보'(data.go.kr 15128555). (2026-11-20, 262기)

    TOURAPI_KEY=... python3 scripts/crowd_kr.py --kto <kto_*.json.gz 폴더> --out <폴더>        # 시험·로컬
    (crowd-kr.yml 이 layers_extra 의 kto_* 를 받아 돌리고 결과를 layers_extra 에 올린다)

받는 곳: https://apis.data.go.kr/B551011/TatsCnctrRateService/tatsCnctrRatedList (areaCd·signguCd·tAtsNm, 응답 baseYmd·cnctrRate·tAtsNm).
  이용허락범위 제한 없음(data.go.kr 표기). 키는 Actions secret DATA_GO_KR_KEY(TourAPI 와 같은 키, 이 API 활용신청 따로 필요 — CEO_FINAL).
범위: sources/priority_cities.json 의 한국 도시 상자 안 TourAPI 관광지가 있는 시군구만(인기 도시부터). 시군구 1개 = 요청 1~몇 번.
행: {정규화한 관광지 이름: [[yyyymmdd, 집중률(%)], …]} — 앱(tl_p2262)이 장소 이름으로 맞춰 그날 값을 '붐빔·보통·여유'로 보이고 출처·기준일을 붙인다.
키·주소·응답 본문은 출력하지 않는다(개수만).
"""
import argparse, glob, gzip, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
BASE = os.environ.get('CROWD_BASE', 'https://apis.data.go.kr/B551011/TatsCnctrRateService/')
STRIP = re.compile(r'[\s　・·\-‐－—.,()（）「」『』&＆\'’]+')


def nrm(s):
    return STRIP.sub('', (s or '').lower())


def kr_cities():
    j = json.load(open(os.path.join(ROOT, 'sources', 'priority_cities.json'), encoding='utf-8'))
    out = []
    for c in sorted(j['cities'], key=lambda c: c['rank']):
        b = c.get('bbox')
        if isinstance(b, list) and len(b) == 4 and 124 <= b[0] <= 132 and 33 <= b[1] <= 39:
            out.append(c)
    return out


def sigungu_targets(kto_dir, cities):
    """kto_*.json.gz 행(좌표·area·sigungu) → 인기 도시 순서의 [(areaCd, signguCd)] (중복 없이)."""
    rows = []
    for fn in sorted(glob.glob(os.path.join(kto_dir, 'kto_*.json.gz'))):
        j = json.loads(gzip.decompress(open(fn, 'rb').read()))
        a = str(j.get('area') or '')  # kto 층은 시도 1개 = 파일 1개(머리 area), 행 sg = 시군구
        for r in (j.get('rows') or {}).values():
            if isinstance(r, dict) and 'lat' in r and a and r.get('sg'):
                rows.append((r['lat'], r['lon'], a, str(r['sg'])))
    seen, out = set(), []
    for c in cities:
        w, s, e, n = c['bbox']
        for la, lo, a, g in rows:
            if w <= lo <= e and s <= la <= n:
                sg = g if len(g) == 5 else a + g.zfill(3)
                if (a, sg) not in seen:
                    seen.add((a, sg)); out.append((a, sg))
    return out


class Client:
    def __init__(self, key, limit=300, opener=None, sleep=time.sleep, interval=1.0):
        self.key, self.limit, self.calls, self.sleep, self.interval = key, limit, 0, sleep, interval
        self.opener = opener or (lambda u: urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'TripRoutie-pipeline/1.0'}), timeout=60).read())

    def items(self, **q):
        q.update(serviceKey=self.key, MobileOS='ETC', MobileApp='TripRoutie', _type='json', numOfRows=1000, pageNo=1)
        if self.calls >= self.limit:
            raise RuntimeError('LIMIT')
        self.calls += 1
        if self.calls > 1:
            self.sleep(self.interval)
        try:
            raw = self.opener(BASE + 'tatsCnctrRatedList?' + urllib.parse.urlencode(q))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f'HTTP {e.code}')
        j = json.loads(raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else raw)
        body = ((j.get('response') or {}).get('body') or {})
        it = (body.get('items') or {})
        it = it.get('item') if isinstance(it, dict) else it
        return it if isinstance(it, list) else ([it] if isinstance(it, dict) else [])


def build(client, targets):
    rows, base, asked, why = {}, '', 0, None
    for a, sg in targets:
        try:
            its = client.items(areaCd=a, signguCd=sg)
        except RuntimeError as e:
            why = str(e)
            break
        asked += 1
        for x in its:
            k, ymd = nrm(x.get('tAtsNm')), str(x.get('baseYmd') or '')
            try:
                rate = round(float(x.get('cnctrRate')), 1)
            except (TypeError, ValueError):
                continue
            if not k or not re.fullmatch(r'\d{8}', ymd) or not 0 <= rate <= 100:
                continue
            rows.setdefault(k, []).append([int(ymd), rate])
            base = max(base, ymd)
    for v in rows.values():
        v.sort()
    return rows, asked, why


def doc(rows, built):
    return {'schema': 'crowd-1', 'region': 'KR', 'built': built, 'license': '이용허락범위 제한 없음(data.go.kr 15128555)',
            'attribution': '한국관광공사 관광지 집중률 방문자 추이 예측 정보', 'rows': rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kto', required=True); ap.add_argument('--out', required=True); ap.add_argument('--limit', type=int, default=300)
    a = ap.parse_args()
    key = os.environ.get('TOURAPI_KEY', '').strip()
    if not key:
        sys.exit('TOURAPI_KEY 없음(Actions secret DATA_GO_KR_KEY)')
    tg = sigungu_targets(a.kto, kr_cities())
    rows, asked, why = build(Client(key, a.limit), tg)
    os.makedirs(a.out, exist_ok=True)
    if rows:
        with open(os.path.join(a.out, 'crowd-kr-1.json.gz'), 'wb') as f:
            f.write(gzip.compress(json.dumps(doc(rows, time.strftime('%Y-%m-%d')), ensure_ascii=False, separators=(',', ':')).encode()))
    line = f'## 혼잡 예측(crowd-kr) — 시군구 {len(tg)} · 물음 {asked} · 관광지 {len(rows):,}' + (f' · 멈춘 까닭 {why}' if why else '')
    p = os.environ.get('GITHUB_STEP_SUMMARY')
    if p:
        open(p, 'a', encoding='utf-8').write(line + '\n')
    print(line)


if __name__ == '__main__':
    main()
