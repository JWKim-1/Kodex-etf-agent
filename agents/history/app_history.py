"""
마케팅 채널 히스토리 뷰어
marketing_history.json 주차별 수집 결과 열람
- 전체 통합 이벤트 보드 (기간·종목·보상·주관사)
- 세션별 상세 탭
- 백테스트 결과 탭 (마케팅 활동 vs DiD 유효성)
"""

import os, sys, json, html as _html
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

    return (
        f'<div class="ev-card" style="border-color:{color}33;background:{color}08;">'
        f'<span class="ev-card-type {cls}">{icon} {mtype}</span>'
        f'<span class="ev-org-badge" style="background:{color}18;color:{color};border:1px solid {color}44;">{org_icon} {organizer}</span>'
        f'<div class="ev-title">{title_html}</div>'
        + period_html + etf_html +
        f'<div class="ev-summary">{_html.escape(str(summary)[:150])}</div>'
        f'<div class="ev-channel">📡 {_html.escape(str(channel))}</div>'
        f'</div>'
    )


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
                    # 유튜브 썸네일 카드 (videos 있는 채널)
                    yt_channels = [(k, v) for k, v in ok_cards if v.get("videos")]
                    if yt_channels:
                        st.markdown("**📹 유튜브 영상**")
                        for ch_key, ch_data in yt_channels:
                            ch_name = ch_data.get("channel_name", ch_key)
                            videos = ch_data["videos"]
                            etf_vids = [v for v in videos if v.get("is_etf_related")]
                            all_vids = etf_vids or videos  # ETF 관련 없으면 전체 표시
                            if not all_vids:
                                continue
                            st.caption(f"📡 {ch_name} — ETF 관련 {len(etf_vids)}/{len(videos)}개")
                            vid_cols = st.columns(min(len(all_vids), 4))
                            for col, v in zip(vid_cols, all_vids[:4]):
                                with col:
                                    thumb = v.get("thumbnail", "")
                                    title = v.get("title", "")
                                    url = v.get("url", "#")
                                    pub = v.get("published_at", "")[:10]
                                    if thumb:
                                        st.markdown(
                                            f'<a href="{url}" target="_blank" style="text-decoration:none;">'
                                            f'<img src="{thumb}" style="width:100%;border-radius:6px;margin-bottom:4px;">'
                                            f'</a>', unsafe_allow_html=True)
                                    st.markdown(
                                        f'<a href="{url}" target="_blank" style="font-size:.78rem;color:#e8eaed;'
                                        f'text-decoration:none;line-height:1.3;display:block;">{_html.escape(title)}</a>'
                                        f'<div style="font-size:.68rem;color:#666;margin-top:2px;">{pub}</div>',
                                        unsafe_allow_html=True)
                        st.markdown("")

                    # 나머지 채널 텍스트 카드
                    non_yt = [(k, v) for k, v in ok_cards if not v.get("videos")]
                    if non_yt:
                        ch_html = '<div class="ev-board">'
                        for ch_key, ch_data in non_yt:
                            name    = ch_data.get("channel_name", ch_key)
                            snippet = (ch_data.get("snippet") or "")[:160]
                            ch_html += (
                                f'<div class="ev-card" style="border-color:{color}33;background:{color}06;">'
                                f'<span class="ev-card-type ev-type-content">✅ 수집 성공</span>'
                                f'<div class="ev-title">📡 {name}</div>'
                                f'<div class="ev-summary">{_html.escape(snippet)}</div>'
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

# ── 백테스트 결과 탭 ──────────────────────────────────────────────────────────
st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
st.markdown("### 📊 백테스트 결과")

_BT_FILE = os.path.join(_ROOT, "marketing_backtest_result.json")

if not os.path.exists(_BT_FILE):
    st.warning(
        "결과 없음 — `marketing_backtest.py`를 실행해주세요.\n\n"
        "```\npython marketing_backtest.py\n```"
    )
else:
    with open(_BT_FILE, encoding="utf-8") as _f:
        _bt = json.load(_f)

    st.caption(
        f"생성: {_bt.get('generated_at','')} | "
        f"마케팅 이력 주차: {', '.join(_bt.get('marketing_history_weeks', []))} | "
        f"주차 매핑: {_bt.get('week_map', {})}"
    )

    _note = _bt.get("analysis_note", "")
    if _note:
        st.info(f"ℹ️ {_note}")

    _CH_LABELS = {
        "securities": "📈 증권사",
        "bank":       "🏦 은행",
        "mass":       "🎯 개인(KODEX직접)",
    }
    _CH_COLORS = {
        "securities": "#4d9fff",
        "bank":       "#05b169",
        "mass":       "#f0c040",
    }

    _bt_tabs = st.tabs([_CH_LABELS.get(k, k) for k in ["securities", "bank", "mass"]] + ["🔬 은행 보조분석"])

    for _tab, _ch in zip(_bt_tabs[:3], ["securities", "bank", "mass"]):
        with _tab:
            _res = (_bt.get("channels") or {}).get(_ch, {})
            _color = _CH_COLORS.get(_ch, "#aaa")

            if not _res or _res.get("status") == "insufficient_data":
                st.warning(f"⚠️ 데이터 부족: {_res.get('reason', '분석 불가')}")
                _lim = _res.get("data_limitation", "")
                if _lim:
                    st.caption(f"데이터 제약: {_lim}")
            else:
                _n_with = _res.get("n_weeks_with_marketing", 0)
                _n_wo   = _res.get("n_weeks_without", 0)
                _mw     = _res.get("mean_did_with")
                _mwo    = _res.get("mean_did_without")
                _t      = _res.get("t_stat")
                _p      = _res.get("p_value")
                _sig    = _res.get("significant", False)
                _pr     = _res.get("pearson_r")
                _prp    = _res.get("pearson_p")

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("마케팅 있는 주", f"{_n_with}주")
                c2.metric("마케팅 없는 주", f"{_n_wo}주")
                if _mw is not None:
                    c3.metric("평균 DiD (마케팅O)", f"{_mw:.4f}")
                if _mwo is not None:
                    c4.metric("평균 DiD (마케팅X)", f"{_mwo:.4f}")

                if _t is not None and _p is not None:
                    st.markdown(
                        f"**t-검정:** t = `{_t:.3f}` | p = `{_p:.4f}` | "
                        + (f"<span style='color:#4ade80;font-weight:700;'>★ 유의 (p < 0.05)</span>"
                           if _sig else "<span style='color:#aaa;'>비유의</span>"),
                        unsafe_allow_html=True,
                    )
                else:
                    st.caption("t-검정: 샘플 부족으로 생략")

                if _pr is not None:
                    st.markdown(
                        f"**Pearson r (이벤트 수 ↔ 주간 DiD):** `{_pr:.4f}` (p = `{_prp:.4f}`)"
                    )
                else:
                    st.caption("Pearson 상관: 주차 수 부족으로 생략")

                _lim = _res.get("data_limitation", "")
                if _lim:
                    with st.expander("데이터 제약 상세"):
                        st.caption(_lim)

                # ETF별 표
                _by_etf = _res.get("by_etf", {})
                if _by_etf:
                    import pandas as _pd
                    _etf_rows = []
                    for _code, _ev in _by_etf.items():
                        _diff = None
                        if _ev.get("mean_with") is not None and _ev.get("mean_without") is not None:
                            _diff = round(_ev["mean_with"] - _ev["mean_without"], 4)
                        _etf_rows.append({
                            "종목코드": _code,
                            "종목명": _ev.get("name", ""),
                            "마케팅O 평균": round(_ev["mean_with"], 4) if _ev.get("mean_with") is not None else None,
                            "마케팅X 평균": round(_ev["mean_without"], 4) if _ev.get("mean_without") is not None else None,
                            "효과(Δ)": _diff,
                            "O 샘플수": _ev.get("n_with", 0),
                            "X 샘플수": _ev.get("n_without", 0),
                        })
                    _etf_df = _pd.DataFrame(_etf_rows)
                    if "효과(Δ)" in _etf_df.columns:
                        _etf_df = _etf_df.sort_values("효과(Δ)", ascending=False, na_position="last")
                    st.markdown("**ETF별 마케팅 효과 분해**")
                    st.dataframe(_etf_df, use_container_width=True, hide_index=True)

    # 은행 보조분석 탭
    with _bt_tabs[3]:
        _aux = _bt.get("bank_aux_analysis", {})
        st.caption(_aux.get("note", ""))
        _mw2  = _aux.get("mean_did_with")
        _mwo2 = _aux.get("mean_did_without")
        _t2   = _aux.get("t_stat")
        _p2   = _aux.get("p_value")
        _sig2 = _aux.get("significant", False)

        if _mw2 is not None or _mwo2 is not None:
            c1, c2 = st.columns(2)
            if _mw2 is not None:
                c1.metric("마케팅O 평균 Z-score", f"{_mw2:.4f}")
            if _mwo2 is not None:
                c2.metric("마케팅X 평균 Z-score", f"{_mwo2:.4f}")

        if _t2 is not None and _p2 is not None:
            st.markdown(
                f"**t-검정:** t = `{_t2:.3f}` | p = `{_p2:.4f}` | "
                + (f"<span style='color:#4ade80;font-weight:700;'>★ 유의</span>"
                   if _sig2 else "<span style='color:#aaa;'>비유의</span>"),
                unsafe_allow_html=True,
            )
        else:
            st.info("bank_zscore_history에 marketing_detected=False 주차가 없어 비교 불가 (모든 수집 주차가 마케팅 기간).")

# ── 이벤트 캘린더 ─────────────────────────────────────────────────────────────
import re as _re, plotly.graph_objects as _go
from datetime import datetime as _dt, timedelta as _td

_DATE_PAT = _re.compile(r'(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})')

def _parse_date(s):
    m = _DATE_PAT.search(str(s or ''))
    if m:
        try: return _dt(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except: pass
    return None

_SESS_COLOR_CAL = {"securities":"#4d9fff","bank":"#05b169","mass":"#f0c040","competitor":"#f43f5e"}
_SESS_LBL_CAL   = {"securities":"증권","bank":"은행","mass":"개인","competitor":"경쟁사"}
_PROV_COLOR_CAL = {"KODEX":"#4d9fff","TIGER":"#ff8c42","ACE":"#05b169","RISE":"#a78bfa",
                   "HANARO":"#00c6ff","SOL":"#f43f5e","PLUS":"#fb923c"}

# 전체 히스토리에서 기간 있는 이벤트 수집
_cal_events = []
for _wk in sorted(history.keys(), key=lambda w: _parse_week_label(w) or date.min):
    for _sk in ["securities","bank","mass","competitor"]:
        _sess = (history[_wk].get(_sk) or {})
        for _ev in (_sess.get("events") or {}).get("events") or []:
            _s = _parse_date(_ev.get("event_period",""))
            _dates = _DATE_PAT.findall(str(_ev.get("event_period","") or ""))
            if len(_dates) >= 2:
                try:
                    _sd = _dt(int(_dates[0][0]),int(_dates[0][1]),int(_dates[0][2]))
                    _ed = _dt(int(_dates[1][0]),int(_dates[1][1]),int(_dates[1][2]))
                except: continue
            elif len(_dates) == 1:
                try:
                    _sd = _ed = _dt(int(_dates[0][0]),int(_dates[0][1]),int(_dates[0][2]))
                except: continue
            else:
                continue
            _prov = _ev.get("provider","") or _sk
            _cal_events.append({
                "session": _sk, "provider": _prov,
                "title": (_ev.get("title") or "")[:28],
                "start": _sd, "end": _ed,
                "color": _PROV_COLOR_CAL.get(_prov, _SESS_COLOR_CAL.get(_sk,"#888")),
                "label": f"[{_SESS_LBL_CAL.get(_sk,_sk)}] {(_ev.get('title') or '')[:25]}",
            })

st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
st.markdown("### 📅 마케팅 이벤트 캘린더")
st.caption("전체 수집 이력에서 기간 정보가 있는 이벤트를 달력으로 표시합니다.")

if _cal_events:
    # 월 선택
    _all_months = sorted(set(f"{e['start'].year}-{e['start'].month:02d}" for e in _cal_events), reverse=True)
    _sel_month = st.selectbox("월 선택", _all_months, index=0, key="cal_month")
    _yr, _mo = int(_sel_month.split("-")[0]), int(_sel_month.split("-")[1])
    _mo_start = _dt(_yr, _mo, 1)
    _mo_end   = (_mo_start + _td(days=32)).replace(day=1) - _td(days=1)

    # 해당 월 이벤트 필터
    _mo_events = [e for e in _cal_events if e["end"] >= _mo_start and e["start"] <= _mo_end]

    if _mo_events:
        fig_cal = _go.Figure()
        for i, ev in enumerate(_mo_events):
            _s = max(ev["start"], _mo_start)
            _e = min(ev["end"], _mo_end)
            fig_cal.add_trace(_go.Bar(
                x=[(_e - _s).days + 1],
                y=[ev["label"]],
                orientation="h",
                base=_s.strftime("%Y-%m-%d"),
                marker=dict(color=ev["color"], opacity=0.8,
                            line=dict(color="rgba(255,255,255,0.3)", width=1)),
                hovertemplate=(
                    f"<b>{ev['title']}</b><br>"
                    f"{ev['start'].strftime('%Y.%m.%d')} ~ {ev['end'].strftime('%Y.%m.%d')}<br>"
                    f"채널: {_SESS_LBL_CAL.get(ev['session'],ev['session'])}"
                    "<extra></extra>"
                ),
                showlegend=False,
            ))
        # 오늘 날짜 선
        _today_str = date.today().strftime("%Y-%m-%d")
        if _mo_start.date() <= date.today() <= _mo_end.date():
            fig_cal.add_vline(x=_today_str, line_color="rgba(255,255,255,0.4)",
                              line_width=1.5, line_dash="dot",
                              annotation_text="오늘", annotation_font_color="#aaa",
                              annotation_font_size=10)

        fig_cal.update_layout(
            height=max(250, len(_mo_events) * 32 + 80),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(20,20,30,0.5)",
            font=dict(color="#e8eaed", size=11),
            xaxis=dict(type="date",
                       range=[_mo_start.strftime("%Y-%m-%d"), (_mo_end + _td(days=1)).strftime("%Y-%m-%d")],
                       gridcolor="rgba(255,255,255,0.06)",
                       tickformat="%m/%d", dtick="D3", title=""),
            yaxis=dict(autorange="reversed", showgrid=False),
            margin=dict(l=10, r=20, t=20, b=30),
            barmode="overlay",
        )
        st.plotly_chart(fig_cal, use_container_width=True)
        st.caption(f"{_sel_month} 기준 {len(_mo_events)}개 이벤트 표시")
    else:
        st.info(f"{_sel_month}에 기간 정보 있는 이벤트 없음")
else:
    st.info("기간 정보 있는 이벤트 없음 — 마케팅 수집 후 이벤트 기간이 추출되면 표시됩니다.")
