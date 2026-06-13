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


def _history_summary(history: dict, week: str) -> str:
    """해당 주차 4개 세션 마케팅 이벤트 요약 텍스트."""
    entry = history.get(week, {})
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


def _listing_summary(cache: dict) -> str:
    result = detect_listing_changes(cache)
    new_c = [x for x in result["new_listings"] if x["status"] == "confirmed"]
    delist = [x for x in result["delistings"] if x["reason"] in ("delisting_confirmed", "delisting_pending", "maturity_redemption")]
    lines = []
    if new_c:
        lines.append(f"신규상장 확정 {len(new_c)}건: " + ", ".join(f"{x['종목명']}({x['week']})" for x in new_c[-5:]))
    if delist:
        lines.append(f"상폐/만기 {len(delist)}건: " + ", ".join(f"{x['종목명']}[{x['reason']}]" for x in delist[-5:]))
    return "\n".join(lines) if lines else "신규상장/상폐 이슈 없음"


# ── LLM 리포트 생성 ───────────────────────────────────────────────────────────

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

    client = ant.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


# ── UI ───────────────────────────────────────────────────────────────────────

st.title("📋 주간 종합 리포트")
st.caption("6개 채널 데이터를 통합 분석해 마케팅 인사이트 & 액션 제안을 생성합니다")

api_key = os.getenv("ANTHROPIC_API_KEY", "")
if not api_key:
    api_key = st.text_input("Anthropic API Key", type="password", key="report_api_key")

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
        st.text(_listing_summary(cache))

    st.markdown("**채널 이벤트**")
    hist_text = _history_summary(history, selected_week)
    if "이벤트 없음" in hist_text and "수집 데이터 없음" in hist_text:
        st.warning("해당 주차 채널 수집 데이터가 없습니다. 금요일 자동수집 후 이용하세요.")
    else:
        st.text(hist_text[:1200])

st.markdown("---")

if st.button("🤖  리포트 생성", type="primary", use_container_width=True, key="run_report"):
    if not api_key:
        st.error("API Key를 입력해주세요.")
        st.stop()

    with st.spinner("6개 채널 데이터 통합 분석 중..."):
        krx_text     = _krx_summary(cache, selected_week)
        history_text = _history_summary(history, selected_week)
        listing_text = _listing_summary(cache)

        report_md = generate_report(
            week=selected_week,
            krx_text=krx_text,
            history_text=history_text,
            listing_text=listing_text,
            api_key=api_key,
        )

    st.session_state["report_md"] = report_md
    st.session_state["report_week"] = selected_week

if "report_md" in st.session_state:
    st.markdown(f"### {st.session_state['report_week']} 주간 종합 리포트")
    st.markdown(st.session_state["report_md"])

    st.download_button(
        "📥 리포트 다운로드 (.md)",
        data=st.session_state["report_md"],
        file_name=f"kodex_report_{st.session_state['report_week'].replace('.', '-').replace(' ', '')}.md",
        mime="text/markdown",
        use_container_width=True,
    )
