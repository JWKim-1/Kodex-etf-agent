"""
주간 종합 리포트 — 6개 세션 데이터 통합 분석
- marketing_history.json : 증권/은행/개인/경쟁사 채널 이벤트
- krx_data_cache.parquet : 수급 트렌드 (투자자별 순매수)
- detect_listing_changes(): 신규상장/상폐 현황
→ LLM이 종합해 마케팅 담당자 액션 제안 생성
"""

import os, sys, json, re
from datetime import datetime, date, timedelta
from collections import defaultdict

import streamlit as st
import pandas as pd
import anthropic as ant

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scheduled_collect import load_history, HISTORY_FILE
from krx_data_fetcher import load_cache, _parse_week_label, detect_listing_changes

_REPORT_CACHE_FILE = os.path.join(_ROOT, "report_cache.json")

def _load_report_cache() -> dict:
    if not os.path.exists(_REPORT_CACHE_FILE):
        return {}
    try:
        with open(_REPORT_CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_report_cache(week: str, report_md: str):
    cache = _load_report_cache()
    cache[week] = report_md
    with open(_REPORT_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

# ── 유틸 ─────────────────────────────────────────────────────────────────────

def _sorted_weeks(d: dict) -> list:
    return sorted(d.keys(), key=lambda w: _parse_week_label(w) or date.min)


def _latest_week(cache: dict) -> str:
    return _sorted_weeks(cache)[-1]


def _krx_summary(cache: dict, week: str) -> str:
    """해당 주차 KRX 수급 상위 5종목 요약 텍스트."""
    df = cache.get(week)
    if df is None or df.empty:
        return "KRX 데이터 없음"
    rows = []
    for col in ["금융투자", "은행", "개인"]:
        if col not in df.columns:
            continue
        top = df.nlargest(3, col)[["종목명", col]].values.tolist()
        for name, val in top:
            rows.append(f"  [{col}] {name}: {int(val):,}천원 순매수")
    return "\n".join(rows) if rows else "수급 데이터 없음"


def _closest_history_week(history: dict, week: str) -> str:
    """KRX 주차와 날짜가 가장 가까운 히스토리 주차 반환."""
    if week in history:
        return week
    target = _parse_week_label(week)
    if not target or not history:
        return week
    best, best_diff = week, 999
    for hw in history:
        hw_date = _parse_week_label(hw)
        if hw_date:
            diff = abs((hw_date - target).days)
            if diff < best_diff:
                best, best_diff = hw, diff
    return best if best_diff <= 7 else week


def _history_summary(history: dict, week: str) -> str:
    """해당 주차 4개 세션 마케팅 이벤트 요약 텍스트."""
    matched = _closest_history_week(history, week)
    entry = history.get(matched, {})
    parts = []
    session_labels = {
        "securities": "증권사 채널",
        "bank":       "은행 채널",
        "mass":       "개인(ETF 운용사) 채널",
        "competitor": "경쟁사 채널",
    }
    for key, label in session_labels.items():
        sess = entry.get(key) or {}
        events = (sess.get("events") or {}).get("events") or []
        summary = (sess.get("events") or {}).get("summary") or ""
        if not events and not summary:
            parts.append(f"[{label}] 이벤트 없음")
            continue
        parts.append(f"[{label}] {summary}")
        for ev in events[:4]:
            title  = ev.get("title", "")
            period = ev.get("event_period") or ""
            etf    = ev.get("target_etf") or ""
            mtype  = ev.get("marketing_type", "")
            parts.append(f"  · {title}" + (f" ({period})" if period else "") +
                         (f" — {etf}" if etf else "") + (f" [{mtype}]" if mtype else ""))
    return "\n".join(parts) if parts else "수집 데이터 없음"


def _listing_summary(cache: dict, selected_week: str = None) -> str:
    result = detect_listing_changes(cache)
    cutoff = date.today() - timedelta(days=28)

    def _week_date(x):
        return _parse_week_label(x.get("week", "")) or date.min

    new_all = [x for x in result["new_listings"] if x["status"] == "confirmed"]
    new_recent = [x for x in new_all if _week_date(x) >= cutoff]
    new_older  = [x for x in new_all if _week_date(x) < cutoff]

    delist = [x for x in result["delistings"] if x["reason"] in ("delisting_confirmed", "delisting_pending", "maturity_redemption")]
    lines = []
    if new_recent:
        lines.append(f"신규상장 확정 {len(new_recent)}건 (최근 4주): " + ", ".join(f"{x['종목명']}({x['week']})" for x in new_recent))
    if new_older:
        lines.append(f"  ↳ 그 외 이전 상장 {len(new_older)}건 (생략)")
    if delist:
        lines.append(f"상폐/만기 {len(delist)}건: " + ", ".join(f"{x['종목명']}[{x['reason']}]" for x in delist[-5:]))
    return "\n".join(lines) if lines else "신규상장/상폐 이슈 없음"


# ── LLM 리포트 생성 ───────────────────────────────────────────────────────────

def generate_report_no_llm(week: str, krx_text: str, history_text: str,
                           listing_text: str) -> str:
    """API 키 없을 때 데이터 그대로 마크다운 리포트로 포매팅."""
    return f"""## 이번 주 핵심 요약
> 📌 API 키 없음 — 수집 데이터를 그대로 표시합니다 (LLM 인사이트 없음)

---

## 채널별 마케팅 활동

```
{history_text}
```

---

## 수급 시그널

```
{krx_text}
```

---

## 신규상장 / 상폐 현황

```
{listing_text}
```

---

## 다음 주 액션 제안

> Anthropic 또는 Gemini API 키를 입력하면 AI가 위 데이터를 분석해 액션 제안을 생성합니다.
"""


def generate_report(week: str, krx_text: str, history_text: str,
                    listing_text: str, api_key: str) -> str:
    prompt = f"""당신은 삼성자산운용 KODEX ETF 마케팅 전략 담당자의 AI 어시스턴트입니다.
아래는 {week} 주간 수집된 데이터입니다.

=== KRX 투자자별 수급 ===
{krx_text}

=== 마케팅 채널 이벤트 (증권/은행/개인/경쟁사) ===
{history_text}

=== 신규상장/상폐 현황 ===
{listing_text}

위 데이터를 종합해 마케팅 담당자를 위한 주간 리포트를 작성해주세요.

[리포트 형식 — 반드시 아래 섹션 순서로, 마크다운 사용]

## 이번 주 핵심 요약
(3줄 이내, 가장 중요한 시사점)

## 채널별 마케팅 활동
(증권/은행/개인/경쟁사 채널에서 감지된 주요 이벤트 정리)

## 수급 시그널
(투자자별 순매수 흐름에서 주목할 패턴)

## 경쟁사 동향
(경쟁 운용사 마케팅 활동 중 KODEX에 영향 줄 내용)

## 다음 주 액션 제안
(마케팅 담당자가 취해야 할 구체적 액션 3~5개, 우선순위 순)

간결하고 실무적으로 작성하세요. 데이터가 없는 섹션은 "감지 없음"으로 표기."""

    from llm_client import call_llm
    return call_llm(prompt, anthropic_key=api_key, gemini_key=os.getenv("GEMINI_API_KEY",""), max_tokens=2048)


# ── UI ───────────────────────────────────────────────────────────────────────

st.title("📋 주간 종합 리포트")
st.caption("6개 채널 데이터를 통합 분석해 마케팅 인사이트 & 액션 제안을 생성합니다")

with st.sidebar:
    st.header("⚙️ 설정")
    api_key = st.text_input("Anthropic API Key", value=os.getenv("ANTHROPIC_API_KEY",""), type="password", key="report_ant_key", help="Anthropic Claude 사용 시")
    gemini_key = st.text_input("Gemini API Key", value=os.getenv("GEMINI_API_KEY",""), type="password", key="report_gem_key", help="Google Gemini 무료 사용 시 (둘 중 하나만)")
    if not api_key and gemini_key:
        api_key = ""  # call_llm이 gemini_key 우선 처리

# 주차 선택
cache   = load_cache()
history = load_history()
all_weeks = _sorted_weeks(cache)

if not all_weeks:
    st.error("KRX 캐시 데이터가 없습니다.")
    st.stop()

selected_week = st.selectbox(
    "분석 주차",
    options=list(reversed(all_weeks)),
    index=0,
    key="report_week_select",
)

# 데이터 미리보기
with st.expander("📂 데이터 소스 미리보기", expanded=False):
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**KRX 수급**")
        st.text(_krx_summary(cache, selected_week))
    with col_b:
        st.markdown("**신규상장/상폐**")
        st.text(_listing_summary(cache, selected_week))

    st.markdown("**채널 이벤트**")
    hist_text = _history_summary(history, selected_week)
    if "이벤트 없음" in hist_text and "수집 데이터 없음" in hist_text:
        st.warning("해당 주차 채널 수집 데이터가 없습니다. 금요일 자동수집 후 이용하세요.")
    else:
        st.text(hist_text[:1200])

st.markdown("---")

_has_key = bool(api_key or gemini_key)

# 캐시에 이번 주차 리포트 있으면 자동 로드 (버튼 없이)
_report_cache = _load_report_cache()
_has_cached = selected_week in _report_cache

if _has_cached and "report_md" not in st.session_state:
    st.session_state["report_md"] = _report_cache[selected_week]
    st.session_state["report_week"] = selected_week
    st.caption(f"📦 저장된 리포트 자동 로드 ({selected_week})")

if _has_key:
    _btn_label = "🤖  AI 리포트 생성"
elif _has_cached:
    _btn_label = "📦  저장된 리포트 불러오기"
else:
    _btn_label = "📄  데이터 리포트 보기 (API 없음)"
    st.info("💡 API 키 없이도 수집 데이터 기반 리포트를 볼 수 있습니다. Anthropic/Gemini 키 입력 시 AI 인사이트가 추가됩니다.")

if st.button(_btn_label, type="primary", use_container_width=True, key="run_report"):
    # API 키 없고 캐시 있으면 → 캐시 로드만, LLM 폴백 금지
    if not _has_key and _has_cached:
        st.session_state["report_md"] = _report_cache[selected_week]
        st.session_state["report_week"] = selected_week
        st.rerun()

    with st.spinner("데이터 통합 중..." if not _has_key else "6개 채널 데이터 통합 분석 중..."):
        krx_text     = _krx_summary(cache, selected_week)
        history_text = _history_summary(history, selected_week)
        listing_text = _listing_summary(cache, selected_week)

        if not _has_key:
            report_md = generate_report_no_llm(
                week=selected_week,
                krx_text=krx_text,
                history_text=history_text,
                listing_text=listing_text,
            )
        else:
            import os as _os
            _os.environ["GEMINI_API_KEY"] = gemini_key
            report_md = generate_report(
                week=selected_week,
                krx_text=krx_text,
                history_text=history_text,
                listing_text=listing_text,
                api_key=api_key,
            )

    st.session_state["report_md"] = report_md
    st.session_state["report_week"] = selected_week
    _save_report_cache(selected_week, report_md)

if "report_md" in st.session_state:
    import markdown as _md_lib
    _report_week_label = st.session_state.get("report_week", selected_week)
    _report_md = st.session_state["report_md"]

    st.markdown(f"### {_report_week_label} 주간 종합 리포트")
    st.markdown(_report_md)

    _report_html_body = _md_lib.markdown(_report_md, extensions=["tables", "fenced_code"])
    _full_html = f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<title>KODEX 주간 리포트 {_report_week_label}</title>
<style>
  body {{ font-family: 'Pretendard', 'Noto Sans KR', sans-serif; max-width: 860px; margin: 40px auto; padding: 0 24px; color: #1a1a2e; line-height: 1.7; }}
  h1 {{ color: #0052ff; border-bottom: 2px solid #0052ff; padding-bottom: 8px; }}
  h2 {{ color: #1f6feb; margin-top: 32px; }}
  h3 {{ color: #3b82f6; }}
  table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
  th {{ background: #0052ff; color: white; padding: 8px 12px; text-align: left; }}
  td {{ border: 1px solid #dde; padding: 8px 12px; }}
  tr:nth-child(even) {{ background: #f5f8ff; }}
  code {{ background: #f0f4ff; padding: 2px 6px; border-radius: 4px; font-size: 0.9em; }}
  pre {{ background: #f0f4ff; padding: 16px; border-radius: 8px; overflow-x: auto; }}
  blockquote {{ border-left: 4px solid #0052ff; margin: 0; padding-left: 16px; color: #555; }}
</style></head>
<body>
<h1>KODEX 주간 리포트 — {_report_week_label}</h1>
{_report_html_body}
</body></html>"""

    st.download_button(
        "📥 HTML 리포트 다운로드",
        data=_full_html.encode("utf-8"),
        file_name=f"kodex_report_{_report_week_label.replace('.', '-').replace(' ', '')}.html",
        mime="text/html",
        use_container_width=True,
    )
