"""
마케팅 채널 히스토리 뷰어
marketing_history.json 주차별 수집 결과 열람
"""

import os, sys, json
from datetime import date

import streamlit as st

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scheduled_collect import load_history, HISTORY_FILE
from krx_data_fetcher import _parse_week_label

HISTORY_FILE_PATH = HISTORY_FILE

SESSION_LABELS = {
    "securities": "📈 증권사",
    "bank":       "🏦 은행",
    "mass":       "🎯 개인",
    "competitor": "🏢 경쟁사",
}

def _sorted_weeks(h):
    return sorted(h.keys(), key=lambda w: _parse_week_label(w) or date.min, reverse=True)

# ── UI ───────────────────────────────────────────────────────────────────────

st.title("📁 마케팅 채널 히스토리")
st.caption(f"저장 위치: {HISTORY_FILE_PATH}")

history = load_history()

if not history:
    st.warning("수집된 히스토리가 없습니다. 먼저 채널 수집을 실행해주세요.")
    st.stop()

weeks = _sorted_weeks(history)
selected = st.selectbox("주차 선택", weeks, index=0)
entry = history[selected]

st.markdown(f"**수집 일시:** {entry.get('collected_at', '알 수 없음')}")
st.markdown("---")

tabs = st.tabs([SESSION_LABELS.get(k, k) for k in SESSION_LABELS])

for tab, (sess_key, sess_label) in zip(tabs, SESSION_LABELS.items()):
    with tab:
        sess = entry.get(sess_key)
        if not sess:
            st.info("이 주차에 수집 데이터 없음")
            continue

        if "error" in sess:
            st.error(f"수집 오류: {sess['error']}")
            continue

        col = sess.get("collection", {})
        ok_cnt   = col.get("ok_count", 0)
        fail_cnt = col.get("fail_count", 0)
        c1, c2 = st.columns(2)
        c1.metric("수집 성공", f"{ok_cnt}채널")
        c2.metric("수집 실패", f"{fail_cnt}채널")

        # LLM 이벤트
        events_data = sess.get("events") or {}
        events = events_data.get("events") or []
        summary = events_data.get("summary") or ""
        detected = events_data.get("marketing_detected")

        if detected is None:
            st.info("LLM 분석 미실행 — API 키 입력 후 종합 리포트에서 분석 가능합니다.")
        elif detected:
            st.success(f"📣 마케팅 활동 감지 — {len(events)}건")
        else:
            st.info("이번 주 마케팅 활동 없음")

        if summary and detected is not None:
            st.markdown(f"> {summary}")

        if events:
            st.markdown("**감지된 이벤트**")
            for ev in events:
                with st.expander(f"**{ev.get('title','(제목 없음)')}** — {ev.get('provider','')} [{ev.get('marketing_type','')}]"):
                    period = ev.get("event_period") or "기간 미상"
                    etf    = ev.get("target_etf") or "—"
                    url    = ev.get("url")
                    evsum  = ev.get("event_summary") or ""
                    st.markdown(f"- **기간:** {period}")
                    st.markdown(f"- **대상 ETF:** {etf}")
                    if evsum:
                        st.markdown(f"- **내용:** {evsum}")
                    if url:
                        st.markdown(f"- **링크:** {url}")

        # 수집 원본 스니펫
        raw = sess.get("raw") or {}
        if raw:
            with st.expander("🗂 수집 원본 (채널별 스니펫)"):
                for ch_key, ch_data in raw.items():
                    ok_icon = "✅" if ch_data.get("success") else "❌"
                    name    = ch_data.get("channel_name", ch_key)
                    snippet = ch_data.get("snippet", "")
                    st.markdown(f"{ok_icon} **{name}**")
                    if snippet:
                        st.caption(snippet[:200])

        # 실패 채널 목록
        failed = col.get("failed") or []
        if failed:
            with st.expander(f"❌ 실패 채널 {len(failed)}개"):
                for f in failed:
                    st.markdown(f"- **{f.get('name','')}**: {f.get('error','')}")
