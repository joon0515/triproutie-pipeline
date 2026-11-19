#!/usr/bin/env python3
"""외교부 국가·지역별 입국허가요건 → 비자 팩(places/v1/visa/kr.json) — prep264 수집기. (2026-11-20, 267기)

    DATA_GO_KR_KEY=... python3 scripts/visa_mofa.py --out <폴더>      # 시험·로컬 (visa_mofa.yml 이 주 1회 돌려 Storage 에 올림)

받는 곳: 공공데이터포털 '외교부_국가·지역별 입국허가요건' OpenAPI(data.go.kr 15075345, 제공기관 외교부 정보화담당관실)
  https://apis.data.go.kr/1262000/EntranceVisaService2  (serviceKey·returnType=JSON·numOfRows·pageNo, 선택 cond[country_nm::EQ]·cond[country_iso_alp2::EQ])
  이용허락범위 제한 없음(data.go.kr 표기). 이 공식 API 한 곳만 쓴다 — 웹페이지 긁기 없음(robots 대상 아님), 키는 Actions secret DATA_GO_KR_KEY.
  응답 칸(공식 문서): country_nm·country_eng_nm·iso_num·have_yn·gnrl_pspt_visa_yn·gnrl_pspt_visa_cn(일반여권)·ofclpspt_*·dplmt_*·nvisa_entry_evdc_cn·remark.
  문서에 칸 설명이 서로 어긋난 곳(country_nm=영문명, gnrl_pspt_visa_yn=여권소지여부)이 있어 *_yn 값은 믿지 않고 일반여권 내용 글(gnrl_pspt_visa_cn)만 읽는다.
앱이 읽는 모양: {asof:'YYYY-MM-DD', src:'…', countries:{JP:{need:'free'|'eta'|'visa', days:90}}}. 기준일 = 이 수집을 한 날(API 에 갱신일 칸이 없음).
애매하면 그 나라는 넣지 않는다(앱이 '외교부 해외안전여행에서 확인'으로 안내). 숫자를 짐작하지 않는다.
키·주소·응답 본문은 출력하지 않는다(개수만). 나라 수가 너무 적으면 올리지 않고 실패(옛 팩 유지).
"""
import argparse, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
BASE = os.environ.get('VISA_BASE', 'https://apis.data.go.kr/1262000/EntranceVisaService2')
SRC = '외교부 국가·지역별 입국허가요건(공공데이터포털 15075345)'
MIN_ROWS, MIN_CLASSIFIED = 100, 30

_ETA = re.compile(r'e-?ta\b|esta\b|k-?eta|전자\s*여행\s*허가|전자\s*입국\s*허가|전자\s*여행\s*인증', re.I)
_FREE = re.compile(r'무\s*비자|무\s*사증|사증\s*면제|비자\s*면제|사증\s*없이|비자\s*없이')
_VISA = re.compile(r'(?<!무)(?<!면제)(사증|비자)[^.\n]{0,12}(필요|발급|취득|신청)|도착\s*(사증|비자)|전자\s*(사증|비자)|e-?visa|입국\s*불가|입국\s*금지', re.I)
_LIMITED = re.compile(r'초과|이상|장기|취업|유학|영주|업무|상용|언론|취재|공무|연장|거주|투자|결혼|종교')
_DAYS = re.compile(r'(\d{1,3})\s*(일|개월|달)')


def classify(text):
    """일반여권 내용 글 → ('free'|'eta'|'visa', 일수|None) 또는 None(애매)."""
    t = re.sub(r'\s+', ' ', str(text or '')).strip()
    if not t:
        return None
    sents = [s for s in re.split(r'[.\n;·•※]+|(?<=다)\s', t) if s.strip()]
    main = [s for s in sents if not _LIMITED.search(s)] or []
    if not main:
        return None
    blob = ' '.join(main)
    eta, free, visa = bool(_ETA.search(blob)), bool(_FREE.search(blob)), bool(_VISA.search(blob))
    if eta and not visa:
        return ('eta', _days(blob))
    if visa and not (free or eta):
        return ('visa', None)
    if free and not (visa or eta):
        d = _days(' '.join(s for s in main if _FREE.search(s)))
        return ('free', d) if d else None  # 일수 없으면 넣지 않음(숫자 짐작 금지)
    return None


def _days(s):
    m = _DAYS.search(s)
    if not m:
        return None
    n = int(m.group(1)) * (30 if m.group(2) != '일' else 1)
    return n if 1 <= n <= 365 else None


