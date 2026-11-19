"""일정 글(시각표·챕터 목록) → 숫자. 원문은 이 함수 안에서만 쓰고 돌려주지 않는다 — 돌려주는 것은 하루 단위 숫자와 장소 '종류'뿐(이름·문장 없음).

두 가지 모양을 읽는다.
  clock    : '09:00 센소지' · '오전 9시 30분 …' · '9:30am …' · '午前9時 …' · '上午9点半 …' 처럼 실제 시각이 붙은 일정표
  chapters : '0:00 Intro / 1:23 Day 1 / 2:10 …' 처럼 0:00 에서 시작하는 영상 챕터(재생 위치) — 시각이 아니므로 장소 수·종류·날 구분만 센다
5개 표기: 한국어·영어·일본어·중국어 간체·번체.
"""
import re
import unicodedata

PM_WORDS = ('오후', '저녁', '밤', '午後', '下午', '晚上', '傍晚', 'pm')
AM_WORDS = ('오전', '아침', '새벽', '午前', '上午', '早上', '清晨', 'am')
_PRE = r'(?P<pre>오전|오후|아침|저녁|밤|새벽|午前|午後|上午|下午|晚上|早上|中午|傍晚|清晨|[AaPp]\.?[Mm]\.?)?\s*'
_T_COLON = re.compile(_PRE + r'(?P<h>\d{1,2})\s*[:：]\s*(?P<m>\d{2})(?![\d:：])(?:\s*(?P<suf>[AaPp]\.?[Mm]\.?)(?![A-Za-z]))?')
_T_WORD = re.compile(_PRE + r'(?P<h>\d{1,2})\s*(?:시|時|时|点|點)\s*(?:(?P<m>\d{1,2})\s*(?:분|分)|(?P<half>半|반))?')
_T_AMPM = re.compile(r'(?<![\d:])(?P<h>\d{1,2})\s*(?P<suf>[AaPp]\.?[Mm]\.?)(?![A-Za-z])')
_CHAPTER = re.compile(r'^(\d{1,2}):(\d{2})(?::(\d{2}))?(?![\d:])')
_LEAD = re.compile(r'^[^\w]+', re.U)
_RANGE = re.compile(r'^\s*(?:[-–—~〜～]|to|부터|から|至|到)\s*')
_CN = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10, '两': 2, '兩': 2}
_KO_ORD = {'첫': 1, '둘': 2, '셋': 3, '넷': 4, '다섯': 5, '여섯': 6, '일곱': 7}
_DAY = re.compile(
    r'(?:\bday\s*[-#]?\s*(?P<en>\d{1,2})\b)|(?:(?P<ko>\d{1,2})\s*일\s*(?:차|째))|(?:(?P<ko2>첫|둘|셋|넷|다섯|여섯|일곱)\s*째?\s*날)'
    r'|(?:(?P<ja>\d{1,2})\s*日\s*目)|(?:第\s*(?P<zh>\d{1,2}|[一二三四五六七八九十两兩])\s*[天日])|(?:\bD(?P<d>\d{1,2})\b)', re.I)

