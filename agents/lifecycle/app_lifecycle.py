"""
ETF 사후관리 — 신규상장 / 상장폐지 모니터링
"""

import os, sys
from datetime import date
from collections import defaultdict

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from krx_data_fetcher import load_cache, detect_listing_changes, _parse_week_label

st.markdown("""
<style>
.lc-title {
    font-size:1.5rem; font-weight:800;
    background:linear-gradient(90deg,#a78bfa,#60a5fa);
    -webkit-background-clip:text; -webkit-text-fill-color:transparent;
    margin-bottom:.3rem;
}
.lc-card {
    border-radius:12px; padding:12px 16px; margin:6px 0;
    border:1px solid rgba(255,255,255,0.08);
}
.lc-badge {
    font-size:.68rem; font-weight:700; padding:2px 10px; border-radius:100px;
    display:inline-block; margin-bottom:6px;
}
.lc-name  { font-size:.95rem; font-weight:700; color:#e8eaed; }
.lc-code  { font-size:.72rem; color:#555; margin-top:2px; }
.lc-week  { font-size:.70rem; color:#aaa; margin-top:4px; }
</style>
""", unsafe_allow_html=True)

# ── 데이터 로드 ───────────────────────────────────────────────────────────────
with st.spinner("ETF 변동 분석 중..."):
    cache  = load_cache()
    result = detect_listing_changes(cache)

sorted_weeks = sorted(cache.keys(), key=lambda w: _parse_week_label(w) or date.min)

# ── 헤더 ─────────────────────────────────────────────────────────────────────
st.markdown('<div class="lc-title">🏷️ ETF 사후관리</div>', unsafe_allow_html=True)
st.caption(f"신규상장 감지 · 상장폐지 모니터링 · 총 {len(sorted_weeks)}주차 데이터 기준")

# ── 상단 메트릭 4개 ───────────────────────────────────────────────────────────
new_confirmed = [x for x in result["new_listings"] if x["status"] == "confirmed"]
new_pending   = [x for x in result["new_listings"] if x["status"] == "pending"]
delist_conf   = [x for x in result["delistings"] if x["reason"] == "delisting_confirmed"]
delist_pend   = [x for x in result["delistings"] if x["reason"] == "delisting_pending"]
maturity      = [x for x in result["delistings"] if x["reason"] == "maturity_redemption"]
gaps          = [x for x in result["delistings"] if x["reason"] == "collection_gap"]

total_etf = len(cache[sorted_weeks[-1]]) if sorted_weeks else 0
prev_etf  = len(cache[sorted_weeks[-2]]) if len(sorted_weeks) >= 2 else total_etf
delta_etf = total_etf - prev_etf

c1, c2, c3, c4 = st.columns(4)
c1.metric("총 KODEX ETF (최신)", f"{total_etf}개", f"{delta_etf:+d}개 (전주 대비)")
c2.metric("신규상장 확정", f"{len(new_confirmed)}건", f"+{len(new_pending)} 추적중" if new_pending else None)
c3.metric("상폐 확정", f"{len(delist_conf)}건", f"{len(delist_pend)} 추적중" if delist_pend else None)
c4.metric("만기 청산", f"{len(maturity)}건")

st.divider()

# ── 주차별 ETF 수 추이 차트 ───────────────────────────────────────────────────
st.subheader("📈 주차별 ETF 수 추이")
week_counts = [(w, len(cache[w])) for w in sorted_weeks]

# 신규상장 주차 마커
new_weeks = set(x["week"] for x in new_confirmed + new_pending)
del_weeks = set(x["week"] for x in delist_conf + delist_pend)

