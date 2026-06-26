"""
KRX 자동 데이터 수집 모듈
pykrx + KRX 계정으로 ETF 투자자별 순매수 데이터 자동 수집
멘토님 엑셀 파일 대체 가능
"""

import os
import re
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
from typing import Optional, Dict, List

# KRX 수집 품질 기준 — 이 미만이면 수집 실패/빵꾸로 간주
KRX_MIN_ETF_COUNT = 950   # 전체 ETF 수 (1000+ 정상, 950 미만 = 빵꾸)
KRX_MIN_KODEX_COUNT = 200  # KODEX ETF 수

# KODEX ETF 우선 수집 목록 (멘토님 엑셀 기준 234개) — 모듈 레벨 상수
KODEX_PRIORITY = [
    '091160','233740','069500','261220','144600','114800','314250','379810','252670','0064K0',
    '476800','0115D0','091180','462330','337160','279530','138910','0153K0','091170','226490',
    '461950','278530','251340','401470','0098F0','0100K0','266360','445290','266370','300950',
    '449190','102780','0065G0','395170','0089D0','0080G0','481060','223190','266410','325010',
    '153130','437080','275300','114260','211900','473460','494890','0115E0','0144L0','0007F0',
    '156080','329660','453820','471230','329650','256750','226980','152380','487950','289040',
    '140710','0041E0','453810','276990','360140','419430','329670','0000H0','276970','337120',
    '483290','275290','400570','244620','269420','0119H0','0028X0','473290','101280','373490',
    '453650','0026E0','459580','261260','304660','437070','308620','488770','214980','457700',
    '423160','368680','0117L0','176950','0162M0','138920','244660','279540','280940','291890',
    '292770','304670','321410','450180','457690','481050','439860','453660','463690','428560',
    '390400','477730','456250','0089C0','463640','0048J0','363570','419420','404260','273130',
    '468630','261250','491090','200030','433980','271050','169950','453630','306950','0091C0',
    '434060','337150','439870','433970','244670','468380','463680','364690','0162L0','261240',
    '352540','450190','283580','409810','0082V0','251350','453640','489250','375770','461450',
    '280930','485540','352560','441640','102960','292190','185680','099140','275280','261270',
    '468370','0173Y0','476070','229720','219480','455030','0167Z0','0005A0','218420','446690',
    '273140','459560','395150','385520','428510','304940','213610','360150','0190G0','483280',
    '244580','0068M0','271060','484790','445150','0041D0','0038A0','298770','411420','475080',
    '359210','252650','266390','132030','0151S0','325020','409820','390390','0144M0','337140',
    '204450','266420','494300','315930','117460','140700','278540','117680','0132H0','415340',
    '449180','363580','284430','117700','372330','102970','0048K0','237370','498410','385510',
    '305720','237350','471990','487240','498400','0177N0','395160','448330','494310','495850',
    '487230','379800','122630','229200',
]

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


def _safe_import_pykrx(retry_wait: int = 30):
    """pykrx import 시 로그인 실패하면 대기 후 1회만 재시도."""
    import time
    try:
        from pykrx import stock
        return stock
    except Exception as e:
        print(f"  pykrx 로그인 실패, {retry_wait}초 대기 후 재시도...")
        time.sleep(retry_wait)
        try:
            import importlib
            import pykrx
            importlib.reload(pykrx)
            from pykrx import stock
            return stock
        except Exception as e2:
            print(f"  재시도 실패: {e2}")
            return None

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
    max_etfs: int = 9999,  # 사실상 제한 없음 — 거래 없는 ETF는 자동 skip
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

    # ETF 목록 가져오기 (KODEX_PRIORITY는 모듈 상단 상수 사용)
    if etf_codes is None:
        try:
            ticker_list = stock.get_etf_ticker_list(end_str)
            if ticker_list is None or len(ticker_list) == 0:
                ticker_list = stock.get_etf_ticker_list(start_str)
            all_codes = list(ticker_list)
            # KODEX 우선 정렬: KODEX 코드 먼저, 나머지 뒤에
            priority = [c for c in KODEX_PRIORITY if c in all_codes]
            rest = [c for c in all_codes if c not in set(priority)]
            etf_codes = (priority + rest)[:max_etfs]
            print(f"  ETF 목록: {len(etf_codes)}개 (KODEX 우선 {len(priority)}개)")
        except Exception as e:
            print(f"  ETF 목록 조회 실패: {e}")
            return pd.DataFrame()

    rows = []
    failed = 0
    _session_start = datetime.now()
    _SESSION_LIMIT = 25  # 25분마다 선제적 재로그인 (KRX 세션 30분 만료)

    for i, code in enumerate(etf_codes):
        # 25분 경과 시 선제적 재로그인
        if (datetime.now() - _session_start).seconds > _SESSION_LIMIT * 60:
            try:
                print("  세션 선제 갱신 중...", flush=True)
                _setup_krx_env()
                import importlib, pykrx.website.comm.webio as _webio
                importlib.reload(_webio)
                _session_start = datetime.now()
                print("  세션 갱신 완료", flush=True)
            except Exception as _e:
                print(f"  세션 갱신 실패: {_e}", flush=True)

        try:
            # 종목명 먼저 확보 (거래 없는 종목도 이름은 저장)
            try:
                name = stock.get_etf_ticker_name(code)
                name = name or code
            except Exception:
                name = code

            row = {"종목코드": code, "종목명": name, "금융투자": 0, "개인": 0, "은행": 0}

            df = stock.get_etf_trading_volume_and_value(start_str, end_str, code)
            if df is not None and not df.empty:
                col_순매수 = ('거래대금', '순매수')
                if col_순매수 in df.columns:
                    순매수 = df[col_순매수]
                    investor_map = {"금융투자": "금융투자", "은행": "은행", "개인": "개인"}
                    for krx_name, col_name in investor_map.items():
                        if krx_name in 순매수.index:
                            # KRX 원 단위 → 천원 단위 (멘토 엑셀 기준)
                            row[col_name] = int(순매수[krx_name] / 1000)

            rows.append(row)

            if (i + 1) % 50 == 0:
                print(f"  진행: {i+1}/{len(etf_codes)}")

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
TREND_CACHE_FILE = "krx_trend_cache.parquet"


