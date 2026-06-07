import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
os.environ['KRX_ID'] = 'kjwoo6307'
os.environ['KRX_PW'] = 'Clzls9932!'
from pykrx import stock
from datetime import date

# 이번 주 ETF 목록
list_now = set(stock.get_etf_ticker_list("20260530") or [])
# 지난 주 ETF 목록
list_prev = set(stock.get_etf_ticker_list("20260523") or [])

new_etfs = list_now - list_prev
delisted = list_prev - list_now

print(f"신규 상장: {len(new_etfs)}개")
for t in list(new_etfs)[:5]:
    name = stock.get_etf_ticker_name(t)
    print(f"  {t}: {name}")

print(f"\n상장 폐지: {len(delisted)}개")
for t in list(delisted)[:5]:
    print(f"  {t}")
