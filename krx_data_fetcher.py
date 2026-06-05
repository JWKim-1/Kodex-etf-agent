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
            df = stock.get_etf_trading_volume_and_value(start_str, end_str, code)
            if df is None or df.empty:
                continue

            # 거래대금 순매수 추출
            try:
                col_순매수 = ('거래대금', '순매수')
                if col_순매수 not in df.columns:
                    continue
                순매수 = df[col_순매수]

                # 투자자 유형별 매핑 — DiD 분석에 필요한 컬럼만 저장
                row = {"단축코드": f"{code}*001", "종목명": ""}
                investor_map = {
                    "금융투자": "금융투자",  # 증권사 채널
                    "은행": "은행",          # 은행 채널
                    "개인": "개인",          # 개인 (LP 노이즈 감지용)
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

    # 이미 수집된 코드 제외
    existing_codes = set(df['단축코드'].str.replace('*001','').str.strip())

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

    # 기존 데이터와 병합
    combined = pd.concat([df, df_patch], ignore_index=True)
    combined = combined.drop_duplicates(subset=['단축코드'], keep='last')
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