def fetch_etf_market_summary_naver() -> pd.DataFrame:
    """
    네이버 금융 ETF 목록 API로 실시간 ETF 데이터 수집.
    KRX IP 차단 시 폴백으로 사용. 수익률/거래대금/시총은 있으나 투자자별 수급 없음.
    """
    import requests as _req
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Referer": "https://finance.naver.com/",
    }
    r = _req.get("https://finance.naver.com/api/sise/etfItemList.nhn",
                 headers=HEADERS, timeout=15)
    r.raise_for_status()
    items = r.json().get("result", {}).get("etfItemList", [])
    if not items:
        raise ValueError("네이버 ETF 목록 비어있음")
    rows = []
    for x in items:
        rows.append({
            "종목코드":    str(x.get("itemcode", "")),
            "종목명":      x.get("itemname", ""),
            "수익률_pct":  float(x.get("changeRate") or 0),
            "거래대금_억": float(x.get("amonut") or 0) / 100,  # 백만원 → 억원
            "시총_억":     float(x.get("marketSum") or 0) / 100,
            "현재가":      float(x.get("nowVal") or 0),
            "NAV":         float(x.get("nav") or 0),
            "3개월수익률": float(x.get("threeMonthEarnRate") or 0),
            "마지막종가":  float(x.get("nowVal") or 0),
            "금융투자": 0, "개인": 0, "은행": 0,  # 네이버는 투자자 수급 없음
            "data_source": "naver",
        })
    df = pd.DataFrame(rows)
    print(f"  네이버 금융 ETF 수집 완료: {len(df)}개")
    return df