def rows_of(j):
    """JSON 어디에 있든 country_nm 을 가진 dict 목록을 찾는다(구·신 응답 모양 모두)."""
    out = []
    def walk(x):
        if isinstance(x, dict):
            if 'country_nm' in x or 'country_eng_nm' in x:
                out.append(x)
            else:
                for v in x.values():
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(j)
    return out


def code_of(r, num_map):
    n = str(r.get('iso_num') or '').strip()
    if n.isdigit():
        return num_map.get(n.zfill(3))
    return None


class Client:
    def __init__(self, key, opener=None, sleep=time.sleep, limit=30):
        self.key = urllib.parse.unquote(key) if '%' in key else key
        self.sleep, self.limit, self.calls = sleep, limit, 0
        self.opener = opener or (lambda u: urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'TripRoutie-pipeline/1.0'}), timeout=60).read())

    def page(self, n, per=300):
        if self.calls >= self.limit:
            raise RuntimeError('LIMIT')
        self.calls += 1
        if self.calls > 1:
            self.sleep(1.0)
        q = {'serviceKey': self.key, 'returnType': 'JSON', 'numOfRows': per, 'pageNo': n}
        try:
            raw = self.opener(BASE + '?' + urllib.parse.urlencode(q))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f'HTTP {e.code}')
        s = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else raw
        if s.lstrip().startswith('<'):
            raise RuntimeError('XML 오류 응답(키 미승인·활용신청 전일 수 있음)')
        try:
            return json.loads(s)
        except ValueError:
            raise RuntimeError('JSON 아님')


def find_key(x, k):
    """중첩 JSON 에서 첫 k 값(없으면 None)."""
    if isinstance(x, dict):
        if k in x:
            return x[k]
        for v in x.values():
            r = find_key(v, k)
            if r is not None:
                return r
    elif isinstance(x, list):
        for v in x:
            r = find_key(v, k)
            if r is not None:
                return r
    return None


def total_of(j):
    try:
        return int(find_key(j, 'totalCount'))
    except (TypeError, ValueError):
        return None


def collect(client, num_map, per=300, max_pages=10):
    rows, why, total = [], None, None
    for n in range(1, max_pages + 1):
        try:
            j = client.page(n, per)
        except RuntimeError as e:
            why = str(e)
            break
        rc = find_key(j, 'resultCode')
        if rc is not None and str(rc) not in ('0', '00', '000'):
            why = f'resultCode {rc}'
            break
        got = rows_of(j)
        rows += got
        total = total if total is not None else total_of(j)
        if not got or (total is not None and len(rows) >= total) or len(got) < per:
            break
    countries, unknown = {}, 0
    for r in rows:
        c = code_of(r, num_map)
        if not c:
            unknown += 1
            continue
        res = classify(r.get('gnrl_pspt_visa_cn'))
        if not res:
            unknown += 1
            continue
        need, days = res
        e = {'need': need}
        if days:
            e['days'] = days
        countries[c] = e
    return countries, len(rows), unknown, why


def doc(countries, asof):
    return {'schema': 'visa-1', 'asof': asof, 'src': f'{SRC} {asof} 수집', 'for': 'KR 여권(일반여권)', 'countries': dict(sorted(countries.items()))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    key = os.environ.get('DATA_GO_KR_KEY', '').strip()
    if not key:
        sys.exit('DATA_GO_KR_KEY 없음(Actions secret)')
    num_map = json.load(open(os.path.join(ROOT, 'sources', 'iso3166_numeric.json'), encoding='utf-8'))
    countries, nrows, unknown, why = collect(Client(key), num_map)
    asof = time.strftime('%Y-%m-%d', time.gmtime(time.time() + 9 * 3600))  # KST 날짜
    line = f'## 비자 팩(visa-mofa) — 받은 행 {nrows} · 넣은 나라 {len(countries)} · 애매해 뺀 행 {unknown}' + (f' · 멈춘 까닭 {why}' if why else '')
    p = os.environ.get('GITHUB_STEP_SUMMARY')
    if p:
        open(p, 'a', encoding='utf-8').write(line + '\n')
    print(line)
    if why or nrows < MIN_ROWS or len(countries) < MIN_CLASSIFIED:
        sys.exit('올리지 않음: 행·나라 수가 기준 미달이거나 중간에 멈춤(옛 팩 유지)')
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, 'kr.json'), 'w', encoding='utf-8') as f:
        json.dump(doc(countries, asof), f, ensure_ascii=False, separators=(',', ':'))


if __name__ == '__main__':
    main()
