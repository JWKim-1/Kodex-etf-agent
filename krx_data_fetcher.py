"""
KRX 자동 데이터 수집 모듈
pykrx + KRX 계정으로 ETF 투자자별 순매수 데이터 자동 수집
멘토님 엑셀 파일 대체 가능
"""

import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
from typing import Optional, Dict, List

# KRX 계정 설정 (.env에서 로드)
def _setup_krx_env():
    from dotenv import load_dotenv
    load_dotenv()
    krx_id = os.getenv("KRX_ID", "")
    krx_pw = os.getenv("KRX_PW", "")
    if krx_id:
        os.environ["KRX_ID"] = krx_id
    if krx_pw:
        os.environ["KRX_PW"] = krx_pw

def get_week_dates(target_date: date = None) -> tuple:
    """주어진 날짜가 속한 주의 월~금 반환."""
    if target_date is None:
        target_date = date.today()
    # 해당 주 월요일
    monday = target_date - timedelta(days=target_date.weekday())
    friday = monday + timedelta(days=4)
    return monday, friday

def fetch_weekly_etf_data(
    week_start: date = None,
    week_end: date = None,
    etf_codes: List[str] = None,
    max_etfs: int = 300,
) -> pd.DataFrame:
    """
    지정 주차의 ETF 투자자별 순매수 데이터를 KRX에서 직접 가져옴.
    멘토님 엑셀 파일과 동일한 형식으로 반환.

    Returns: DataFrame (종목코드, 종목명, 금융투자, 개인, 은행, 투신, 사모, 연기금등, 외국인, ...)
    """
    _setup_krx_env()

    try:
        from pykrx import stock
    except ImportError:
        raise ImportError("pykrx 미설치: pip install pykrx")

    if week_start is None or week_end is None:
        week_start, week_end = get_week_dates()

    start_str = week_start.strftime("%Y%m%d")
    end_str   = week_end.strftime("%Y%m%d")
    week_label = f"{week_start.month}.{week_start.day}-{week_end.month}.{week_end.day}"

    print(f"KRX 데이터 수집: {week_label} ({start_str}~{end_str})")

    # ETF 목록 가져오기
    if etf_codes is None:
        try:
            ticker_list = stock.get_etf_ticker_list(end_str)
            if ticker_list is None or len(ticker_list) == 0:
                ticker_list = stock.get_etf_ticker_list(start_str)
            etf_codes = list(ticker_list)[:max_etfs]
            print(f"  ETF 목록: {len(etf_codes)}개")
        except Exception as e:
            print(f"  ETF 목록 조회 실패: {e}")
            return pd.DataFrame()

    rows = []
    failed = 0
    for i, code in enumerate(etf_codes):
        try:
            df = stock.get_etf_trading_volume_and_value(start_str, end_str, code)
            if df is None or df.empty:
                continue

            # 거래대금 순매수 추출
            try:
                col_순매수 = ('거래대금', '순매수')
                if col_순매수 not in df.columns:
                    continue
                순매수 = df[col_순매수]

                # 투자자 유형별 매핑
                row = {"단축코드": f"{code}*001", "종목명": ""}
                investor_map = {
                    "금융투자": "금융투자",
                    "보험": "보험",
                    "투신": "투신",
                    "사모": "사모",
                    "은행": "은행",
                    "기타금융": "기타금융",
                    "연기금 등": "연기금 등",
                    "기관합계": "기관",
                    "기타법인": "기타법인",
                    "개인": "개인",
                    "외국인": "외국인",
                    "기타외국인": "외인기타",
                }
                for krx_name, col_name in investor_map.items():
                    if krx_name in 순매수.index:
                        row[col_name] = int(순매수[krx_name])
                    else:
                        row[col_name] = 0

                # 종목명
                try:
                    name = stock.get_etf_ticker_name(code)
                    row["종목명"] = name or code
                except Exception:
                    row["종목명"] = code

                rows.append(row)

                if (i + 1) % 50 == 0:
                    print(f"  진행: {i+1}/{len(etf_codes)}")

            except Exception as e:
                failed += 1

        except Exception as e:
            failed += 1

    if not rows:
        print("  데이터 없음")
        return pd.DataFrame()

    result = pd.DataFrame(rows)
    result["week"] = week_label
    print(f"  완료: {len(result)}개 종목, 실패: {failed}개")
    return result


def fetch_multiple_weeks(
    weeks: List[tuple],  # [(start_date, end_date), ...]
    etf_codes: List[str] = None,
) -> Dict[str, pd.DataFrame]:
    """여러 주차 데이터를 한번에 수집."""
    results = {}
    for start, end in weeks:
        label = f"{start.month}.{start.day}-{end.month}.{end.day}"
        try:
            df = fetch_weekly_etf_data(start, end, etf_codes)
            if not df.empty:
                results[label] = df
        except Exception as e:
            print(f"  {label} 실패: {e}")
    return results


CACHE_FILE = "krx_data_cache.parquet"

def save_cache(sheets: dict):
    """수집된 데이터를 로컬 parquet에 저장."""
    if not sheets:
        return
    rows = []
    for week_label, df in sheets.items():
        df = df.copy()
        df["week"] = week_label
        rows.append(df)
    combined = pd.concat(rows, ignore_index=True)
    combined.to_parquet(CACHE_FILE, index=False)
    print(f"캐시 저장: {CACHE_FILE} ({len(combined)}행, {len(sheets)}주차)")

def load_cache() -> dict:
    """저장된 캐시에서 데이터 로드."""
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        df = pd.read_parquet(CACHE_FILE)
        sheets = {}
        for week in df["week"].unique():
            sheets[week] = df[df["week"] == week].drop(columns=["week"]).reset_index(drop=True)
        print(f"캐시 로드: {len(sheets)}주차")
        return sheets
    except Exception as e:
        print(f"캐시 로드 실패: {e}")
        return {}

def get_missing_weeks(sheets: dict, from_date: date, to_date: date) -> list:
    """저장된 시트 중 빠진 주차 목록 반환."""
    existing = set(sheets.keys())
    missing = []
    cur = from_date - timedelta(days=from_date.weekday())
    while cur <= to_date:
        end_w = cur + timedelta(days=4)
        label = f"{cur.month}.{cur.day}-{end_w.month}.{end_w.day}"
        if label not in existing:
            missing.append((cur, min(end_w, to_date)))
        cur += timedelta(weeks=1)
    return missing


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    # 이번 주 데이터 테스트
    s, e = get_week_dates(date(2026, 5, 26))
    df = fetch_weekly_etf_data(s, e, etf_codes=["069500","229200","102110","091160","379800"], max_etfs=5)
    if not df.empty:
        print("\n수집 결과:")
        print(df[["단축코드","종목명","금융투자","개인","은행","투신"]].to_string())