def fetch_etf_market_summary(week_start: date, week_end: date) -> pd.DataFrame:
    """
    주간 ETF 수익률·거래대금 조회.
    KRX 차단 시 네이버 금융 API로 자동 폴백.
    """
    _setup_krx_env()
    try:
        from pykrx import stock
    except ImportError:
        raise ImportError("pykrx 미설치: pip install pykrx")

    end_str   = week_end.strftime("%Y%m%d")
    start_str = week_start.strftime("%Y%m%d")
    prev_friday = week_start - timedelta(days=3)
    prev_str  = prev_friday.strftime("%Y%m%d")
    week_label = f"{week_start.month}.{week_start.day}-{week_end.month}.{week_end.day}"

    # ETF 티커 목록 (KODEX 우선 + 전체)
    try:
        all_tickers = list(stock.get_etf_ticker_list(end_str) or [])
    except Exception as e:
        print(f"  ETF 목록 조회 실패: {e}")
        return pd.DataFrame()

    # KODEX 우선 정렬 후 전체 포함 (최대 300개 — 속도/커버리지 균형)
    priority = [c for c in KODEX_PRIORITY if c in set(all_tickers)]
    rest     = [c for c in all_tickers if c not in set(priority)]
    target_tickers = (priority + rest)[:300]
    print(f"  수집 대상: {len(target_tickers)}개 ETF (KODEX {len(priority)}개 우선)")

    rows = []
    failed = 0
    _session_start = datetime.now()
    _SESSION_LIMIT = 25

    for i, code in enumerate(target_tickers):
        # 세션 선제 갱신
        if (datetime.now() - _session_start).seconds > _SESSION_LIMIT * 60:
            try:
                _setup_krx_env()
                import importlib, pykrx.website.comm.webio as _webio
                importlib.reload(_webio)
                _session_start = datetime.now()
            except Exception:
                pass

        try:
            # 이번 주 일별 OHLCV (시가·고가·저가·종가·거래량·거래대금)
            df_w = stock.get_etf_ohlcv_by_date(start_str, end_str, code)
            if df_w is None or df_w.empty:
                continue

            # 직전 금요일 종가 (수익률 기준)
            try:
                df_p = stock.get_etf_ohlcv_by_date(prev_str, prev_str, code)
                prev_close = float(df_p["종가"].iloc[-1]) if (df_p is not None and not df_p.empty and "종가" in df_p.columns) else np.nan
            except Exception:
                prev_close = np.nan

            last_close = float(df_w["종가"].iloc[-1]) if "종가" in df_w.columns else np.nan
            ret_pct = (last_close - prev_close) / prev_close * 100 if (not np.isnan(prev_close) and prev_close != 0) else np.nan

            vol_col = "거래대금" if "거래대금" in df_w.columns else None
            total_vol = float(df_w[vol_col].sum()) if vol_col else 0.0

            try:
                name = stock.get_etf_ticker_name(code)
            except Exception:
                name = code

            rows.append({
                "종목코드": code,
                "종목명":   name,
                "수익률_pct": ret_pct,
                "거래대금_억": total_vol / 1e8,
                "마지막종가": last_close,
            })

        except Exception:
            failed += 1

        if (i + 1) % 50 == 0:
            print(f"  진행: {i+1}/{len(target_tickers)} (실패 {failed}개)")

    if not rows:
        print("  KRX 데이터 없음 — 네이버 금융 폴백 시도")
        return fetch_etf_market_summary_naver()

    df_result = pd.DataFrame(rows)
    df_result["week"] = week_label
    df_result["data_source"] = "krx"
    print(f"  시장 트렌드 수집 완료: {len(df_result)}개 ETF, 실패 {failed}개, 기준주 {week_label}")
    return df_result


def load_trend_cache() -> dict:
    """주간 시장 트렌드 캐시 로드."""
    if not os.path.exists(TREND_CACHE_FILE):
        return {}
    try:
        df = pd.read_parquet(TREND_CACHE_FILE)
        result = {}
        for week in df["week"].unique():
            result[week] = df[df["week"] == week].drop(columns=["week"]).reset_index(drop=True)
        return result
    except Exception as e:
        print(f"트렌드 캐시 로드 실패: {e}")
        return {}


def save_trend_cache(week_label: str, df: pd.DataFrame):
    """주간 시장 트렌드 데이터 캐시에 추가/갱신.
    장마감(15:30 KST) 이후 수집분만 유효 — 실시간 데이터 오염 방지.
    금요일 15:30 이후 ~ 다음주 화요일까지만 해당 주차로 저장 허용."""
    from datetime import date as _date, timedelta as _td, datetime as _dt
    import pytz as _pytz
    try:
        _kst = _pytz.timezone("Asia/Seoul")
        _now_kst = _dt.now(_kst)
    except Exception:
        _now_kst = _dt.now()

    today = _now_kst.date()
    weekday = _now_kst.weekday()   # 0=월 ... 4=금 5=토 6=일
    hour = _now_kst.hour * 60 + _now_kst.minute  # 분 단위

    MARKET_CLOSE = 15 * 60 + 30  # 15:30

    week_start = _parse_week_label(week_label)
    if week_start:
        week_friday = week_start + _td(days=4)
        allow_until = week_friday + _td(days=4)  # 다음주 화요일

        # 금요일이면 15:30 이후만 허용
        if weekday == 4 and hour < MARKET_CLOSE:
            print(f"트렌드 캐시 저장 거부: 장마감(15:30) 전 수집 불가 (현재 {_now_kst.strftime('%H:%M')} KST)")
            return

        # 해당 주차 허용 범위 체크
        if not (week_friday <= today <= allow_until):
            print(f"트렌드 캐시 저장 거부: {week_label}은 금요일({week_friday}) 장마감 후 수집해야 함 (오늘={today})")
            return

    existing = load_trend_cache()
    df_save = df.copy()
    if "week" in df_save.columns:
        df_save = df_save.drop(columns=["week"])
    existing[week_label] = df_save
    all_dfs = []
    for wk, wdf in existing.items():
        wdf = wdf.copy()
        wdf["week"] = wk
        all_dfs.append(wdf)
    pd.concat(all_dfs, ignore_index=True, sort=False).to_parquet(TREND_CACHE_FILE, index=False)
    print(f"트렌드 캐시 저장: {week_label}")

