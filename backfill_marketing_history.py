"""
과거 주차 마케팅 채널 소급 수집 스크립트
- KRX 데이터가 있는 모든 주차 대상
- 유튜브/블로그/뉴스는 날짜 필터로 과거 소급 가능
- 이벤트 페이지는 현재 진행 중인 것만 (skip)
- 이미 수집된 주차는 건너뜀
- 채널별 독립 실행: --channel securities|bank|mass|all
"""

import os, sys, json, argparse, logging, time
from datetime import datetime, timedelta, date

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
load_dotenv()

from collector import DataCollector
from scheduled_collect import (
    load_history, save_history, extract_events, collection_summary,
    _load_bank_collector, HISTORY_FILE
)
from krx_data_fetcher import load_cache

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(_ROOT, "backfill.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def week_label(mon: date, fri: date) -> str:
    return f"{mon.month}.{mon.day}-{fri.month}.{fri.day}"


def get_krx_weeks() -> list:
    """KRX 캐시에 있는 모든 주차 → (monday, friday, label) 목록."""
    cache = load_cache()
    weeks = []
    for label in sorted(cache.keys()):
        # 레이블 파싱: "9.22-9.26" → date
        try:
            parts = label.split("-")
            m1, d1 = parts[0].split(".")
            m2, d2 = parts[1].split(".")
            # 연도 추정: 월이 9~12면 2025, 1~8면 2026
            y1 = 2025 if int(m1) >= 9 else 2026
            y2 = 2025 if int(m2) >= 9 else 2026
            mon = date(y1, int(m1), int(d1))
            fri = date(y2, int(m2), int(d2))
            weeks.append((mon, fri, label))
        except Exception as e:
            logger.warning(f"주차 레이블 파싱 실패: {label} - {e}")
    return weeks


def _raw_data(results: dict) -> dict:
    raw = {}
    for k, r in results.items():
        d = getattr(r, "data", {}) or {}
        videos = d.get("videos", [])
        raw[k] = {
            "channel_name": getattr(r, "channel_name", k),
            "success": getattr(r, "success", getattr(r, "detected", False)),
            "snippet": (
                d.get("raw_text", "")[:300] or
                " / ".join(v.get("title", "") for v in videos[:3]) or
                " / ".join(a.get("title", "") for a in d.get("articles", [])[:3]) or ""
            ),
            "videos": [
                {
                    "title": v.get("title", ""),
                    "url": v.get("url", ""),
                    "thumbnail": v.get("thumbnail", ""),
                    "published_at": v.get("published_at", ""),
                    "is_etf_related": v.get("is_etf_related", False),
                }
                for v in videos[:10]
            ] if videos else [],
        }
    return raw


def _llm_analyze(collector_instance, results, mode, api_key):
    if not api_key or not results:
        return {"marketing_detected": None, "events": [], "summary": "LLM 분석 미실행"}
    try:
        return extract_events(results, api_key, mode)
    except Exception as e:
        logger.warning(f"LLM 분석 실패 ({mode}): {e}")
        return {"marketing_detected": None, "events": [], "summary": f"LLM 오류: {e}"}


def backfill(channels: list, dry_run: bool = False, force: bool = False):
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    youtube_key = os.getenv("YOUTUBE_API_KEY", "")
    naver_id = os.getenv("NAVER_CLIENT_ID", "")
    naver_sec = os.getenv("NAVER_CLIENT_SECRET", "")

    if not youtube_key and not naver_id:
        logger.warning("YOUTUBE_API_KEY, NAVER_CLIENT_ID 미설정 — 수집 결과가 제한됩니다")

    krx_weeks = get_krx_weeks()
    logger.info(f"KRX 캐시 주차 수: {len(krx_weeks)}")

    history = load_history()
    done = set(history.keys())

    to_process = []
    for mon, fri, label in krx_weeks:
        entry = history.get(label, {})
        needs = False
        for ch in channels:
            if force or entry.get(ch) is None:
                needs = True
        if needs:
            to_process.append((mon, fri, label))

    logger.info(f"소급 수집 대상: {len(to_process)}주 / 전체 {len(krx_weeks)}주 (채널: {channels})")

    if dry_run:
        for mon, fri, label in to_process:
            print(f"  [DRY-RUN] {label}")
        return

    for i, (mon, fri, label) in enumerate(to_process, 1):
        logger.info(f"[{i}/{len(to_process)}] {label} 수집 시작...")

        week_start_dt = datetime(mon.year, mon.month, mon.day)
        week_end_dt   = datetime(fri.year, fri.month, fri.day, 23, 59)

        entry = history.setdefault(label, {
            "week": label,
            "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "backfilled": True,
            "securities": None, "bank": None, "mass": None, "competitor": None,
        })
        entry["backfilled"] = True

        collector = DataCollector(
            youtube_api_key=youtube_key,
            naver_client_id=naver_id,
            naver_client_secret=naver_sec,
            anthropic_api_key=api_key,
            week_start=week_start_dt,
            week_end=week_end_dt,
        )

        # ── 증권사 채널 ──
        if "securities" in channels and (force or entry.get("securities") is None):
            try:
                logger.info(f"  [{label}] 증권사 채널 수집...")
                results = collector.collect_all()
                summary = collection_summary(results)
                entry["securities"] = {
                    "collection": summary,
                    "raw": _raw_data(results),
                    "events": _llm_analyze(collector, results, "securities", api_key),
                }
                logger.info(f"  [{label}] 증권사 OK: {summary['ok_count']}채널")
            except Exception as e:
                logger.error(f"  [{label}] 증권사 실패: {e}")
                entry["securities"] = {"error": str(e)}

        # ── 은행 채널 ──
        if "bank" in channels and (force or entry.get("bank") is None):
            try:
                logger.info(f"  [{label}] 은행 채널 수집...")
                BankColl = _load_bank_collector()
                bank_coll = BankColl(
                    week_start=week_start_dt, week_end=week_end_dt,
                    youtube_api_key=youtube_key,
                )
                bank_results = bank_coll.collect_all()
                bank_summary = collection_summary(bank_results)
                entry["bank"] = {
                    "collection": bank_summary,
                    "raw": _raw_data(bank_results),
                    "events": _llm_analyze(bank_coll, bank_results, "bank", api_key),
                }
                logger.info(f"  [{label}] 은행 OK: {bank_summary['ok_count']}채널")
            except Exception as e:
                logger.error(f"  [{label}] 은행 실패: {e}")
                entry["bank"] = {"error": str(e)}

        # ── 개인(매스) / 경쟁사 채널 ──
        if ("mass" in channels or "competitor" in channels) and (
            force or entry.get("mass") is None or entry.get("competitor") is None
        ):
            try:
                logger.info(f"  [{label}] ETF AM 채널 수집...")
                etf_results = collector.collect_all_competitor()
                etf_summary = collection_summary(etf_results)
                etf_raw = _raw_data(etf_results)

                if "mass" in channels and (force or entry.get("mass") is None):
                    entry["mass"] = {
                        "collection": etf_summary,
                        "raw": etf_raw,
                        "events": _llm_analyze(collector, etf_results, "mass", api_key),
                    }

                if "competitor" in channels and (force or entry.get("competitor") is None):
                    entry["competitor"] = {
                        "collection": etf_summary,
                        "raw": etf_raw,
                        "events": _llm_analyze(collector, etf_results, "competitor", api_key),
                    }
                logger.info(f"  [{label}] ETF AM OK: {etf_summary['ok_count']}채널")
            except Exception as e:
                logger.error(f"  [{label}] ETF AM 실패: {e}")
                for ch in ("mass", "competitor"):
                    if ch in channels:
                        entry[ch] = {"error": str(e)}

        save_history(history)
        logger.info(f"  [{label}] 저장 완료")

        # 과부하 방지
        if i < len(to_process):
            time.sleep(2)

    logger.info(f"=== 소급 수집 완료: {len(to_process)}주 처리 ===")
    logger.info("marketing_backtest.py를 실행해서 백테스트 결과를 갱신하세요.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="과거 마케팅 채널 소급 수집")
    parser.add_argument(
        "--channel", default="all",
        choices=["all", "securities", "bank", "mass", "competitor"],
        help="수집할 채널 (default: all)"
    )
    parser.add_argument("--dry-run", action="store_true", help="실제 수집 없이 대상 주차만 출력")
    parser.add_argument("--force", action="store_true", help="이미 수집된 주차도 재수집")
    args = parser.parse_args()

    channels = ["securities", "bank", "mass", "competitor"] if args.channel == "all" else [args.channel]
    backfill(channels=channels, dry_run=args.dry_run, force=args.force)
