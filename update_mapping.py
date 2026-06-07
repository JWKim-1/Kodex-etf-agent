import sys, json, re, pandas as pd, numpy as np
sys.stdout.reconfigure(encoding='utf-8')
import FinanceDataReader as fdr

df = pd.read_parquet('krx_data_cache.parquet')
w = df[df['week']=='5.25-5.28']

def bare(c): return str(c).split('*')[0].strip()

with open('etf_mapping.json', encoding='utf-8') as f:
    mapping = json.load(f)

no_comp = {bare(k): v['kodex_name'] for k,v in mapping.items() if not v.get('competitors')}

PREFIXES = ['TIGER','ACE','PLUS','SOL','HANARO','RISE','KINDEX','WOORI','KB','NH']

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

def extract_keywords(name):
    clean = name.replace('KODEX','').replace('액티브','').replace('(합성)','')
    clean = clean.replace('(H)','').replace('TR','').replace('Plus','').strip()
    keywords = set()
    tokens = [t for t in re.split(r'[\s\-&]+', clean) if len(t) >= 2]
    for t in tokens:
        t2 = re.sub(r'^\d+', '', t).strip()
        if len(t2) >= 2: keywords.add(t2)
        if len(t) >= 2: keywords.add(t)
    for n in [4, 6, 8]:
        kw = clean[:n].strip()
        if len(kw) >= 2: keywords.add(kw)
    return [k for k in keywords if len(k) >= 2]

added = 0
for kcode, kname in no_comp.items():
    keywords = extract_keywords(kname)
    best_matches = {}
    for kw in keywords:
        for pfx in PREFIXES:
            hits = w[w['종목명'].str.startswith(pfx) &
                     w['종목명'].str.contains(kw, regex=False, na=False)]
            for _, r in hits.iterrows():
                ccode = bare(r['단축코드'])
                cname = r['종목명']
                if ccode == kcode: continue
                corr = get_corr(kcode, ccode)
                if corr is not None and corr >= 0.7:
                    if pfx not in best_matches or corr > best_matches[pfx][2]:
                        best_matches[pfx] = (ccode, cname, corr)

    if best_matches:
        comps = []
        if 'TIGER' in best_matches:
            cc, cn, cr = best_matches['TIGER']
            comps.append({'code': cc+'*001', 'name': cn, 'provider': 'TIGER', 'corr': round(cr,3)})
        for pfx in ['ACE','PLUS','SOL','HANARO','RISE','KINDEX']:
            if pfx in best_matches and len(comps) < 2:
                cc, cn, cr = best_matches[pfx]
                comps.append({'code': cc+'*001', 'name': cn, 'provider': pfx, 'corr': round(cr,3)})
        if not comps:
            for pfx, (cc, cn, cr) in sorted(best_matches.items(), key=lambda x: -x[1][2])[:2]:
                comps.append({'code': cc+'*001', 'name': cn, 'provider': pfx, 'corr': round(cr,3)})

        # mapping에 업데이트
        key = kcode + '*001'
        if key in mapping:
            mapping[key]['competitors'] = comps
            added += 1

with open('etf_mapping.json', 'w', encoding='utf-8') as f:
    json.dump(mapping, f, ensure_ascii=False, indent=2)

# 최종 현황
with_comp = sum(1 for v in mapping.values() if v.get('competitors'))
no_comp_final = sum(1 for v in mapping.values() if not v.get('competitors'))
print(f'추가됨: {added}개')
print(f'비교군 있음: {with_comp}개 / 없음: {no_comp_final}개')