def save_cache(sheets: dict):
    """수집된 데이터를 로컬 parquet에 저장."""
    if not sheets:
        return
    rows = []
    for week_label, df in sheets.items():
        df = df.copy()
        # week 컬럼 중복 방지 (fetch_weekly_etf_data에서 이미 추가된 경우)
        if "week" in df.columns:
            df = df.drop(columns=["week"])
        df["week"] = week_label
        # 중복 컬럼 제거
        df = df.loc[:, ~df.columns.duplicated()]
        rows.append(df)
    combined = pd.concat(rows, ignore_index=True, sort=False)
    combined.to_parquet(CACHE_FILE, index=False)
    print(f"캐시 저장: {CACHE_FILE} ({len(combined)}행, {len(sheets)}주차)")

def _normalize_codes(df: pd.DataFrame) -> pd.DataFrame:
    """단축코드 *001 suffix 제거 + 컬럼명을 '종목코드'로 통일.
    이후 모든 analyzer/app에서 '종목코드' 하나만 쓰면 됨."""
    if "단축코드" in df.columns:
        df = df.copy()
        df["단축코드"] = df["단축코드"].astype(str).str.split("*").str[0].str.strip()
        df = df.rename(columns={"단축코드": "종목코드"})
    elif "종목코드" in df.columns:
        df = df.copy()
        df["종목코드"] = df["종목코드"].astype(str).str.split("*").str[0].str.strip()
    return df


def load_cache() -> dict:
    """저장된 캐시에서 데이터 로드."""
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        df = _normalize_codes(pd.read_parquet(CACHE_FILE))
        sheets = {}
        for week in df["week"].unique():
            sheets[week] = df[df["week"] == week].drop(columns=["week"]).reset_index(drop=True)
        print(f"캐시 로드: {len(sheets)}주차")
        return sheets
    except Exception as e:
        print(f"캐시 로드 실패: {e}")
        return {}

BASELINE_WEEKS = 8  # 베이스라인 윈도우 (변경 시 여기만 수정)


def load_cache_recent(n_weeks: int = BASELINE_WEEKS + 1) -> dict:
    """
    캐시에서 최근 N주만 로드 (앱 시작 속도 최적화).
    n_weeks = 8주 베이스라인 + 현재 주 1 = 9주
    """
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        df = _normalize_codes(pd.read_parquet(CACHE_FILE))
        # 주차 정렬 (시트명 기준 날짜 파싱)
        weeks_all = sorted(df["week"].unique(), key=lambda w: _parse_week_label(w) or date.min)
        weeks_recent = weeks_all[-n_weeks:]
        df_recent = df[df["week"].isin(weeks_recent)]
        sheets = {}
        for week in weeks_recent:
            sheets[week] = df_recent[df_recent["week"] == week].drop(columns=["week"]).reset_index(drop=True)
        print(f"캐시 최근 {len(sheets)}주 로드 (전체 {len(weeks_all)}주 중)")
        return sheets
    except Exception as e:
        print(f"캐시 로드 실패: {e}")
        return {}


def _parse_week_label(label: str):
    """'3.2-3.6' 형태 주차 레이블 → date 변환."""
    import re
    m = re.match(r"(\d{1,2})\.(\d{1,2})", label)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = date.today().year if month <= date.today().month else date.today().year - 1
        try:
            return date(year, month, day)
        except Exception:
            return None
    return None