KW = (  # 순서가 우선순위. 장소 이름은 남기지 않고 종류만 쓴다.
    ('skip', ('intro', 'outro', 'opening', 'ending', 'summary', 'recap', 'budget', 'cost', 'tips', 'subscribe', 'credits', '인트로', '오프닝', '엔딩', '마무리',
              '총정리', '경비', '예고', '구독', 'オープニング', 'エンディング', 'まとめ', '予告', '費用', '片头', '片頭', '片尾', '开场', '開場', '总结', '總結', '花费', '花費')),
    ('lodging', ('hotel', 'hostel', 'check-in', 'check in', 'checkout', 'check-out', 'check out', 'airbnb', 'ryokan', '호텔', '숙소', '체크인', '체크아웃', '료칸',
                 'ホテル', 'チェックイン', 'チェックアウト', '旅館', '宿泊', '酒店', '民宿', '入住', '退房', '旅店')),
    ('breakfast', ('breakfast', '조식', '아침 식사', '아침식사', '아침밥', '朝食', '朝ごはん', 'モーニング', '早餐', '早饭', '早飯')),
    ('lunch', ('lunch', '점심', '중식', 'ランチ', '昼食', '昼ごはん', '午餐', '午饭', '午飯', '中餐')),
    ('dinner', ('dinner', 'supper', '저녁 식사', '저녁식사', '저녁밥', '저녁', '석식', '夕食', 'ディナー', '晩ごはん', '夜ごはん', '晚餐', '晚饭', '晚飯')),
    ('transit', ('airport', 'flight', 'station', 'train to', 'bus to', 'ferry to', 'drive to', 'transfer', 'shinkansen', '공항', '이동', '탑승', '비행기', '기차역',
                 '터미널', '空港', '駅', '移動', '新幹線', '机场', '機場', '车站', '車站', '前往', '搭乘', '高铁', '高鐵')),
    ('cafe', ('cafe', 'café', 'coffee', 'dessert', 'tea house', '카페', '커피', '디저트', 'カフェ', '喫茶', 'スイーツ', '咖啡', '甜品', '下午茶')),
    ('food', ('restaurant', 'ramen', 'sushi', 'izakaya', 'street food', 'eat ', 'bbq', '맛집', '식당', '레스토랑', '이자카야', '라멘', '스시', '국밥', '고기집', '먹거리', 'グルメ', '食堂',
              'レストラン', 'ラーメン', '寿司', '居酒屋', '焼肉', '餐厅', '餐廳', '美食', '小吃', '火锅', '火鍋', '拉面', '拉麵')),
    ('shopping', ('shopping', 'market', 'mall', 'outlet', '쇼핑', '시장', '백화점', '아울렛', '買い物', 'ショッピング', '商店街', '市場', '购物', '購物', '商场', '商場', '百货', '百貨', '夜市')),
    ('museum', ('museum', 'gallery', 'aquarium', '박물관', '미술관', '아쿠아리움', '博物館', '美術館', '水族館', '博物馆', '美术馆', '水族馆')),
    ('park', ('park', 'garden', 'beach', 'trail', 'hike', '공원', '정원', '해변', '해수욕장', '둘레길', '公園', '庭園', 'ビーチ', '公园', '花园', '海滩', '海灘')),
    ('activity', ('theme park', 'onsen', 'spa', 'cruise', 'tour', 'experience', '테마파크', '온천', '체험', '유람선', 'テーマパーク', '温泉', '体験', '乐园', '樂園', '體驗', '体验')),
)
MEALS = ('breakfast', 'lunch', 'dinner')
NOT_STOP = ('skip', 'lodging', 'transit')
TYPES = (
    ('family', ('family', 'kids', 'toddler', 'children', '아이와', '아이랑', '가족', '子連れ', '家族', '亲子', '親子', '带娃', '帶娃')),
    ('couple', ('couple', 'honeymoon', 'romantic', '커플', '신혼', 'カップル', '新婚', '情侣', '情侶', '蜜月')),
    ('solo', ('solo', 'alone', '혼자', '혼행', '一人旅', 'ひとり旅', '一个人', '一個人', '独旅', '獨旅')),
    ('parents', ('parents', '부모님', '효도', '親孝行', '両親', '父母', '爸妈', '爸媽')),
    ('friends', ('friends', 'girls trip', '친구', '우정', '友達', '女子旅', '闺蜜', '閨蜜', '朋友')),
)


def _norm(s):
    return unicodedata.normalize('NFKC', s or '')


def _to_min(m, kind):
    """정규식 일치 → (분, 오전·오후가 분명한가). 못 믿을 값이면 None."""
    h = int(m.group('h'))
    mm = 30 if (kind == 'word' and m.group('half')) else int(m.group('m') or 0) if kind != 'ampm' else 0
    pre = (m.groupdict().get('pre') or '').lower().replace('.', '')
    suf = (m.groupdict().get('suf') or '').lower().replace('.', '')
    tag = suf or pre
    if h > 29 or mm > 59:
        return None
    sure = bool(tag) or h >= 13
    if tag in PM_WORDS:
        if h > 12:
            pass
        elif tag == '밤' and h <= 4:
            h += 24
        elif h < 12:
            h += 12
    elif tag in AM_WORDS:
        if h == 12:
            h = 0
        if tag == '새벽' and h <= 3:
            h += 24  # 새벽 1시 = 전날 일정의 끝
    elif tag == '中午' and h < 11:
        h += 12
    return h * 60 + mm, sure


