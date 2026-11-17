#!/usr/bin/env python3
"""라이선스·출처 표기 검사(네트워크 없음) — PR·push 마다 CI 에서 돈다.

  python3 scripts/guards/license_check.py [--registry sources/registry.yaml] [--exceptions sources/license_exceptions.yaml]

규칙
  1. active 출처는 license · attribution · (license_url 또는 https url) 을 모두 가진다.
  2. license 는 열린 라이선스(common.license_open) 이거나 KOGL·Apache·MIT·OGDL 처럼 등록부에 적힌 허용 목록이다. NC·ND·'미확인'은 안 된다.
  3. 등록부의 주소(url·license_url)가 금지 도메인이면 안 된다(구글·네이버·카카오·다음·타벨로그·트립어드바이저·인스타그램·틱톡·유튜브).
  4. share-alike(ODbL·CC BY-SA) 출처는 장소 수집 층(col_*)에 섞이면 안 된다 — status 가 active 이고 script 가 수집 층으로 가면 실패(예외 파일에 이유·만료일이 있으면 경고).
  5. 앱 목록(publish_layers.build_manifest)의 출처 표기에 수집 층 주요 출처 이름이 다 들어 있다.
  6. 금지 도메인 자동 제외(common.forbidden)가 대표 금지 목록 9개를 모두 막는다.
"""
import argparse, datetime, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..', '..')
sys.path.insert(0, os.path.join(ROOT, 'scripts', 'collect')); sys.path.insert(0, os.path.join(ROOT, 'scripts'))

BANNED = ('instagram.com', 'tiktok.com', 'naver.com', 'kakao.com', 'daum.net', 'tabelog.com', 'google.com', 'tripadvisor.com', 'youtube.com')
EXTRA_OK = ('kogl', '공공누리', 'apache-2.0', 'mit', 'ogdl', '政府資料開放授權條款', 'ogl-canada', 'ogl-uk', 'cdla-permissive')
SHARE_ALIKE = re.compile(r'odbl|by-sa|cc-by-sa|share-?alike', re.I)
PER_ITEM = re.compile(r'마다|사실값만|메타데이터만')
MAIN_LAYER_SCRIPTS = {'wikivoyage', 'overture', 'fsq', 'taiwan', 'crawl', 'open_dataset', 'gtfs'}  # 행이 process.py 의 장소 층(col_*)으로 가는 수집기
ATTR_NAMES = {'overture_places': 'Overture', 'fsq_os_places': 'Foursquare', 'wikivoyage': 'Wikivoyage', 'wikidata_official': 'Wikidata'}


def lic_ok(lic):
    import common as K
    s = str(lic or '')
    low = s.lower()
    if not s or '미확인' in s or re.search(r'\b(nc|nd)\b|noncommercial|no-?deriv', low):
        return False
    return K.license_open(s) or any(x in low for x in EXTRA_OK) or '데이터셋마다' in s or '사실값만' in s or '사진마다' in s or '피드마다' in s or '메타데이터만' in s


def check(reg, exceptions, today=None, attribution=None):
    """돌려줌 = (실패 list, 경고 list)."""
    import common as K
    today = today or datetime.date.today().isoformat()
    exc = {(e['id'], e['rule']): e for e in (exceptions or [])}
    errs, warns = [], []

    def fail(sid, rule, msg):
        e = exc.get((sid, rule))
        if e and str(e.get('until', '')) >= today:
            warns.append(f'{sid}: {msg} (예외 — {e.get("reason", "")[:80]} · {e["until"]}까지)')
        elif e:
            errs.append(f'{sid}: {msg} (예외 만료 {e.get("until")})')
        else:
            errs.append(f'{sid}: {msg}')

    for s in reg.get('sources', []):
        sid = s.get('id')
        for k in ('url', 'license_url'):
            u = str(s.get(k) or '')
            if u.startswith('http') and K.forbidden(u):
                fail(sid, 'banned_domain', f'{k} 가 금지 도메인')
        if s.get('status') != 'active':
            continue
        if not s.get('license') or not s.get('attribution'):
            fail(sid, 'fields', 'license·attribution 칸 비어 있음')
        per_item = bool(PER_ITEM.search(str(s.get('license') or '')))  # 사이트·데이터셋마다 다른 출처: 행마다 lic 를 남기므로 출처 단위 주소는 없어도 된다
        if not per_item and not (str(s.get('license_url') or '').startswith('https://') or str(s.get('url') or '').startswith(('https://', 's3://'))):
            fail(sid, 'fields', '라이선스 근거 주소(license_url) 없음')
        if s.get('script') == 'open_dataset':
            warns.append(f'{sid}: 데이터셋 라이선스를 기계로 판정(사람이 읽은 근거 없음 — LICENSE_AUDIT 12번). 장소 층에 싣기 전 데이터셋별 확인 필요')
        if not lic_ok(s.get('license')):
            fail(sid, 'license', f"허용 라이선스가 아님: {str(s.get('license'))[:60]}")
        if SHARE_ALIKE.search(str(s.get('license') or '')) and s.get('script') in MAIN_LAYER_SCRIPTS:
            fail(sid, 'share_alike_in_main', 'share-alike 자료가 장소 수집 층(col_*)에 섞임 — 분리 층으로만')
    if attribution is None:
        import publish_layers as PL
        attribution = PL.build_manifest('t', [])['attribution']['ko']
    for sid, name in ATTR_NAMES.items():
        if any(s.get('id') == sid and s.get('status') == 'active' for s in reg.get('sources', [])) and name not in attribution:
            fail(sid, 'attribution', f'앱 목록 출처 표기에 {name} 없음')
    for d in BANNED:
        if not K.forbidden(f'https://{d}/x') or not K.forbidden(f'https://www.{d}/x'):
            errs.append(f'금지 도메인 자동 제외가 {d} 를 막지 않음')
    for e in exceptions or []:
        if not e.get('reason') or not e.get('until'):
            errs.append(f"예외 {e.get('id')}: 이유·만료일 필수")
    return errs, warns


def main():
    import yaml
    ap = argparse.ArgumentParser()
    ap.add_argument('--registry', default=os.path.join(ROOT, 'sources', 'registry.yaml'))
    ap.add_argument('--exceptions', default=os.path.join(ROOT, 'sources', 'license_exceptions.yaml'))
    a = ap.parse_args()
    reg = yaml.safe_load(open(a.registry, encoding='utf-8'))
    exc = (yaml.safe_load(open(a.exceptions, encoding='utf-8')) or {}).get('exceptions', []) if os.path.exists(a.exceptions) else []
    errs, warns = check(reg, exc)
    out = ['## 라이선스·출처 표기 검사', f'- 실패 {len(errs)} · 경고 {len(warns)}'] + [f'- ❌ {e}' for e in errs] + [f'- ⚠️ {w}' for w in warns]
    print('\n'.join(out))
    p = os.environ.get('GITHUB_STEP_SUMMARY')
    if p:
        open(p, 'a', encoding='utf-8').write('\n'.join(out) + '\n')
    return 1 if errs else 0


if __name__ == '__main__':
    sys.exit(main())