def patch_week(week_label: str, min_kodex: int = 200, min_tiger: int = 150) -> bool:
    """
    특정 주차에서 KODEX/TIGER가 부족하면 해당 ETF만 골라서 추가 수집 후 캐시에 병합.
    전체 재수집 없이 빠진 부분만 채움.
    """
    _setup_krx_env()
    stock = _safe_import_pykrx()
    if stock is None:
        return False

    existing = load_cache()
    if week_label not in existing:
        print(f"{week_label} 캐시에 없음")
        return False

    df = existing[week_label]
    kodex_cnt = df['종목명'].str.contains('KODEX', na=False).sum()
    tiger_cnt = df['종목명'].str.contains('TIGER', na=False).sum()

    if kodex_cnt >= min_kodex and tiger_cnt >= min_tiger:
        print(f"{week_label} 이미 충분 (KODEX {kodex_cnt}, TIGER {tiger_cnt})")
        return False

    print(f"{week_label} 부분 보완 시작 (KODEX {kodex_cnt}, TIGER {tiger_cnt})")

    # 이미 수집된 코드 제외 (df는 load_cache()에서 이미 정규화됨 → '종목코드', 접미사 없음)
    existing_codes = set(df['종목코드'].astype(str).str.strip())

    # 보완 대상: KODEX 우선 + 전체 목록에서 누락된 것
    m = re.match(r"(\d{1,2})\.(\d{1,2})-(\d{1,2})\.(\d{1,2})", week_label)
    if not m:
        return False
    import re as _re
    m = _re.match(r"(\d{1,2})\.(\d{1,2})-(\d{1,2})\.(\d{1,2})", week_label)
    parsed = _parse_week_label(week_label)
    if not parsed:
        return False
    ws = parsed
    we = ws + timedelta(days=4)
    start_str = ws.strftime("%Y%m%d")
    end_str = we.strftime("%Y%m%d")

    try:
        all_codes = list(stock.get_etf_ticker_list(end_str) or [])
        missing_codes = [c for c in (KODEX_PRIORITY + all_codes) if c not in existing_codes]
        missing_codes = list(dict.fromkeys(missing_codes))  # 중복 제거
    except Exception as e:
        print(f"  목록 조회 실패: {e}")
        return False

    print(f"  보완 대상: {len(missing_codes)}개")
    df_patch = fetch_weekly_etf_data(ws, we, etf_codes=missing_codes)
    if df_patch.empty:
        return False

    # 새로 받은 데이터는 '단축코드'+'*001' 원본 형태 → df(이미 정규화됨)와 컬럼/형식 통일
    df_patch = _normalize_codes(df_patch)

    # 기존 데이터와 병합
    combined = pd.concat([df, df_patch], ignore_index=True)
    combined = combined.drop_duplicates(subset=['종목코드'], keep='last')
    existing[week_label] = combined
    save_cache(existing)

    new_kodex = combined['종목명'].str.contains('KODEX', na=False).sum()
    new_tiger = combined['종목명'].str.contains('TIGER', na=False).sum()
    print(f"  보완 완료: KODEX {kodex_cnt}→{new_kodex}, TIGER {tiger_cnt}→{new_tiger}")
    return True


def verify_cache(min_kodex: int = 150, min_tiger: int = 100) -> dict:
    """캐시 품질 검사. 기준 미달 주차 반환."""
    existing = load_cache()
    issues = {}
    for week_label in sorted(existing.keys(), key=lambda w: _parse_week_label(w) or date.min):
        df = existing[week_label]
        kodex_cnt = df['종목명'].str.contains('KODEX', na=False).sum()
        tiger_cnt = df['종목명'].str.contains('TIGER', na=False).sum()
        total = len(df)
        if kodex_cnt < min_kodex or tiger_cnt < min_tiger:
            issues[week_label] = {'kodex': kodex_cnt, 'tiger': tiger_cnt, 'total': total}
    return issues


def patch_all_weeks(min_kodex: int = 150, min_tiger: int = 100, max_rounds: int = 3):
    """
    캐시 전체 품질 체크 → 부족한 주차 보완 → 재검사.
    원하는 품질 될 때까지 최대 max_rounds회 반복.
    """
    existing = refresh_stale_weeks(load_cache())
    save_cache(existing)
    for round_num in range(1, max_rounds + 1):
        issues = verify_cache(min_kodex, min_tiger)
        if not issues:
            print(f"[OK] 전체 품질 통과 ({round_num-1}라운드 보완)")
            return
        print(f"\n[{round_num}라운드] 미달 주차 {len(issues)}개 보완 시작")
        for week_label, counts in issues.items():
            print(f"  {week_label}: KODEX {counts['kodex']}, TIGER {counts['tiger']}")
            patch_week(week_label, min_kodex, min_tiger)
    # 최종 결과 출력
    remaining = verify_cache(min_kodex, min_tiger)
    if remaining:
        print(f"[WARNING] {max_rounds}라운드 후에도 미달: {list(remaining.keys())}")
    else:
        print("[OK] 전체 품질 통과")


def fetch_full_history(
    from_date: date = date(2025, 1, 6),  # 2025년 1월 첫째 주
    to_date: date = None,
    progress_callback=None,
) -> dict:
    """
    2025년 1월부터 현재까지 전체 주차 데이터를 수집하고 캐시에 저장.
    이미 수집된 주차는 스킵.
    """
    if to_date is None:
        to_date = date.today()

    existing = load_cache()
    existing = refresh_stale_weeks(existing)  # 부분 수집된 과거 주차부터 5일 완전본으로 교체
    missing = get_missing_weeks(existing, from_date, to_date)

    if not missing:
        print("수집할 새 주차 없음")
        return existing

    print(f"수집 대상: {len(missing)}주차")
    for i, (ws, we) in enumerate(missing):
        label = f"{ws.month}.{ws.day}-{we.month}.{we.day}"
        if progress_callback:
            progress_callback(i + 1, len(missing), label)
        try:
            df_w = fetch_weekly_etf_data(ws, we)
            if not df_w.empty:
                existing[label] = df_w
        except Exception as e:
            print(f"  {label} 실패: {e}")

    if existing:
        save_cache(existing)

    # 수집 완료 후 자동 품질 검사 및 보완
    print("\n[품질 검사 시작]")
    patch_all_weeks()

    return existing


