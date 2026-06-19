"""
매주 금요일 전체 파이프라인
1. KRX 데이터 수집 (현재 주차)
2. 3채널 DiD 분석 (securities/bank/mass) → did_history / bank_zscore_history 저장
3. 마케팅 채널 수집 + LLM 이벤트 추출 → marketing_history 저장
4. 마케팅 백테스트 갱신

실행: python weekly_pipeline.py
"""
import os, sys, logging
from datetime import date, datetime, timedelta

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "agents", "bank"))
sys.path.insert(0, os.path.join(_ROOT, "agents", "securities"))
sys.path.insert(0, os.path.join(_ROOT, "agents", "mass"))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(_ROOT, "weekly_pipeline.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ── 현재 주차 계산 ───────────────────────────────────────────────────────────
today = date.today()
monday = today - timedelta(days=today.weekday())
friday = monday + timedelta(days=4)
WEEK_LABEL = f"{monday.month}.{monday.day}-{friday.month}.{friday.day}"
WEEK_START  = datetime(monday.year, monday.month, monday.day)
WEEK_END    = datetime(friday.year, friday.month, friday.day, 23, 59)

logger.info(f"{'='*60}")
logger.info(f"주간 파이프라인 시작: {WEEK_LABEL}")
logger.info(f"{'='*60}")


# ── Step 1: KRX 수집 ─────────────────────────────────────────────────────────
def step1_krx():
    logger.info("[Step 1] KRX 데이터 수집...")
    from krx_data_fetcher import fetch_weekly_etf_data, load_cache, save_cache
    cache = load_cache()
    if WEEK_LABEL in cache:
        logger.info(f"  KRX 이미 수집됨: {WEEK_LABEL} ({len(cache[WEEK_LABEL])} rows) — skip")
        return True
    result = fetch_weekly_etf_data(monday, friday)
    if result is not None and not result.empty:
        cache[WEEK_LABEL] = result
        save_cache(cache)
        logger.info(f"  KRX 수집 완료: {len(result)} rows")
        return True
    logger.error("  KRX 수집 실패")
    return False


# ── Step 2: DiD 분석 3채널 ────────────────────────────────────────────────────
def step2_did():
    logger.info("[Step 2] DiD 분석 (securities / mass / bank)...")
    from krx_data_fetcher import load_cache_recent
    import pandas as pd

    all_sheets = load_cache_recent(25)
    if not all_sheets or WEEK_LABEL not in all_sheets:
        logger.error("  KRX 캐시에 현재 주차 없음 — DiD 분석 불가")
        return

    sheet_names = list(all_sheets.keys())
    current_df  = all_sheets[WEEK_LABEL]
    _code_col   = "단축코드" if "단축코드" in current_df.columns else "종목코드"
    all_codes   = current_df[_code_col].dropna().astype(str).tolist()

    # KODEX 코드만 필터
    from etf_mapping_loader import get_competitors
    try:
        import json as _json
        mapping = _json.load(open(os.path.join(_ROOT, "etf_mapping.json"), encoding="utf-8"))
        kodex_codes = [c.split("*")[0] for c in mapping.keys()]
    except Exception:
        kodex_codes = all_codes[:100]

    from did_history import save_results as _save_did

    # ── securities ──
    try:
        import importlib.util as _ilu
        spec = _ilu.spec_from_file_location(
            "sec_analyzer",
            os.path.join(_ROOT, "agents", "securities", "analyzer.py")
        )
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sec_analyzer = mod.MarketingAnalyzer()
        sec_results = sec_analyzer.analyze(all_sheets, kodex_codes, WEEK_LABEL)
        _save_did(WEEK_LABEL, [
            {"code": c, "name": r.kodex_name, "did": r.raw_did_value if r.raw_did_value else r.did_value,
             "judgement": r.judgement, "marketing_detected": True, "no_competitors": r.no_competitors}
            for c, r in sec_results.items()
        ], channel_type="securities")
        logger.info(f"  securities DiD 저장: {len(sec_results)}개 ETF")
    except Exception as e:
        logger.error(f"  securities DiD 실패: {e}")

    # ── mass ──
    try:
        spec = _ilu.spec_from_file_location(
            "mass_analyzer",
            os.path.join(_ROOT, "agents", "mass", "analyzer.py")
        )
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mass_analyzer = mod.MassAnalyzer()
        mass_results = mass_analyzer.analyze(all_sheets, kodex_codes, WEEK_LABEL)
        _save_did(WEEK_LABEL, [
            {"code": c, "name": r.kodex_name, "did": r.raw_did_value if r.raw_did_value else r.did_value,
             "judgement": r.judgement, "marketing_detected": True, "no_competitors": r.no_competitors}
            for c, r in mass_results.items()
        ], channel_type="mass")
        logger.info(f"  mass DiD 저장: {len(mass_results)}개 ETF")
    except Exception as e:
        logger.error(f"  mass DiD 실패: {e}")

    # ── bank ──
    try:
        import pickle as _pickle
        _pkl_dir  = os.path.join(_ROOT, ".did_cache")
        os.makedirs(_pkl_dir, exist_ok=True)
        _pkl_name = f"bank_{WEEK_LABEL.replace('.','_').replace('-','_')}.pkl"
        _pkl_path = os.path.join(_pkl_dir, _pkl_name)

        bank_dir = os.path.join(_ROOT, "agents", "bank")
        if bank_dir not in sys.path:
            sys.path.insert(0, bank_dir)
        import analyzer as _bank_mod
        bank_analyzer = _bank_mod.MarketingAnalyzer()
        bank_results = bank_analyzer.analyze(all_sheets, kodex_codes, WEEK_LABEL)

        with open(_pkl_path, "wb") as pf:
            _pickle.dump(bank_results, pf)
        logger.info(f"  bank DiD pkl 저장: {_pkl_name} ({len(bank_results)}개 ETF)")

        from did_history import save_results as _save_bank
        _save_bank(WEEK_LABEL, [
            {"code": c, "name": r.kodex_name,
             "did": getattr(r, "zscore", r.did_value),
             "judgement": r.judgement, "marketing_detected": True, "no_competitors": r.no_competitors}
            for c, r in bank_results.items()
        ], channel_type="bank")
        logger.info(f"  bank Z-score 히스토리 저장 완료")
    except Exception as e:
        logger.error(f"  bank DiD 실패: {e}")


# ── Step 3: 마케팅 채널 수집 ─────────────────────────────────────────────────
def step3_marketing():
    logger.info("[Step 3] 마케팅 채널 수집 (scheduled_collect.run)...")
    try:
        from scheduled_collect import run as _run_collect
        _run_collect()
        logger.info("  마케팅 채널 수집 완료")
    except Exception as e:
        logger.error(f"  마케팅 채널 수집 실패: {e}")


# ── Step 4: 백테스트 갱신 ─────────────────────────────────────────────────────
def step4_backtest():
    logger.info("[Step 4] 마케팅 백테스트 갱신...")
    try:
        import subprocess
        r = subprocess.run(
            [sys.executable, os.path.join(_ROOT, "marketing_backtest.py")],
            capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if r.returncode == 0:
            logger.info("  백테스트 갱신 완료")
        else:
            logger.warning(f"  백테스트 경고: {r.stderr[:200]}")
    except Exception as e:
        logger.error(f"  백테스트 실패: {e}")


# ── 실행 ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ok = step1_krx()
    if ok:
        step2_did()
    step3_marketing()
    step4_backtest()
    logger.info(f"{'='*60}")
    logger.info(f"파이프라인 완료: {WEEK_LABEL}")
    logger.info(f"{'='*60}")
