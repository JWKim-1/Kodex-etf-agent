"""
금욜 저녁 자동 마케팅 채널 수집 + 히스토리 저장
- 증권사 채널 (collect_all)
- ETF 운용사 채널 (collect_all_competitor / mass 동일)
- LLM 마케팅 이벤트 추출
- marketing_history.json 누적 저장
"""

import os
import sys
import json
import re
import logging
from datetime import datetime, timedelta, date

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
load_dotenv()

import anthropic as ant
from collector import DataCollector
import importlib.util as _ilu
import pathlib as _pl

def _load_bank_collector():
    p = _pl.Path(_ROOT) / "agents" / "bank" / "collector.py"
    spec = _ilu.spec_from_file_location("bank_collector", p)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.BankChannelCollector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(_ROOT, "scheduled_collect.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

HISTORY_FILE = os.path.join(_ROOT, "marketing_history.json")


# ── 히스토리 로드/저장 ────────────────────────────────────────────────────────

def load_history() -> dict:
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"히스토리 로드 실패: {e}")
        return {}


def save_history(history: dict):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    logger.info(f"히스토리 저장: {HISTORY_FILE} ({len(history)}주차)")


# ── LLM 마케팅 이벤트 추출 ────────────────────────────────────────────────────

def extract_events(collection_results: dict, api_key: str, mode: str) -> dict:
    """채널 수집 결과에서 LLM으로 마케팅 이벤트 구조화."""
    texts = []
    for r in collection_results.values():
        ok = getattr(r, "success", None)
        if ok is None:
            ok = getattr(r, "detected", False)
        if not ok or not r.data:
            continue
        d = r.data
        label = f"[{r.channel_name}]"
        if "raw_text" in d:
            texts.append(f"{label}\n{d['raw_text'][:600]}")
        elif "videos" in d:
            lines = [f"- {v['title']}" for v in d["videos"][:5]]
            if lines:
                texts.append(f"{label}\n" + "\n".join(lines))
        elif "event_details" in d:
            lines = [f"- {e['title']}" for e in d["event_details"][:5]]
            if lines:
                texts.append(f"{label}\n" + "\n".join(lines))
        elif "posts" in d:
            lines = [f"- {p['title']}" for p in d["posts"][:5]]
            if lines:
                texts.append(f"{label}\n" + "\n".join(lines))
        elif "articles" in d:
            lines = [f"- {a['title']}" for a in d["articles"][:8]]
            if lines:
                texts.append(f"{label}\n" + "\n".join(lines))

    if not texts:
        return {"marketing_detected": False, "events": [], "summary": "수집된 텍스트 없음"}

    mode_map = {
        "securities": "증권사(삼성/미래에셋/키움/토스/한투/신한/KB증권)",
        "bank":       "은행(KB/신한/하나/우리/NH농협은행)",
        "mass":       "ETF 운용사 전체(KODEX/TIGER/ACE/RISE/HANARO/SOL) — 개인 투자자 대상 마케팅 (운용사 구분 없이 모든 ETF 이벤트 포함)",
        "competitor": "ETF 운용사 전체(KODEX/TIGER/ACE/RISE/HANARO/SOL) — KODEX 경쟁사 비교 관점",
    }
    mode_desc = mode_map.get(mode, mode)

    focus_map = {
        "securities": "증권사가 KODEX ETF를 어떻게 마케팅하는지 — 추천/프로모션/수수료혜택 감지",
        "bank":       "은행이 ETF 매수를 유도하는 이벤트/혜택 감지 — KODEX 관련 우선",
        "mass":       "ETF 운용사(KODEX/TIGER/ACE/RISE/HANARO/SOL) 전체가 개인 투자자를 대상으로 진행한 마케팅 감지 — 운용사 구분 없이 개인 투자자에게 ETF 매수를 유도하는 모든 이벤트·프로모션·혜택 포함",
        "competitor": "KODEX 경쟁사(TIGER/ACE/RISE/HANARO/SOL) 마케팅 vs KODEX 활동 비교 — 경쟁사가 집중 마케팅하는 상품과 KODEX 대응 현황",
    }
    focus_desc = focus_map.get(mode, "")

    prompt = f"""다음은 {mode_desc} 마케팅 채널에서 수집된 텍스트입니다.

{chr(10).join(texts)}

[분석 관점]
{focus_desc}

[분석 기준]
- 마케팅 이벤트, 프로모션, ETF 매수 유도, 수수료 혜택, 신규상장 프로모션 감지
- 시황 분석·교육 콘텐츠는 제외
- 이벤트 기간, 대상 ETF, 핵심 혜택 조건 추출

JSON만 출력:
{{
  "marketing_detected": true/false,
  "summary": "전체 마케팅 활동 요약 2-3문장",
  "events": [
    {{
      "channel": "채널명",
      "provider": "회사/브랜드명",
      "title": "이벤트 제목",
      "url": "링크 (없으면 null)",
      "marketing_type": "이벤트|프로모션|추천콘텐츠|수수료혜택|신규상장|기타",
      "event_period": "YYYY-MM-DD ~ YYYY-MM-DD (없으면 null)",
      "event_summary": "혜택/조건 핵심 1-2문장",
      "target_etf": "대상 ETF명 (없으면 null)"
    }}
  ]
}}"""

    try:
        from llm_client import call_llm
        text = call_llm(prompt, anthropic_key=api_key, gemini_key=os.getenv("GEMINI_API_KEY",""), max_tokens=3000)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        logger.warning(f"LLM 분석 실패 ({mode}): {e}")

    return {"marketing_detected": False, "events": [], "summary": "LLM 분석 실패"}