def refresh_stale_weeks(existing: dict) -> dict:
    """
    주중(예: 수요일)에 수집되어 5거래일 미만으로 저장된 과거 주차 중,
    이제 그 주(월~금)가 완전히 끝난 것들을 5일 전체로 재수집해서 교체.
    예: '6.1-6.3'(부분, 3일치)로 저장됐는데 그 주의 금요일이 이미 지났다면
        '6.1-6.5'(전체, 5일치)로 재수집 후 stale 항목 제거.
    DiD는 N주 평균과 비교하는 계산이라 대조군에 부분 주차가 섞이면 왜곡됨 — 반드시 5일 완성본으로 교체해야 함.
    """
    today = date.today()
    stale = []
    for label in list(existing.keys()):
        parsed_start = _parse_week_label(label)
        if not parsed_start:
            continue
        m = re.match(r"(\d{1,2})\.(\d{1,2})-(\d{1,2})\.(\d{1,2})", label)
        if not m:
            continue
        end_month, end_day = int(m.group(3)), int(m.group(4))
        year = parsed_start.year if end_month >= parsed_start.month else parsed_start.year + 1
        try:
            parsed_end = date(year, end_month, end_day)
        except Exception:
            continue
        canonical_friday = parsed_start + timedelta(days=4)
        # 부분 수집(끝날이 금요일보다 이름) + 그 주가 이미 끝났으면 재수집 대상
        if parsed_end < canonical_friday and canonical_friday < today:
            stale.append((label, parsed_start, canonical_friday))

    for label, ws, we in stale:
        print(f"[재수집] {label}: 부분 수집된 주차 — 해당 주가 끝났으므로 5일 전체로 재수집", flush=True)
        try:
            df_full = fetch_weekly_etf_data(ws, we)
            if df_full.empty:
                print(f"  재수집 실패 — 기존(부분) 데이터 유지", flush=True)
                continue
            df_full = _normalize_codes(df_full)
            new_label = df_full['week'].iloc[0]
            del existing[label]
            existing[new_label] = df_full.drop(columns=['week'])
            print(f"  교체 완료: {label} → {new_label}", flush=True)
        except Exception as e:
            print(f"  재수집 실패: {e}", flush=True)

    return existing


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


def detect_new_listings(lookback_weeks: int = 4) -> list:
    """
    캐시에서 신규 상장 ETF 감지.
    최근 lookback_weeks 기간 이전에는 없었다가 최신 주차에 처음 등장한 ETF 반환.

    반환: [{"code": "0193W0", "name": "KODEX 삼성전자단일종목레버리지",
             "first_seen": "5.25-5.28"}, ...]

    4번째 ETF 사후관리 Agent 용도로 설계됨.
    """
    existing = load_cache()
    if not existing:
        return []

    weeks = sorted(existing.keys(), key=lambda w: _parse_week_label(w) or date.min)
    if len(weeks) < 2:
        return []

    latest_week = weeks[-1]
    reference_weeks = weeks[max(0, len(weeks) - lookback_weeks - 1):-1]

    # 기준 기간 전체에 등장한 코드
    historic_codes = set()
    for w in reference_weeks:
        df = existing[w]
        col = "종목코드" if "종목코드" in df.columns else "단축코드"
        codes = df[col].astype(str).str.split("*").str[0].str.strip()
        historic_codes.update(codes)

    # 최신 주차 코드
    latest_df = existing[latest_week]
    col = "종목코드" if "종목코드" in latest_df.columns else "단축코드"
    latest_df = latest_df.copy()
    latest_df["_code"] = latest_df[col].astype(str).str.split("*").str[0].str.strip()

    new_codes = set(latest_df["_code"]) - historic_codes

    results = []
    for code in sorted(new_codes):
        row = latest_df[latest_df["_code"] == code]
        if row.empty:
            continue
        name = str(row["종목명"].iloc[0]) if "종목명" in row.columns else code
        results.append({
            "code": code,
            "name": name,
            "first_seen": latest_week,
        })

    return results


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    # 이번 주 데이터 테스트
    s, e = get_week_dates(date(2026, 5, 26))
    df = fetch_weekly_etf_data(s, e, etf_codes=["069500","229200","102110","091160","379800"], max_etfs=5)
    if not df.empty:
        print("\n수집 결과:")
        print(df[["단축코드","종목명","금융투자","개인","은행","투신"]].to_string())


import re as _re