def find_time(line):
    """줄 앞머리의 시각 → (분, 분명함, 끝 분 또는 None, 남은 글). 없으면 None."""
    s = _LEAD.sub('', line)
    best = None
    for kind, rx in (('colon', _T_COLON), ('word', _T_WORD), ('ampm', _T_AMPM)):
        m = rx.match(s)
        if m and m.end() > m.start() and (best is None or m.end() > best[1].end()):
            best = (kind, m)
    if not best:
        return None
    kind, m = best
    t = _to_min(m, kind)
    if t is None:
        return None
    rest, end = s[m.end():], None
    r = _RANGE.match(rest)
    if r:
        tail = rest[r.end():]
        for k2, rx in (('colon', _T_COLON), ('word', _T_WORD), ('ampm', _T_AMPM)):
            m2 = rx.match(tail)
            if m2:
                t2 = _to_min(m2, k2)
                if t2:
                    end = t2[0]
                    rest = tail[m2.end():]
                break
    return t[0], t[1], end, rest.strip(' \t-–—~:：|)]】」')


def day_no(line):
    m = _DAY.search(line)
    if not m:
        return None
    g = m.groupdict()
    for k in ('en', 'ko', 'ja', 'd'):
        if g.get(k):
            return int(g[k])
    if g.get('ko2'):
        return _KO_ORD[g['ko2']]
    z = g.get('zh')
    return (int(z) if z.isdigit() else _CN.get(z)) if z else None


def kind_of(label, gaz=None):
    """글자 → 종류. 식사 낱말이 먼저(식사 시각을 재야 하므로), 다음이 우리 장소 자료(gaz), 마지막이 낱말 규칙."""
    low = _norm(label).lower()
    hit = None
    for kind, words in KW:
        if any(w in low for w in words):
            hit = kind
            break
    if hit in ('skip',) + MEALS:
        return hit
    if gaz is not None:
        c = gaz.lookup(label)
        if c:
            return c
    return hit or 'sight'


def trip_type(text):
    low = _norm(text).lower()
    for t, words in TYPES:
        if any(w in low for w in words):
            return t
    return 'any'


def is_chapters(lines):
    """0:00 에서 시작해 늘어나기만 하는 '분:초' 목록이면 영상 챕터(시각이 아님)."""
    toks = []
    for ln in lines:
        s = _LEAD.sub('', ln)
        m = _CHAPTER.match(s)
        if m:
            if _T_COLON.match(s).group('suf') if _T_COLON.match(s) else False:
                return False
            a, b, c = int(m.group(1)), int(m.group(2)), m.group(3)
            toks.append(a * 3600 + b * 60 + int(c) if c else a * 60 + b)
    return len(toks) >= 3 and toks[0] == 0 and all(y >= x for x, y in zip(toks, toks[1:]))


def _split_days(lines, chapters):
    """줄 → [(날 번호 또는 None, [(분 또는 None, 분명함, 끝, 글)])]."""
    days, cur, cur_no = [], [], None
    for ln in lines:
        ln = _norm(ln).strip()
        if not ln:
            continue
        label, t = ln, None
        if chapters:
            s = _LEAD.sub('', ln)
            m = _CHAPTER.match(s)
            if not m:
                d = day_no(ln) if len(ln) <= 40 else None
                if d is not None:
                    if cur:
                        days.append((cur_no, cur))
                    cur, cur_no = [], d
                continue
            label = s[m.end():].strip(' \t-–—~:：|)]')
            t = (None, False, None, label)
        else:
            t = find_time(ln)
            if t:
                label = t[3]
        d = day_no(label if t else ln)
        short = len(label if t else ln) <= 40
        if d is not None and short and (not t or chapters or not label.strip()) or (d is not None and chapters and short):
            if cur:
                days.append((cur_no, cur))
            cur, cur_no = [], d
            continue
        if d is not None and short and t and not chapters and len(_DAY.sub('', label).strip(' \t-–—:：')) < 3:
            if cur:
                days.append((cur_no, cur))
            cur, cur_no = [], d
            continue
        if t:
            cur.append(t)
    if cur:
        days.append((cur_no, cur))
    return days


