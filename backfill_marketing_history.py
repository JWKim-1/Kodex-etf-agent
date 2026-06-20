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


_KW_EVENT   = ["이벤트", "경품", "추첨", "당첨", "기념", "선착순", "한정"]
_KW_PROMO   = ["수수료", "무료", "할인", "혜택", "특별", "프로모션", "캐시백", "포인트"]
_KW_CONTENT = ["etf", "kodex", "투자", "펀드", "운용", "포트폴리오", "자산배분", "리밸런싱"]
_KW_EDU     = ["교육", "웨비나", "세미나", "강의", "설명회", "가이드", "튜토리얼"]
_KW_ALL     = _KW_EVENT + _KW_PROMO + _KW_CONTENT + _KW_EDU


def _keyword_analyze(results: dict, mode: str) -> dict:
    """
    1단계: 키워드로 후보 채널 필터링
    2단계: 후보가 있을 때만 LLM에 요약 텍스트 전달 → 이벤트 추출
    키워드 미감지 채널은 LLM 완전 스킵
    """
    from llm_client import call_llm
    import json as _json
    api_key = os.getenv("ANTHROPIC_API_KEY", "")

    candidates = []  # (ch_key, channel_name, snippet, url)

    for ch_key, r in results.items():
        d = getattr(r, "data", {}) or {}
        videos   = d.get("videos", [])
        articles = d.get("articles", [])
        raw_text = d.get("raw_text", "")

        texts = [raw_text]
        texts += [v.get("title", "") + " " + v.get("description", "") for v in videos]
        texts += [a.get("title", "") + " " + a.get("description", "") for a in articles]
        combined = " ".join(t for t in texts if t).lower()

        if not combined.strip():
            continue

        # 키워드 1차 필터
        if not any(kw in combined for kw in _KW_ALL):
            continue

        titles = [v.get("title") for v in videos if v.get("title")]
        titles += [a.get("title") for a in articles if a.get("title")]
        snippet = " / ".join(titles[:5]) or raw_text[:200]
        url = (videos[0].get("url") if videos else "") or ""
        candidates.append((ch_key, getattr(r, "channel_name", ch_key), snippet, url))

    if not candidates:
        return {"marketing_detected": False, "events": [], "summary": "키워드 미감지 — LLM 스킵"}

    # 키워드 걸린 채널만 LLM에 전달
    if not api_key:
        # API 키 없으면 키워드 결과 그대로 반환
        events = [{
            "marketing_type": "추천콘텐츠",
            "title": snippet[:60],
            "channel": ch_name,
            "event_period": "",
            "target_etf": "KODEX",
            "event_summary": "키워드 감지",
            "url": url,
        } for _, ch_name, snippet, url in candidates]
        return {"marketing_detected": True, "events": events, "summary": f"키워드 감지 {len(events)}건 (LLM 미실행)"}

    ch_lines = "\n".join(
        f"[{ch_name}] {snippet[:300]}" for _, ch_name, snippet, _ in candidates
    )
    prompt = f"""다음은 ETF 마케팅 채널에서 키워드로 1차 필터링된 콘텐츠 목록입니다.
각 항목이 실제 KODEX ETF 마케팅 활동(이벤트/프로모션/수수료혜택/추천콘텐츠)인지 판단하고,
해당하는 것만 JSON 배열로 반환하세요. 마케팅이 아닌 일반 시황·뉴스는 제외합니다.

채널 목록:
{ch_lines}

반드시 다음 형식의 JSON 배열만 반환 (설명 없이):
[{{"marketing_type":"이벤트|프로모션|수수료혜택|추천콘텐츠","title":"...","channel":"...","event_period":"","target_etf":"","event_summary":"..."}}]
마케팅 활동이 없으면 빈 배열 [] 반환."""

    try:
        raw = call_llm(prompt, anthropic_key=api_key, max_tokens=1500)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        events = _json.loads(raw)
        if not isinstance(events, list):
            events = []
        # url 보완
        url_map = {ch_name: url for _, ch_name, _, url in candidates}
        for ev in events:
            if not ev.get("url"):
                ev["url"] = url_map.get(ev.get("channel", ""), "")
    except Exception as e:
        logger.warning(f"LLM 파싱 실패 ({mode}): {e} — 키워드 결과로 폴백")
        events = [{
            "marketing_type": "추천콘텐츠",
            "title": snippet[:60],
            "channel": ch_name,
            "event_period": "",
            "target_etf": "KODEX",
            "event_summary": "키워드 감지 (LLM 파싱 실패)",
            "url": url,
        } for _, ch_name, snippet, url in candidates]

    detected = bool(events)
    summary = f"LLM 확인: {len(events)}건 마케팅 감지" if detected else f"키워드 후보 {len(candidates)}건 → LLM 필터링 후 미감지"
    return {"marketing_detected": detected, "events": events, "summary": summary}


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
                    "events": _keyword_analyze(results, "securities"),
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
                    "events": _keyword_analyze(bank_results, "bank"),
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
                        "events": _keyword_analyze(etf_results, "mass"),
                    }

                if "competitor" in channels and (force or entry.get("competitor") is None):
                    entry["competitor"] = {
                        "collection": etf_summary,
                        "raw": etf_raw,
                        "events": _keyword_analyze(etf_results, "competitor"),
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