_MATURITY_PATTERN = _re.compile(r'\b\d{2}-\d{2}\b')  # 26-06, 25-12 등 만기형 ETF 이름 패턴


def _classify_delisting(code: str, name: str, disappear_week: str,
                         all_weeks: list, cache: dict) -> str:
    """
    상폐 의심 종목의 사유를 분류.
    Returns: 'maturity_redemption' | 'collection_gap' | 'delisting_confirmed' | 'delisting_pending'
    """
    idx = all_weeks.index(disappear_week)
    later_weeks = all_weeks[idx:]
    reappeared = any(
        code in set(cache[w]["종목코드"].astype(str).tolist())
        for w in later_weeks
    )
    if reappeared:
        return "collection_gap"

    # 2) 만기형 ETF (이름에 YY-MM 패턴) — 재등장 없는 경우에만 적용
    if _MATURITY_PATTERN.search(name):
        return "maturity_redemption"

    # 3) 연속 2주 이상 미등장 → 상폐 확정, 1주만 → 추적 중
    missing_count = sum(
        1 for w in later_weeks
        if code not in set(cache[w]["종목코드"].astype(str).tolist())
    )
    if missing_count >= 2:
        return "delisting_confirmed"

    # 4) 마지막 주차 1회만 빠진 경우: 직전 5주 중 3주 이상 등장했으면 수집 오류 가능성
    prev_weeks = all_weeks[max(0, idx - 5):idx]
    stable_count = sum(
        1 for w in prev_weeks
        if code in set(cache[w]["종목코드"].astype(str).tolist())
    )
    if stable_count >= 3 and idx == len(all_weeks) - 1:
        return "collection_gap"

    return "delisting_pending"


def _classify_new_listing(code: str, appear_week: str,
                           all_weeks: list, cache: dict) -> str:
    """
    신규상장 확정 여부.
    Returns: 'confirmed' (다음 주에도 존재) | 'pending' (마지막 주 등장, 아직 검증 불가)
    """
    idx = all_weeks.index(appear_week)
    if idx + 1 >= len(all_weeks):
        return "pending"
    next_week = all_weeks[idx + 1]
    exists_next = code in set(cache[next_week]["종목코드"].astype(str).tolist())
    return "confirmed" if exists_next else "pending"


def detect_listing_changes(cache: dict = None) -> dict:
    """
    주차별 캐시를 비교해 신규상장(+) / 상장폐지(-) ETF 감지.
    모든 ETF를 수집해야 가능하므로 fetch_weekly_etf_data가 거래 없는 종목도 저장해야 함.

    필터링 로직:
    - 만기형 ETF (이름에 YY-MM 패턴): maturity_redemption 으로 자동 분류
    - 1주 갭 후 재등장: collection_gap (수집 오류)
    - 2주+ 연속 미등장: delisting_confirmed
    - 1주 미등장, 아직 후속 주 없음: delisting_pending
    - 신규상장도 다음 주 확인 여부로 confirmed/pending 구분

    Returns:
        {
          "new_listings": [{"week", "종목코드", "종목명", "status": confirmed|pending}],
          "delistings":   [{"week", "종목코드", "종목명", "last_seen",
                            "reason": maturity_redemption|collection_gap|
                                      delisting_confirmed|delisting_pending}],
        }
    """
    if cache is None:
        cache = load_cache()

    sorted_weeks = sorted(cache.keys(), key=lambda w: _parse_week_label(w) or date.min)

    # 코드별 첫 등장 주 추적 (중복 신규상장 방지)
    first_seen: dict = {}
    new_listings = []
    raw_removed: list = []  # (disappear_week, code, name)

    def _is_bad_week(w: str) -> bool:
        """KRX 수집 품질 기준 미달 주차 (빵꾸) 판별."""
        df = cache.get(w, None)
        if df is None: return True
        total = len(df)
        kodex = df["종목명"].str.contains("KODEX", na=False).sum() if "종목명" in df.columns else 0
        return total < KRX_MIN_ETF_COUNT or kodex < KRX_MIN_KODEX_COUNT

    for i in range(1, len(sorted_weeks)):
        prev_week = sorted_weeks[i - 1]
        curr_week = sorted_weeks[i]
        # 빵꾸 주차는 신규/상폐 감지에서 제외 (노이즈 방지)
        if _is_bad_week(prev_week) or _is_bad_week(curr_week):
            continue
        prev_codes = set(cache[prev_week]["종목코드"].astype(str).tolist())
        curr_codes = set(cache[curr_week]["종목코드"].astype(str).tolist())

        # 신규 등장
        for code in curr_codes - prev_codes:
            if code in first_seen:
                continue  # 재등장은 신규상장 아님
            first_seen[code] = curr_week
            row = cache[curr_week][cache[curr_week]["종목코드"].astype(str) == code]
            name = row["종목명"].iloc[0] if not row.empty else code
            status = _classify_new_listing(code, curr_week, sorted_weeks, cache)
            new_listings.append({"week": curr_week, "종목코드": code, "종목명": name, "status": status})

        # 사라진 종목 (1차 수집 — 이후 필터링)
        for code in prev_codes - curr_codes:
            row = cache[prev_week][cache[prev_week]["종목코드"].astype(str) == code]
            name = row["종목명"].iloc[0] if not row.empty else code
            raw_removed.append((curr_week, code, name))

    # 중복 제거: 같은 code의 여러 소멸 이벤트 중 "재등장 이후 마지막 소멸"을 최종 처리
    # (KRX 빵꾸로 잠깐 사라진 뒤 진짜 상폐되는 경우를 정확히 잡기 위함)
    # raw_removed는 시간순으로 쌓이므로, 같은 code에 대해 마지막 소멸만 남긴다
    # 단, collection_gap(재등장)으로 분류된 이전 소멸은 건너뜀
    code_to_last: dict = {}
    for disappear_week, code, name in raw_removed:
        code_to_last[code] = (disappear_week, name)  # 덮어쓰기로 마지막만 유지

    delistings = []
    for code, (disappear_week, name) in code_to_last.items():
        reason = _classify_delisting(code, name, disappear_week, sorted_weeks, cache)
        entry = {
            "week":      disappear_week,
            "종목코드":  code,
            "종목명":    name,
            "last_seen": sorted_weeks[sorted_weeks.index(disappear_week) - 1],
            "reason":    reason,
            "llm_verified": None,
            "llm_summary":  "",
        }
        # 2주 연속 미등장(delisting_confirmed)이면 LLM으로 실제 상폐 여부 검증
        if reason == "delisting_confirmed":
            entry["llm_verified"], entry["llm_summary"] = _verify_delisting_llm(name, code)
        delistings.append(entry)

    return {"new_listings": new_listings, "delistings": delistings}