fig = go.Figure()
fig.add_trace(go.Scatter(
    x=[w for w, _ in week_counts],
    y=[n for _, n in week_counts],
    mode="lines+markers",
    line=dict(color="#60a5fa", width=2),
    marker=dict(size=6, color=[
        "#28a745" if w in new_weeks else "#dc3545" if w in del_weeks else "#60a5fa"
        for w, _ in week_counts
    ]),
    hovertemplate="%{x}<br>ETF %{y}개<extra></extra>",
    name="ETF 수",
))
fig.update_layout(
    height=240,
    margin=dict(l=0, r=0, t=10, b=0),
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    xaxis=dict(showgrid=False, tickfont=dict(size=10), tickangle=-30),
    yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.06)"),
    showlegend=False,
)
st.plotly_chart(fig, use_container_width=True)
st.caption("🟢 신규상장 주차  🔴 상폐 주차  🔵 변동 없음")

st.divider()

# ── 탭: 신규상장 / 상폐 / 만기청산 ──────────────────────────────────────────
tab_new, tab_del, tab_mat, tab_gap = st.tabs([
    f"🆕 신규상장 ({len(new_confirmed)+len(new_pending)})",
    f"⛔ 상폐 ({len(delist_conf)+len(delist_pend)})",
    f"⏳ 만기청산 ({len(maturity)})",
    f"🔍 수집 갭 ({len(gaps)})",
])

def _cards(items, status_field, badge_color_map):
    if not items:
        st.info("해당 항목 없음")
        return
    # 최근 주 먼저
    items_sorted = sorted(items, key=lambda x: _parse_week_label(x["week"]) or date.min, reverse=True)
    by_week = defaultdict(list)
    for x in items_sorted:
        by_week[x["week"]].append(x)
    for week in sorted(by_week.keys(), key=lambda w: _parse_week_label(w) or date.min, reverse=True):
        st.markdown(f"**📅 {week}**")
        cols = st.columns(3)
        for i, x in enumerate(by_week[week]):
            sf = x.get(status_field, "")
            emoji, color, label = badge_color_map.get(sf, ("•", "#aaa", sf))
            with cols[i % 3]:
                krx_url = f"https://finance.naver.com/item/main.naver?code={x['종목코드']}"
                st.markdown(
                    f'<div class="lc-card" style="border-color:{color}33;background:{color}08;">'
                    f'<span class="lc-badge" style="background:{color}18;color:{color};border:1px solid {color}44;">{emoji} {label}</span>'
                    f'<div class="lc-name"><a href="{krx_url}" target="_blank" style="color:#e8eaed;text-decoration:none;">{x["종목명"]}</a></div>'
                    f'<div class="lc-code">{x["종목코드"]}</div>'
                    f'</div>',
                    unsafe_allow_html=True
                )
        st.markdown("")

NEW_BADGE = {
    "confirmed": ("✅", "#05b169", "신규 확정"),
    "pending":   ("🔄", "#f59e0b", "추적 중"),
}
DEL_BADGE = {
    "delisting_confirmed": ("⛔", "#cf202f", "상폐 확정"),
    "delisting_pending":   ("⚠️", "#f59e0b",  "추적 중"),
}
MAT_BADGE = {
    "maturity_redemption": ("⏳", "#8b5cf6", "만기 청산"),
}
GAP_BADGE = {
    "collection_gap": ("🔍", "#6b7280", "수집 갭"),
}

with tab_new:
    st.caption("다음 주에도 등장하면 '확정', 첫 주 등장이면 '추적 중'")
    _cards(new_confirmed + new_pending, "status", NEW_BADGE)

with tab_del:
    st.caption("2주+ 연속 미등장 → 상폐 확정 / 1주 미등장 → 추적 중")
    _cards(delist_conf + delist_pend, "reason", DEL_BADGE)

with tab_mat:
    st.caption("이름에 YY-MM 만기 패턴 포함, 재등장 없음 → 만기 정상 청산")
    _cards(maturity, "reason", MAT_BADGE)

with tab_gap:
    st.caption("1주 미등장 후 다시 등장 → 수집 오류 (실제 상폐 아님)")
    _cards(gaps, "reason", GAP_BADGE)