# ── 채널 수집 요약 통계 ───────────────────────────────────────────────────────

def collection_summary(results: dict) -> dict:
    ok, fail = [], []
    for r in results.values():
        name = getattr(r, "channel_name", "")
        err  = getattr(r, "error", None) or getattr(r, "error_label", None) or ""

        # 메인 collector: success 필드로 판단
        # 은행 collector: detected는 "마케팅 감지 여부"이지 수집 성공이 아님
        #   → 데이터가 있으면 수집 성공, 에러가 있으면 실패
        if hasattr(r, "success"):
            is_ok = r.success
        else:
            # 은행: 에러가 없고 data가 있으면 수집 성공
            data = getattr(r, "data", None) or {}
            has_data = bool(data)
            is_ok = has_data and not err

        if is_ok:
            ok.append(name)
        else:
            fail.append({"name": name, "error": err})
    return {"success": ok, "failed": fail, "ok_count": len(ok), "fail_count": len(fail)}


# ── 메인 수집 루틴 ────────────────────────────────────────────────────────────

def run():
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=4)
    week_label = f"{monday.month}.{monday.day}-{friday.month}.{friday.day}"

    week_start_dt = datetime(monday.year, monday.month, monday.day)
    week_end_dt   = datetime(friday.year, friday.month, friday.day, 23, 59)

    logger.info(f"=== 자동 수집 시작: {week_label} ===")

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    naver_id  = os.getenv("NAVER_CLIENT_ID", "")
    naver_sec = os.getenv("NAVER_CLIENT_SECRET", "")

    collector = DataCollector(
        youtube_api_key=os.getenv("YOUTUBE_API_KEY", ""),
        naver_client_id=naver_id,
        naver_client_secret=naver_sec,
        anthropic_api_key=api_key,
        week_start=week_start_dt,
        week_end=week_end_dt,
    )

    history = load_history()
    entry = history.setdefault(week_label, {
        "week":       week_label,
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "securities": None,   # 증권 세션
        "bank":       None,   # 은행 세션
        "mass":       None,   # 개인 세션
        "competitor": None,   # 경쟁사 세션
    })
    entry["collected_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _llm_analyze(results, mode):
        """API 키 있을 때만 LLM 분석, 없으면 수집 원본만 반환."""
        if api_key:
            return extract_events(results, api_key, mode)
        return {"marketing_detected": None, "events": [], "summary": "LLM 분석 미실행 (API 키 없음)"}

    def _raw_data(results):
        """채널별 수집 원본 텍스트 저장 (LLM 없이도 히스토리 보존)."""
        raw = {}
        for k, r in results.items():
            d = getattr(r, "data", {}) or {}
            raw[k] = {
                "channel_name": getattr(r, "channel_name", k),
                "success": getattr(r, "success", getattr(r, "detected", False)),
                "snippet": (
                    d.get("raw_text", "")[:300] or
                    " / ".join(v.get("title","") for v in d.get("videos",[])[:3]) or
                    " / ".join(a.get("title","") for a in d.get("articles",[])[:3]) or
                    " / ".join(e.get("title","") for e in d.get("event_details",[])[:3]) or ""
                )
            }
        return raw

    # ── 1. 증권사 채널 수집 (증권 세션) ──────────────────────────────────────
    logger.info("증권사 채널 수집 중...")
    try:
        sec_results = collector.collect_all()
        sec_summary = collection_summary(sec_results)
        logger.info(f"  증권사: 성공 {sec_summary['ok_count']}개 / 실패 {sec_summary['fail_count']}개")
        entry["securities"] = {
            "collection": sec_summary,
            "raw": _raw_data(sec_results),
            "events": _llm_analyze(sec_results, "securities"),
        }
    except Exception as e:
        logger.error(f"증권사 수집 실패: {e}")
        entry["securities"] = {"error": str(e)}

    bank_results = None
    bank_summary = {"success": [], "failed": [], "ok_count": 0, "fail_count": 0}
    # ── 2. 은행 채널 수집 (은행 세션) ────────────────────────────────────────
    logger.info("은행 채널 수집 중...")
    try:
        BankChannelCollector = _load_bank_collector()
        bank_collector = BankChannelCollector(week_start=week_start_dt, week_end=week_end_dt,
                                               youtube_api_key=os.getenv("YOUTUBE_API_KEY", ""))
        bank_results = bank_collector.collect_all()
        bank_summary = collection_summary(bank_results)
        logger.info(f"  은행: 성공 {bank_summary['ok_count']}개 / 실패 {bank_summary['fail_count']}개")
        entry["bank"] = {
            "collection": bank_summary,
            "raw": _raw_data(bank_results),
            "events": _llm_analyze(bank_results, "bank"),
        }
    except Exception as e:
        logger.error(f"은행 수집 실패: {e}")
        entry["bank"] = {"error": str(e)}

    # ── 3. ETF 운용사 채널 수집 (개인+경쟁사 공용 수집, LLM 분석은 관점별 독립) ─
    logger.info("ETF 운용사 채널 수집 중 (개인+경쟁사)...")
    etf_results = None
    etf_summary = {"success": [], "failed": [], "ok_count": 0, "fail_count": 0}
    try:
        etf_results = collector.collect_all_competitor()
        etf_summary = collection_summary(etf_results)
        logger.info(f"  ETF AM: 성공 {etf_summary['ok_count']}개 / 실패 {etf_summary['fail_count']}개")
    except Exception as e:
        logger.error(f"ETF AM 수집 실패: {e}")

    etf_raw = _raw_data(etf_results) if etf_results else {}

    entry["mass"] = {
        "collection": etf_summary,
        "raw": etf_raw,
        "events": _llm_analyze(etf_results, "mass") if etf_results else {"marketing_detected": False, "events": [], "summary": "수집 실패"},
    }
    entry["competitor"] = {
        "collection": etf_summary,
        "raw": etf_raw,
        "events": _llm_analyze(etf_results, "competitor") if etf_results else {"marketing_detected": False, "events": [], "summary": "수집 실패"},
    }

    save_history(history)

    # ── channel_archive.json 에도 저장 → 앱 자동 로드 지원 ───────────────────
    try:
        from channel_archive import save_channel_results, save_raw_data
        if sec_results:
            save_channel_results(week_label, sec_results)
        if bank_results:
            save_channel_results(f"bank_{week_label}", bank_results)
            if api_key and entry.get("bank", {}).get("events"):
                save_raw_data(f"bank_llm_{week_label}", entry["bank"]["events"])
        if etf_results:
            save_channel_results(f"mass_{week_label}", etf_results)
            save_channel_results(f"competitor_{week_label}", etf_results)
        logger.info("channel_archive 저장 완료")
    except Exception as e:
        logger.warning(f"channel_archive 저장 실패 (무시): {e}")

    logger.info(
        f"=== 완료: {week_label} | "
        f"증권 {sec_summary.get('ok_count',0)}ch / 은행 {bank_summary.get('ok_count',0) if isinstance(bank_summary,dict) else 0}ch / "
        f"ETF AM {etf_summary.get('ok_count',0)}ch"
        + (" | LLM 분석 완료" if api_key else " | LLM 분석 건너뜀 (API 키 없음)") + " ==="
    )


if __name__ == "__main__":
    run()