def _verify_delisting_llm(name: str, code: str) -> tuple:
    """
    상폐 의심 ETF를 LLM + 네이버 뉴스로 검증.
    Returns: (verified: bool|None, summary: str)
    - True: 실제 상폐 확인
    - False: 수집 오류로 판단
    - None: 확인 불가
    """
    import os as _os
    naver_id  = _os.getenv("NAVER_CLIENT_ID", "")
    naver_sec = _os.getenv("NAVER_CLIENT_SECRET", "")
    ant_key   = _os.getenv("ANTHROPIC_API_KEY", "")

    # 1단계: 네이버 뉴스에서 상폐 관련 뉴스 검색
    news_texts = []
    if naver_id:
        try:
            import requests as _req
            r = _req.get("https://openapi.naver.com/v1/search/news.json",
                params={"query": f"{name} 상장폐지", "display": 5, "sort": "date"},
                headers={"X-Naver-Client-Id": naver_id, "X-Naver-Client-Secret": naver_sec},
                timeout=8)
            import re as _re
            for item in r.json().get("items", []):
                title = _re.sub(r"<[^>]+>", "", item.get("title", ""))
                desc  = _re.sub(r"<[^>]+>", "", item.get("description", ""))
                news_texts.append(f"{title}: {desc[:100]}")
        except Exception:
            pass

    if not news_texts:
        return None, "뉴스 검색 결과 없음 — 확인 불가"

    if not ant_key:
        # LLM 없이 키워드만으로 판단
        combined = " ".join(news_texts).lower()
        if "상장폐지" in combined or "상폐" in combined or "만기" in combined:
            return True, f"키워드 감지: {news_texts[0][:80]}"
        return False, "상폐 관련 뉴스 없음 — 수집 오류 가능성"

    # 2단계: LLM으로 판단
    try:
        from llm_client import call_llm
        prompt = f"""ETF '{name}'(코드:{code})이 KRX 데이터에서 2주 연속 사라졌습니다.
아래 뉴스를 보고 실제 상장폐지가 맞는지 판단하세요.

뉴스:
{chr(10).join(news_texts)}

JSON으로만 응답:
{{"delisted": true/false/null, "reason": "만기청산|AUM미달|수집오류|불명확", "summary": "1줄 요약"}}
- delisted: true=상폐확인, false=수집오류, null=불명확"""
        import json as _json
        raw = call_llm(prompt, anthropic_key=ant_key, max_tokens=200)
        raw = raw.strip().lstrip("```json").rstrip("```").strip()
        result = _json.loads(raw)
        return result.get("delisted"), result.get("summary", "")
    except Exception as e:
        return None, f"LLM 검증 실패: {e}"