def _clock_day(ents, gaz):
    """한 날의 (분, 분명함, 끝, 글) → 숫자. 믿기 어려우면 None."""
    seq, prev = [], None
    for t, sure, end, label in ents:
        k = kind_of(label, gaz)
        if k == 'skip':
            continue
        if prev is not None and t < prev:
            if not sure and t + 720 >= prev and t + 720 < 28 * 60:
                t += 720  # '2시' 가 앞의 11시 뒤에 오면 오후 2시
            elif prev - t > 360 and t < 5 * 60:
                t += 1440  # 자정 넘김
            else:
                return None  # 시각이 거꾸로 = 일정표가 아님
        if end is not None and end < t:
            end = end + 720 if end + 720 >= t and end + 720 - t <= 480 else None
        seq.append((t, end, k))
        prev = t
    stops = [x for x in seq if x[2] not in NOT_STOP]
    if len(seq) < 3 or len(stops) < 2:
        return None
    start, last = stops[0][0], stops[-1][0]
    if not (4 * 60 <= start <= 20 * 60) or not (60 <= last - start <= 20 * 60):
        return None
    full = start <= 16 * 60 and last - start >= 180  # 온전한 하루(첫날 저녁 도착·마지막 날 오전만 같은 반쪽 날이 아님)
    gaps = [b[0] - a[0] for a, b in zip(stops, stops[1:]) if 5 <= b[0] - a[0] <= 300]
    stays = [e - t for t, e, k in stops if e and 5 <= e - t <= 480]
    out = {'clock': True, 'full': full, 'start': start, 'end': last, 'stops': len(stops), 'gaps': gaps, 'stays': stays, 'cats': _cats(stops)}
    bq = _brands([lb for _, _, _, lb in ents], gaz)
    if bq:
        out['brands'] = bq
    for t, _, k in stops:
        meal = k if k in MEALS else None
        if k == 'food':
            meal = 'lunch' if 630 <= t <= 870 else 'dinner' if 1020 <= t <= 1290 else None
        if meal and meal not in out and _meal_ok(meal, t):
            out[meal] = t
    return out


def _meal_ok(meal, t):
    lo, hi = {'breakfast': (300, 630), 'lunch': (600, 930), 'dinner': (990, 1380)}[meal]
    return lo <= t <= hi


def _cats(stops):
    c = {}
    for x in stops:
        k = 'food' if x[2] in MEALS else x[2]
        c[k] = c.get(k, 0) + 1
    return c


def _brands(labels, gaz):
    """(256기) 하루 글 줄들 → {브랜드 QID: 언급 수}. 이름은 버리고 QID 개수만 남긴다."""
    if gaz is None or not getattr(gaz, 'b', None):
        return {}
    out = {}
    for lb in labels:
        q = gaz.lookup_brand(lb or '')
        if q:
            out[q] = out.get(q, 0) + 1
    return out


def _chapter_day(ents, gaz):
    stops = [(None, None, k) for k in (kind_of(lb, gaz) for _, _, _, lb in ents) if k not in NOT_STOP]
    if len(stops) < 2 or len(stops) > 20:
        return None
    out = {'clock': False, 'stops': len(stops), 'cats': _cats(stops)}
    bq = _brands([lb for _, _, _, lb in ents], gaz)
    if bq:
        out['brands'] = bq
    return out


def parse_text(text, gaz=None):
    """글 한 편 → {'type': 여행 유형, 'days': [하루 숫자…]}. 날마다 'idx'(1부터)·'of'(전체 날 수)가 붙는다. 읽을 것이 없으면 None."""
    lines = _norm(text).splitlines()
    chapters = is_chapters(lines)
    parts = _split_days(lines, chapters)
    numbered = [p for p in parts if p[0] is not None]
    use = numbered if len(numbered) >= 2 else parts[:1] if not numbered else numbered
    if chapters and not numbered:
        return None  # 날 구분 없는 챕터 목록은 하루치인지 알 수 없어 세지 않는다
    days = []
    for no, ents in use:
        d = (_chapter_day if chapters else _clock_day)(ents, gaz)
        if d:
            d['idx'] = no or 1
            days.append(d)
    if not days:
        return None
    total = max([d['idx'] for d in days] + [len(use)])
    for d in days:
        d['of'] = total
    return {'type': trip_type(text), 'days': days}
