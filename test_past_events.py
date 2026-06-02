import sys, io, requests, re, time
from bs4 import BeautifulSoup
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
h = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

past_seqs = [86,85,84,83,82,81,80,79,78,77,76,74,73,72,71,70,69,68,64,63,58,57,55,54,51,50,49,47,35,30,29]

results = []
for seq in past_seqs:
    url = f"https://www.samsungfund.com/etf/lounge/event-view-end.do?seq={seq}"
    try:
        r = requests.get(url, headers=h, timeout=10)
        soup = BeautifulSoup(r.text, 'lxml')
        text = soup.get_text(' ', strip=True)

        # 제목
        h1 = soup.find('h2') or soup.find('h3')
        title = h1.get_text(strip=True) if h1 else ''
        if not title:
            m = re.search(r'이벤트기간.{0,5}\d{4}', text)
            title_m = re.search(r'KODEX[\w\s가-힣]+이벤트', text)
            title = title_m.group() if title_m else ''

        # 기간
        period_m = re.search(r'(\d{4}\.\d{2}\.\d{2})\s*~\s*(\d{4}\.\d{2}\.\d{2})', text)
        period = f"{period_m.group(1)}~{period_m.group(2)}" if period_m else '기간미상'

        # ETF명
        etf_m = re.findall(r'KODEX\s+((?!ETF|이벤트|매수|투자|페이지로|분배금)[\w가-힣\-\+\.]+(?:\s+(?!ETF|이벤트)[\w가-힣\-\+\.]+){0,3})', text)
        etf_m += [img.get('alt','') for img in soup.find_all('img') if 'KODEX' in img.get('alt','')]
        etf = ', '.join(list(dict.fromkeys([e.strip() for e in etf_m if len(e.strip())>2]))[:3])

        if title or etf:
            results.append({'seq':seq, 'period':period, 'title':title[:60], 'etf':etf[:60]})
        time.sleep(0.3)
    except:
        pass

results.sort(key=lambda x: x['period'], reverse=True)
print(f"종료 이벤트 {len(results)}개\n")
for r2 in results:
    print(f"seq={r2['seq']} [{r2['period']}]")
    print(f"  {r2['title']}")
    print(f"  ETF: {r2['etf']}")
    print()
