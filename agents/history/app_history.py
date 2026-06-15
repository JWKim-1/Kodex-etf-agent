"""
마케팅 채널 히스토리 뷰어
marketing_history.json 주차별 수집 결과 열람
- 전체 통합 이벤트 보드 (기간·종목·보상·주관사)
- 세션별 상세 탭
"""

import os, sys, json
from datetime import date
from collections import defaultdict

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
    "mass":       "🎯 개인(KODEX직접)",
    "competitor": "🏢 경쟁사",
}

# 주관사 분류
SESSION_ORGANIZER = {
    "securities": "증권사",
    "bank":       "은행",
    "mass":       "자산운용사",
    "competitor": "자산운용사",
}

SESSION_COLOR = {
    "securities": "#4d9fff",
    "bank":       "#05b169",
    "mass":       "#f0c040",
    "competitor": "#f43f5e",
}

def _sorted_weeks(h):
    return sorted(h.keys(), key=lambda w: _parse_week_label(w) or date.min, reverse=True)

def _gather_all_events(entry: dict) -> list:
    """모든 세션의 이벤트를 하나의 리스트로 통합, 세션 정보 추가."""
    all_events = []
    for sess_key in ["securities", "bank", "mass", "competitor"]:
        sess = entry.get(sess_key) or {}
        if "error" in sess:
            continue
        events = (sess.get("events") or {}).get("events") or []
        organizer = SESSION_ORGANIZER.get(sess_key, sess_key)
        color = SESSION_COLOR.get(sess_key, "#aaa")
        sess_label = SESSION_LABELS.get(sess_key, sess_key)
        for ev in events:
            ev = dict(ev)
            ev["_sess_key"]   = sess_key
            ev["_organizer"]  = organizer
            ev["_color"]      = color
            ev["_sess_label"] = sess_label
            all_events.append(ev)
    return all_events

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.hist-title {
    font-size:1.5rem; font-weight:800;
    background:linear-gradient(90deg,#4d9fff,#a78bfa);
    -webkit-background-clip:text; -webkit-text-fill-color:transparent;
    margin-bottom:.2rem;
}
.hist-week { display:inline-block; background:rgba(77,159,255,0.12);
    border:1px solid rgba(77,159,255,0.3); color:#4d9fff;
    border-radius:100px; padding:3px 14px; font-size:.82rem; font-weight:600; margin-bottom:1rem; }
.ev-board { display:flex; gap:12px; flex-wrap:wrap; margin:12px 0 20px; }
.ev-card {
    flex:1; min-width:240px; max-width:340px;
    border-radius:14px; padding:14px 16px;
    border:1px solid rgba(255,255,255,0.1);
    background:rgba(255,255,255,0.03);
    transition:background .15s;
}
.ev-card:hover { background:rgba(255,255,255,0.06); }
.ev-card-type {
    font-size:.65rem; font-weight:700; padding:2px 8px; border-radius:100px;
    display:inline-block; margin-bottom:2px;
}
.ev-type-event   { background:rgba(0,198,255,0.15);color:#00c6ff;border:1px solid rgba(0,198,255,0.3); }
.ev-type-promo   { background:rgba(5,177,105,0.15);color:#05b169;border:1px solid rgba(5,177,105,0.3); }
.ev-type-content { background:rgba(255,200,50,0.15);color:#f0c040;border:1px solid rgba(255,200,50,0.3); }
.ev-type-fee     { background:rgba(167,139,250,0.15);color:#a78bfa;border:1px solid rgba(167,139,250,0.3); }
.ev-type-etc     { background:rgba(255,255,255,0.08);color:#aaa;border:1px solid rgba(255,255,255,0.15); }
.ev-org-badge {
    font-size:.62rem; font-weight:700; padding:2px 8px; border-radius:100px;
    display:inline-block; margin-bottom:6px; margin-left:4px;
}
.ev-title { font-size:.88rem; font-weight:700; color:#e8eaed; margin-bottom:4px; line-height:1.4; }
.ev-period { font-size:.73rem; color:#4d9fff; margin:3px 0; }
.ev-etf    { font-size:.70rem; margin:3px 0; }
.ev-summary { font-size:.77rem; color:#aaa; line-height:1.5; margin:6px 0 0; }
.ev-channel { font-size:.65rem; color:#555; margin-top:6px; }
.section-divider { height:1px; background:rgba(255,255,255,0.07); margin:1.5rem 0; }
.org-header {
    font-size:1rem; font-weight:700; margin:16px 0 8px;
}
</style>
""", unsafe_allow_html=True)

_type_cls  = {"이벤트":"ev-type-event","프로모션":"ev-type-promo","추천콘텐츠":"ev-type-content","수수료혜택":"ev-type-fee"}
_type_icon = {"이벤트":"🎁","프로모션":"💰","추천콘텐츠":"📺","수수료혜택":"🎯"}
_org_icon  = {"증권사":"📈","은행":"🏦","자산운용사":"🏢"}


def _ev_card_html(ev: dict) -> str:
    mtype      = ev.get("marketing_type", "기타")
    cls        = _type_cls.get(mtype, "ev-type-etc")
    icon       = _type_icon.get(mtype, "📋")
    title      = (ev.get("title") or "")[:60]
    period     = ev.get("event_period") or ""
    target_etf = ev.get("target_etf") or ""
    summary    = ev.get("event_summary") or ev.get("event_details") or ""
    channel    = ev.get("channel") or ev.get("provider") or ""
    url        = ev.get("url") or ""
    organizer  = ev.get("_organizer", "")
    color      = ev.get("_color", "#aaa")

    title_html = (
        f'<a href="{url}" target="_blank" style="color:#e8eaed;text-decoration:none;">{title}</a>'
        if url and url.startswith("http") else title
    )
    period_html = f'<div class="ev-period">📅 {period}</div>' if period and period != "null" else ""
    etf_html    = f'<div class="ev-etf" style="color:{color};">🎯 {target_etf}</div>' if target_etf and target_etf != "null" else ""
    org_icon    = _org_icon.get(organizer, "🏛")

    return f"""
    <div class="ev-card" style="border-color:{color}33;background:{color}08;">
      <span class="ev-card-type {cls}">{icon} {mtype}</span>
      <span class="ev-org-badge" style="background:{color}18;color:{color};border:1px solid {color}44;">{org_icon} {organizer}</span>
      <div class="ev-title">{title_html}</div>
      {period_html}
      {etf_html}
      <div class="ev-summary">{str(summary)[:150]}</div>
      <div class="ev-channel">📡 {channel}</div>
    </div>"""


# ── UI ───────────────────────────────────────────────────────────────────────
st.markdown('<div class="hist-title">📁 마케팅 채널 히스토리</div>', unsafe_allow_html=True)

history = load_history()

if not history:
    st.warning("수집된 히스토리가 없습니다. 먼저 채널 수집을 실행해주세요.")
    st.stop()

weeks = _sorted_weeks(history)
selected = st.selectbox("주차 선택", weeks, index=0, key="hist_week")
entry = history[selected]

st.markdown(f'<span class="hist-week">📅 {selected} · 수집: {entry.get("collected_at","")}</span>', unsafe_allow_html=True)

# ── 전체 통합 이벤트 보드 ────────────────────────────────────────────────────
all_events = _gather_all_events(entry)

if all_events:
    st.markdown(f"### 📋 전체 마케팅 이벤트 보드 — {len(all_events)}건")
    st.caption("증권사·은행·자산운용사 전채널 통합 | 색상: 파란=증권사, 초록=은행, 노랑=개인채널, 빨강=경쟁사")

    # 주관사별 그룹핑
    by_org = defaultdict(list)
    for ev in all_events:
        org = ev.get("_organizer", "기타")
        by_org[org].append(ev)

    org_order = ["증권사", "은행", "자산운용사"]
    for org in org_order:
        evs = by_org.get(org, [])
        if not evs:
            continue
        color = {"증권사": "#4d9fff", "은행": "#05b169", "자산운용사": "#f43f5e"}.get(org, "#aaa")
        icon  = _org_icon.get(org, "🏛")
        st.markdown(
            f'<div class="org-header" style="color:{color};">{icon} {org} ({len(evs)}건)</div>',
            unsafe_allow_html=True
        )
        cards_html = '<div class="ev-board">'
        for ev in evs:
            cards_html += _ev_card_html(ev)
        cards_html += "</div>"
        st.markdown(cards_html, unsafe_allow_html=True)

else:
    # LLM 분석 안 됐거나 이벤트 없음
    any_detected = any(
        (entry.get(k) or {}).get("events", {}).get("marketing_detected")
        for k in ["securities","bank","mass","competitor"]
    )
    any_pending = any(
        (entry.get(k) or {}).get("events", {}).get("marketing_detected") is None
        for k in ["securities","bank","mass","competitor"]
    )
    if any_pending:
        st.info("💡 LLM 분석이 실행되지 않은 주차입니다. Anthropic API 키 입력 후 스케줄러를 재실행하면 이벤트 보드가 채워집니다.")
    else:
        st.info("이번 주 감지된 마케팅 이벤트 없음")

st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

# ── 세션별 상세 탭 ────────────────────────────────────────────────────────────
st.markdown("### 📊 세션별 수집 현황")
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

        col_data = sess.get("collection", {})
        ok_cnt   = col_data.get("ok_count", 0)
        fail_cnt = col_data.get("fail_count", 0)
        c1, c2 = st.columns(2)
        c1.metric("수집 성공", f"{ok_cnt}채널")
        c2.metric("수집 실패", f"{fail_cnt}채널")

        events_data = sess.get("events") or {}
        summary  = events_data.get("summary") or ""
        detected = events_data.get("marketing_detected")
        events   = events_data.get("events") or []

        if detected is None:
            st.info("LLM 분석 미실행")
        elif detected:
            st.success(f"📣 마케팅 감지 {len(events)}건")
        else:
            st.info("마케팅 활동 없음")

        # 이벤트 카드 (LLM 분석 결과)
        sess_evs = [ev for ev in all_events if ev.get("_sess_key") == sess_key]
        if sess_evs:
            if summary:
                st.caption(summary)
            cards_html = '<div class="ev-board">'
            for ev in sess_evs:
                cards_html += _ev_card_html(ev)
            cards_html += "</div>"
            st.markdown(cards_html, unsafe_allow_html=True)

        # 수집 원본 — 채널 카드
        raw = sess.get("raw") or {}
        if raw:
            color = SESSION_COLOR.get(sess_key, "#aaa")
            with st.expander(f"📡 채널별 수집 현황 ({ok_cnt}개 성공)", expanded=False):
                ok_cards   = [(k, v) for k, v in raw.items() if v.get("success")]
                fail_cards = [(k, v) for k, v in raw.items() if not v.get("success")]

                if ok_cards:
                    ch_html = '<div class="ev-board">'
                    for ch_key, ch_data in ok_cards:
                        name    = ch_data.get("channel_name", ch_key)
                        snippet = (ch_data.get("snippet") or "")[:160]
                        ch_html += (
                            f'<div class="ev-card" style="border-color:{color}33;background:{color}06;">'
                            f'<span class="ev-card-type ev-type-content">✅ 수집 성공</span>'
                            f'<div class="ev-title">📡 {name}</div>'
                            f'<div class="ev-summary">{snippet}</div>'
                            f'</div>'
                        )
                    ch_html += "</div>"
                    st.markdown(ch_html, unsafe_allow_html=True)

                if fail_cards:
                    st.markdown("**❌ 실패 채널**")
                    fail_html = '<div class="ev-board">'
                    for ch_key, ch_data in fail_cards:
                        name  = ch_data.get("channel_name", ch_key)
                        error = (ch_data.get("error") or ch_data.get("error_label") or "")[:120]
                        fail_html += (
                            f'<div class="ev-card" style="border-color:#cf202f33;background:#cf202f06;">'
                            f'<span class="ev-card-type" style="background:rgba(207,32,47,0.15);color:#cf202f;border:1px solid rgba(207,32,47,0.3);">❌ 실패</span>'
                            f'<div class="ev-title">📡 {name}</div>'
                            f'<div class="ev-summary" style="color:#888;">{error}</div>'
                            f'</div>'
                        )
                    fail_html += "</div>"
                    st.markdown(fail_html, unsafe_allow_html=True)
