import sys, json, pandas as pd, numpy as np, re
sys.stdout.reconfigure(encoding='utf-8')
from pykrx import stock

df = pd.read_parquet('krx_data_cache.parquet')
w = df[df['week']=='5.25-5.29']

def bare(c): return str(c).split('*')[0].strip()

with open('etf_mapping.json', encoding='utf-8') as f:
    mapping = json.load(f)

# 비교군 없는 KODEX
no_comp = {bare(k): v['kodex_name'] for k,v in mapping.items() if not v.get('competitors')}

PREFIXES = ['TIGER','ACE','PLUS','SOL','HANARO','RISE','KINDEX','WOORI','KB','NH',
            'BNK','DAISHIN','FOCUS','HK','IBK','KCGI','KIWOOM','KoAct','MIDAS',
            'TIME','TREX','TRUSTON','UNICORN','VITA','WON',
            '더제이','마이티','아이엠에셋','에셋플러스','파워']

_cache = {}
def get_corr(c1, c2):
    for c in [c1, c2]:
        if c not in _cache:
            try:
                df2 = stock.get_market_ohlcv_by_date('20260101', '20260605', c)
                r = df2['종가'].pct_change().dropna()
                _cache[c] = r if len(r) >= 20 else None
            except Exception:
                _cache[c] = None
    kr, cr = _cache[c1], _cache[c2]
    if kr is None or cr is None: return None
    common = kr.index.intersection(cr.index)
    if len(common) < 20: return None
    return float(np.corrcoef(kr[common].values, cr[common].values)[0,1])

def extract_keywords(name):
    """여러 키워드 전략으로 추출"""
    clean = name.replace('KODEX','').replace('액티브','').replace('(합성)','')
    clean = clean.replace('(H)','').replace('TR','').replace('Plus','').strip()

    keywords = set()
    # 전략1: 공백으로 쪼개서 각 토큰
    tokens = [t for t in re.split(r'[\s\-&]+', clean) if len(t) >= 2]
    for t in tokens:
        # 숫자+문자 혼합 토큰은 앞 숫자 제거
        t2 = re.sub(r'^\d+', '', t).strip()
        if len(t2) >= 2:
            keywords.add(t2)
        if len(t) >= 2:
            keywords.add(t)

    # 전략2: 첫 번째 의미 단어 (2~6자)
    for t in tokens:
        if 2 <= len(t) <= 6 and not t.isdigit():
            keywords.add(t)

    # 전략3: 앞 4~8글자
    for n in [4, 6, 8]:
        kw = clean[:n].strip()
        if len(kw) >= 2:
            keywords.add(kw)

    return [k for k in keywords if len(k) >= 2]

found_new = []
still_none = []

for kcode, kname in sorted(no_comp.items(), key=lambda x: x[1]):
    keywords = extract_keywords(kname)

    best_matches = {}  # provider → best (ccode, cname, corr)

    for kw in keywords:
        for pfx in PREFIXES:
            hits = w[w['종목명'].str.startswith(pfx) &
                     w['종목명'].str.contains(kw, regex=False, na=False)]
            for _, r in hits.iterrows():
                ccode = bare(r['종목코드'])
                cname = r['종목명']
                if ccode == kcode: continue
                corr = get_corr(kcode, ccode)
                if corr is not None and corr >= 0.7:
                    if pfx not in best_matches or corr > best_matches[pfx][2]:
                        best_matches[pfx] = (ccode, cname, corr)

    if best_matches:
        # TIGER 우선, 나머지 중 1개 (최대 2개)
        comps = []
        if 'TIGER' in best_matches:
            comps.append(best_matches['TIGER'])
        for pfx in ['ACE','PLUS','SOL','HANARO','RISE','KINDEX']:
            if pfx in best_matches and len(comps) < 2:
                comps.append(best_matches[pfx])
        if not comps:
            comps = sorted(best_matches.values(), key=lambda x: -x[2])[:2]

        found_new.append((kname, kcode, comps))
    else:
        still_none.append(kname)

print(f'[결과] 새로 찾은 비교군: {len(found_new)}개 / 여전히 없음: {len(still_none)}개')
print()
for kname, kcode, comps in found_new:
    print(f'  {kname}')
    for cc, cn, cr in comps:
        print(f'    {cr:.3f}  {cn}  ({cc})')
print()
print(f'[여전히 없음 {len(still_none)}개]')
for n in still_none: print(f'  {n}')
