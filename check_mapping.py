import sys, json, pandas as pd, numpy as np
sys.stdout.reconfigure(encoding='utf-8')
import FinanceDataReader as fdr

df = pd.read_parquet('krx_data_cache.parquet')
w = df[df['week']=='5.25-5.28']

def bare(c): return str(c).split('*')[0].strip()

with open('etf_mapping.json', encoding='utf-8') as f:
    mapping = json.load(f)

no_comp_codes = {bare(k): v['kodex_name'] for k,v in mapping.items() if not v.get('competitors')}

PREFIXES = ['TIGER','ACE','PLUS','SOL','HANARO','RISE','KINDEX']

_cache = {}
def get_corr(c1, c2):
    for c in [c1, c2]:
        if c not in _cache:
            try:
                df2 = fdr.DataReader(c, '20260101', '20260603')
                r = df2['Close'].pct_change().dropna()
                _cache[c] = r if len(r) >= 20 else None
            except:
                _cache[c] = None
    kr, cr = _cache[c1], _cache[c2]
    if kr is None or cr is None: return None
    common = kr.index.intersection(cr.index)
    if len(common) < 20: return None
    return float(np.corrcoef(kr[common].values, cr[common].values)[0,1])

found = []
still_none = []

for kcode, kname in sorted(no_comp_codes.items(), key=lambda x: x[1]):
    kw = kname.replace('KODEX','').replace('액티브','').replace('(합성)','').replace('(H)','').strip()
    kw = kw[:10].strip()
    if not kw: continue

    matches = []
    for pfx in PREFIXES:
        hits = w[w['종목명'].str.startswith(pfx) & w['종목명'].str.contains(kw, regex=False, na=False)]
        for _, r in hits.iterrows():
            ccode = bare(r['단축코드'])
            cname = r['종목명']
            corr = get_corr(kcode, ccode)
            if corr is not None and corr >= 0.7:
                matches.append((pfx, ccode, cname, corr))

    if matches:
        best = sorted(matches, key=lambda x: -x[3])[:2]
        found.append((kname, kcode, best))
    else:
        still_none.append(kname)

print(f'[결과] 새로 찾은 비교군: {len(found)}개 / 여전히 없음: {len(still_none)}개')
print()
print('[새로 찾은 것들]')
for kname, kcode, comps in found:
    print(f'  {kname}')
    for pfx,cc,cn,cr in comps:
        print(f'    {cr:.3f}  {cn}  ({cc})')
print()
print('[여전히 없음]')
for n in still_none:
    print(f'  {n}')
