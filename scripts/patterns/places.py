"""장소 이름 → 종류(우리 장소 자료와 맞춤) · 도시 이름 → 모델 열쇠. 이름은 맞추는 데만 쓰고 결과에는 종류만 남는다."""
import gzip, json, os, re, sys, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'collect'))
from common import norm_name  # noqa: E402

_SPLIT = re.compile(r'\s+(?:at|@|in|to|->|→)\s+|에서|으로|[,/()\[\]「」『』【】|·・:：>→–—-]|\s+で\s*|在|去')
_CAT = {'sight': 'sight', 'museum': 'museum', 'park': 'park', 'food': 'food', 'cafe': 'cafe', 'bar': 'food', 'shopping': 'shopping', 'activity': 'activity',
        'lodging': 'lodging', 'transport': 'transit', 'event': 'activity'}


class Gazetteer:
    """{정규화 이름: 종류}. 줄마다 JSON(nm·ko·en·ja·zh·c) 인 파일(.gz 가능) 또는 collect raw 조각({'rows': […]})을 읽는다."""

    def __init__(self):
        self.m = {}

    def add(self, row):
        c = _CAT.get(row.get('c') or '', None)
        if not c:
            return
        for k in ('nm', 'ko', 'en', 'ja', 'zh'):
            n = norm_name(row.get(k) or '')
            if len(n) >= 2:
                self.m.setdefault(n, c)

    def load(self, path):
        op = gzip.open if path.endswith('.gz') else open
        with op(path, 'rt', encoding='utf-8') as f:
            head = f.read(1)
            f.seek(0)
            if head == '{' and path.endswith('.json.gz'):
                o = json.load(f)
                for r in o.get('rows', []) if isinstance(o, dict) else []:
                    self.add(r)
            else:
                for ln in f:
                    if ln.strip():
                        self.add(json.loads(ln))
        return self

    def lookup(self, label):
        n = norm_name(label)
        if n in self.m:
            return self.m[n]
        for part in _SPLIT.split(unicodedata.normalize('NFKC', label)):
            p = norm_name(part)
            if len(p) >= 3 and p in self.m:
                return self.m[p]
        return None


class Cities:
    """cities.json: [{key(모델 열쇠), cc, names[…]}]. 제목에서 먼저, 없으면 본문에서 가장 많이 나온 도시."""

    def __init__(self, path=None):
        self.rows = json.load(open(path or os.path.join(HERE, 'cities.json'), encoding='utf-8'))['cities']
        self.idx = [(r, [unicodedata.normalize('NFKC', n).lower() for n in r['names']]) for r in self.rows]

    @staticmethod
    def _n(names, low):
        return sum(len(re.findall(r'(?<![a-z])' + re.escape(a) + r'(?![a-z])', low)) if a.isascii() else low.count(a) for a in names)

    def find(self, title, text=''):
        t, x = (unicodedata.normalize('NFKC', v or '').lower() for v in (title, (text or '')[:20000]))
        best, bs = None, 0
        for r, names in self.idx:
            sc = self._n(names, t) * 1000 + self._n(names, x)  # 제목이 먼저, 같으면 본문에서 더 많이 나온 도시
            if sc > bs:
                best, bs = r, sc
        return (best['key'], best['cc']) if best else (None, None)
