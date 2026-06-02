import sys, io, requests, re
from bs4 import BeautifulSoup
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
h = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

# 전체 이벤트 목록 (진행중 + 종료 포함)
r = requests.get('https://www.samsungfund.com/etf/lounge/event.do', headers=h, timeout=15)
soup = BeautifulSoup(r.text, 'lxml')

events = []
for a in soup.find_all('a', href=re.compile(r'event-view.*seq=')):
    href = a.get('href','')
    seq_m = re.search(r'seq=(\d+)', href)
    if not seq_m: continue
    seq = int(seq_m.group(1))
    full_text = a.get_text(' ', strip=True)
    title = re.split(r'이벤트기간|당첨자', full_text)[0].strip()
    title = re.sub(r'진행중\s*|종료\s*', '', title).strip()
    period_m = re.search(r'(\d{4}\.\d{2}\.\d{2})\s*~\s*(\d{4}\.\d{2}\.\d{2})', full_text)
    period = period_m.groups() if period_m else ('?','?')
    status = '진행중' if '진행중' in full_text else '종료'

    # ETF명 추출
    etf_m = re.findall(r'KODEX\s+((?!ETF|이벤트|매수|투자|증권사)[\w가-힣\-\+\.]+(?:\s+(?!ETF|이벤트|매수|투자)[\w가-힣\-\+\.]+)*)', title)
    etf = ', '.join(['KODEX '+e.strip() for e in etf_m]) if etf_m else '(제목에 ETF명 없음)'

    events.append({'seq':seq, 'title':title[:60], 'status':status, 'start':period[0], 'end':period[1], 'etf':etf})

# seq 중복 제거, 최신순 정렬
seen = set()
unique = []
for e in events:
    if e['seq'] not in seen:
        seen.add(e['seq'])
        unique.append(e)

unique.sort(key=lambda x: -x['seq'])
print(f"총 {len(unique)}개 이벤트\n")
for e in unique:
    print(f"[{e['status']}] seq={e['seq']} {e['start']}~{e['end']}")
    print(f"  {e['title']}")
    print(f"  ETF: {e['etf']}")
    print()
