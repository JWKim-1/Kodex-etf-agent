"""
주간 종합 리포트 — 시각화 중심 리뉴얼
탭1: 시장 트렌드 (수익률/거래대금 차트 + 전략 매트릭스)
탭2: 마케팅 활동 (채널별 이벤트 카드 + 썸네일)
탭3: 수급 분석 (투자자별 순매수 차트)
탭4: 경쟁사 동향 (채널 활동 + DiD)
탭5: AI 인사이트 (LLM 종합 분석)
"""

import os, sys, json, re, html as _html
from datetime import datetime, date, timedelta
from collections import defaultdict

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
load_dotenv()

from scheduled_collect import load_history
from krx_data_fetcher import (
    load_cache, load_trend_cache, _parse_week_label, detect_listing_changes
)

_REPORT_CACHE_FILE = os.path.join(_ROOT, "report_cache.json")

def _load_report_cache():
    try:
        return json.loads(open(_REPORT_CACHE_FILE, encoding="utf-8").read())
    except Exception:
        return {}

def _save_report_cache(week, md):
    c = _load_report_cache(); c[week] = md
    open(_REPORT_CACHE_FILE, "w", encoding="utf-8").write(json.dumps(c, ensure_ascii=False, indent=2))

def _sorted_weeks(d):
    return sorted(d.keys(), key=lambda w: _parse_week_label(w) or date.min)

def _closest_history_week(history, week):
    if week in history: return week
    target = _parse_week_label(week)
    if not target or not history: return week
    best, best_diff = week, 999
    for hw in history:
        hw_date = _parse_week_label(hw)
        if hw_date:
            diff = abs((hw_date - target).days)
            if diff < best_diff: best, best_diff = hw, diff
    return best if best_diff <= 7 else week

# ── CSS ──────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.rpt-title {
    font-size:1.8rem; font-weight:900;
    background:linear-gradient(90deg,#4d9fff,#a78bfa);
    -webkit-background-clip:text; -webkit-text-fill-color:transparent;
    margin-bottom:.2rem;
}
.rpt-week { display:inline-block; background:rgba(77,159,255,0.12);
    border:1px solid rgba(77,159,255,0.3); color:#4d9fff;
    border-radius:100px; padding:4px 16px; font-size:.85rem; font-weight:600; }
.ev-card {
    border-radius:12px; padding:12px 14px; margin:5px 0;
    border:1px solid rgba(255,255,255,0.08);
    background:rgba(255,255,255,0.03);
    box-sizing:border-box; height:100%;
}
/* 카드 행 align */
[data-testid="column"] > div { height:100%; }
[data-testid="stVerticalBlockBorderWrapper"] { height:100%; }
.ev-type { font-size:.65rem; font-weight:700; padding:2px 8px; border-radius:100px;
    display:inline-block; margin-bottom:4px; }
.ev-title { font-size:.88rem; font-weight:700; color:#e8eaed; margin:3px 0; }
.ev-meta  { font-size:.72rem; color:#aaa; }
.thumb-card { border-radius:10px; overflow:hidden; border:1px solid rgba(255,255,255,0.08);
    background:rgba(255,255,255,0.03); margin:4px; }
.thumb-title { font-size:.75rem; color:#e8eaed; padding:6px 8px; line-height:1.4; }
.section-divider { height:1px; background:rgba(255,255,255,0.07); margin:1.5rem 0; }
.insight-box {
    border-radius:14px; padding:20px 24px;
    background:linear-gradient(135deg,rgba(77,159,255,0.08),rgba(167,139,250,0.08));
    border:1px solid rgba(77,159,255,0.2);
}
</style>
""", unsafe_allow_html=True)

_TYPE_COLOR = {"이벤트":"#00c6ff","프로모션":"#05b169","추천콘텐츠":"#f0c040","수수료혜택":"#a78bfa"}
_TYPE_ICON  = {"이벤트":"🎁","프로모션":"💰","추천콘텐츠":"📺","수수료혜택":"🎯"}
_SESS_COLOR = {"securities":"#4d9fff","bank":"#05b169","mass":"#f0c040","competitor":"#f43f5e"}
_SESS_LABEL = {"securities":"📈 증권","bank":"🏦 은행","competitor":"🏢 경쟁사(ETF운용사)"}
_PROV_COLOR = {
    "KODEX":"#4d9fff","TIGER":"#ff8c42","ACE":"#05b169",
    "RISE":"#a78bfa","HANARO":"#00c6ff","SOL":"#f43f5e","PLUS":"#fb923c",
}

def _prov(name):
    for k, c in _PROV_COLOR.items():
        if k in str(name).upper(): return k, c
    return None, "#888"

# ── 데이터 로드 ───────────────────────────────────────────────────────────────
cache    = load_cache()
history  = load_history()
trend_cache = load_trend_cache()
all_weeks = list(reversed(_sorted_weeks(cache)))

if not all_weeks:
    st.error("KRX 캐시 데이터 없음"); st.stop()

# ── 헤더 ─────────────────────────────────────────────────────────────────────
st.markdown('<div class="rpt-title">📋 주간 종합 리포트</div>', unsafe_allow_html=True)

col_sel, col_btn = st.columns([3, 1])
with col_sel:
    selected_week = st.selectbox("분석 주차", all_weeks, index=0, key="rpt_week")
with col_btn:
    st.markdown("<div style='margin-top:28px;'></div>", unsafe_allow_html=True)
    refresh = st.button("🔄 새로고침", key="rpt_refresh", use_container_width=True)

st.markdown(f'<span class="rpt-week">📅 {selected_week} 기준</span>', unsafe_allow_html=True)
st.markdown("")

# ── 상단 메트릭 ───────────────────────────────────────────────────────────────
hist_week = _closest_history_week(history, selected_week)
hist_entry = history.get(hist_week, {})

# mass는 competitor와 동일 데이터 — competitor만 표시
_RPT_SESSIONS = ["securities", "bank", "competitor"]
all_events = []
for sk in _RPT_SESSIONS:
    for ev in (hist_entry.get(sk) or {}).get("events",{}).get("events") or []:
        ev = dict(ev); ev["_sess"] = sk; all_events.append(ev)

# marketing_history에 이벤트 없으면 channel_archive LLM 캐시 폴백
if not all_events:
    _llm_key_map = {"securities": f"sec_llm_{selected_week}", "bank": f"bank_llm_{selected_week}",
                    "competitor": f"comp_llm_{selected_week}"}
    try:
        _arch_all = json.loads(open(os.path.join(_ROOT, "channel_archive.json"), encoding="utf-8").read())
    except Exception:
        _arch_all = {}
    for sk, lk in _llm_key_map.items():
        _llm_entry = _arch_all.get(lk, {})
        _llm_raw = _llm_entry.get("raw", _llm_entry)
        for ev in (_llm_raw.get("events") or []):
            ev = dict(ev); ev["_sess"] = sk; all_events.append(ev)

# raw 데이터에서 제목 매칭으로 URL 주입 (LLM이 URL 누락한 경우 보완)
_url_by_title = {}
for sk in ["securities","bank","mass","competitor"]:
    for _ch_data in ((hist_entry.get(sk) or {}).get("raw") or {}).values():
        for _src in ["event_details","videos","articles","posts"]:
            for _item in (_ch_data.get(_src) or []):
                _t = (_item.get("title") or "").strip()
                _u = _item.get("url","") or ""
                if _t and _u.startswith("http"):
                    _url_by_title[_t] = _u
for _ev in all_events:
    if not (_ev.get("url") or "").startswith("http"):
        _match = _url_by_title.get((_ev.get("title") or "").strip())
        if _match:
            _ev["url"] = _match

krx_df = cache.get(selected_week, pd.DataFrame())
trend_df = trend_cache.get(selected_week, pd.DataFrame())

m1, m2, m3, m4 = st.columns(4)
m1.metric("마케팅 이벤트", f"{len(all_events)}건")
m2.metric("수집 채널", f"{sum(1 for sk in ['securities','bank','mass','competitor'] if hist_entry.get(sk))}개 세션")
m3.metric("ETF 종목 수", f"{len(krx_df)}개" if not krx_df.empty else "미수집")
m4.metric("수익률 데이터", f"{len(trend_df)}개" if not trend_df.empty else "미수집")

st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

# ── 탭 ───────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 시장 트렌드", "📣 마케팅 활동", "💰 수급 분석", "🏢 경쟁사 동향", "🤖 AI 인사이트"
])

# ════════════════════════════════════════════════════════
# 탭1: 시장 트렌드
# ════════════════════════════════════════════════════════
with tab1:
    if trend_df.empty:
        st.info("시장 트렌드 데이터 없음 — 시장트렌드 페이지에서 먼저 수집해주세요.")
    else:
        df_ret = trend_df.dropna(subset=["수익률_pct"]).copy()
        df_vol = trend_df[trend_df.get("거래대금_억", pd.Series([0]*len(trend_df))) > 0].copy() if "거래대금_억" in trend_df.columns else pd.DataFrame()

        c1, c2 = st.columns(2)

        # 수익률 Top10
        with c1:
            st.markdown("#### 📈 수익률 Top 10")
            if not df_ret.empty:
                top10 = df_ret.nlargest(10, "수익률_pct").reset_index(drop=True)
                prov_colors = [_prov(n)[1] for n in top10["종목명"]]
                fig = go.Figure(go.Bar(
                    x=top10["수익률_pct"],
                    y=top10["종목명"].str[:18],
                    orientation="h",
                    marker=dict(color=prov_colors, line=dict(width=0)),
                    text=[f"{v:+.2f}%" for v in top10["수익률_pct"]],
                    textposition="outside",
                    hovertemplate="%{y}<br>%{x:.2f}%<extra></extra>",
                ))
                fig.update_layout(height=340, margin=dict(l=0,r=60,t=10,b=0),
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#e8eaed", size=11),
                    xaxis=dict(showgrid=False, ticksuffix="%"),
                    yaxis=dict(autorange="reversed", showgrid=False))
                st.plotly_chart(fig, use_container_width=True)

        # 거래대금 Top10
        with c2:
            st.markdown("#### 💰 거래대금 Top 10")
            if not df_vol.empty and "거래대금_억" in df_vol.columns:
                top10v = df_vol.nlargest(10, "거래대금_억").reset_index(drop=True)
                prov_colors_v = [_prov(n)[1] for n in top10v["종목명"]]
                vol_labels = [f"{v/10000:.1f}조" if v>=10000 else f"{v:.0f}억" for v in top10v["거래대금_억"]]
                fig2 = go.Figure(go.Bar(
                    x=top10v["거래대금_억"],
                    y=top10v["종목명"].str[:18],
                    orientation="h",
                    marker=dict(color=prov_colors_v, line=dict(width=0)),
                    text=vol_labels, textposition="outside",
                    hovertemplate="%{y}<br>%{text}<extra></extra>",
                ))
                fig2.update_layout(height=340, margin=dict(l=0,r=80,t=10,b=0),
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#e8eaed", size=11),
                    xaxis=dict(showgrid=False, title="억원"),
                    yaxis=dict(autorange="reversed", showgrid=False))
                st.plotly_chart(fig2, use_container_width=True)

        # 시장요인 인사이트 (LLM)
        if not df_ret.empty:
            st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
            st.markdown("#### 💡 시장요인 인사이트")
            _insight_key = f"market_insight_{selected_week}"
            _cached_insight = _load_report_cache().get(_insight_key)
            if _cached_insight and not refresh:
                st.markdown(f'<div class="insight-box">{_cached_insight}</div>', unsafe_allow_html=True)
            else:
                if st.button("🤖 AI 시장요인 분석", key="btn_market_insight"):
                    _top5_ret = df_ret.nlargest(5, "수익률_pct")[["종목명","수익률_pct"]].values.tolist()
                    _top5_vol = df_vol.nlargest(5, "거래대금_억")[["종목명","거래대금_억"]].values.tolist() if not df_vol.empty else []
                    _ret_lines = "\n".join(f"  {n}: {v:+.2f}%" for n, v in _top5_ret)
                    _vol_lines = "\n".join(f"  {n}: {v:.0f}억" for n, v in _top5_vol)
                    from datetime import date as _date2
                    _year2 = _date2.today().year
                    _prompt = f"""삼성자산운용 KODEX ETF 마케팅 담당자를 위한 {_year2}년 {selected_week} 주간 시장 동향 분석입니다.

수익률 Top5:
{_ret_lines}

거래대금 Top5:
{_vol_lines}

위 ETF들이 이번 주 상위권에 오른 시장 요인을 2~3문장으로 설명하세요.
(금리, 섹터 이슈, 글로벌 이벤트, 정책 등 구체적으로)
마케팅 담당자 관점에서 시사점도 1문장 추가해주세요."""
                    api_key = os.getenv("ANTHROPIC_API_KEY","")
                    if api_key:
                        from llm_client import call_llm
                        with st.spinner("분석 중..."):
                            try:
                                _insight = call_llm(_prompt, anthropic_key=api_key, max_tokens=600)
                                _save_report_cache(_insight_key, _insight)
                                st.markdown(f'<div class="insight-box">{_insight}</div>', unsafe_allow_html=True)
                            except Exception as e:
                                st.error(f"실패: {e}")
                    else:
                        st.info("API 키 없음")

        # 전략 매트릭스
        if not krx_df.empty and not df_ret.empty:
            st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
            st.markdown("#### 🎯 수익률 × 순매수 전략 매트릭스")
            _num_cols = [c for c in ["금융투자","은행","개인"] if c in krx_df.columns]
            if _num_cols:
                _krx = krx_df.copy()
                _krx["순매수"] = _krx[_num_cols].fillna(0).sum(axis=1)
                _krx["종목코드"] = _krx["종목코드"].astype(str).str.strip()
                _ret = trend_df[["종목코드","종목명","수익률_pct"]].copy()
                _ret["종목코드"] = _ret["종목코드"].astype(str).str.strip()
                _mx_full = pd.merge(_krx[["종목코드","순매수"]], _ret, on="종목코드", how="inner").dropna(subset=["수익률_pct","순매수"])
                if not _mx_full.empty:
                    # app_market.py와 동일: 수익률Top10 + Bottom10 + 순매수Top10 = 최대 30개
                    _sel = set()
                    _sel.update(_mx_full.nlargest(10, "수익률_pct")["종목코드"])
                    _sel.update(_mx_full.nsmallest(10, "수익률_pct")["종목코드"])
                    _sel.update(_mx_full.nlargest(10, "순매수")["종목코드"])
                    _mx = _mx_full[_mx_full["종목코드"].isin(_sel)].copy()
                    _med_ret = _mx["수익률_pct"].median()
                    _med_net = _mx["순매수"].median()
                    def _quad(row):
                        r, n = row["수익률_pct"], row["순매수"]
                        if r>=_med_ret and n>=_med_net: return "⭐ 스타"
                        if r>=_med_ret and n<_med_net:  return "📣 공격적 마케팅"
                        if r<_med_ret  and n>=_med_net: return "📚 교육형"
                        return "🔄 리포지셔닝"
                    _mx["전략"] = _mx.apply(_quad, axis=1)
                    _mx["is_kodex"] = _mx["종목명"].str.contains("KODEX", case=False, na=False)
                    _Q_C = {"⭐ 스타":"#05b169","📣 공격적 마케팅":"#4d9fff","📚 교육형":"#f0c040","🔄 리포지셔닝":"#f43f5e"}
                    fig3 = go.Figure()
                    _bg = _mx[~_mx["is_kodex"]]
                    fig3.add_trace(go.Scatter(x=_bg["순매수"],y=_bg["수익률_pct"],mode="markers",
                        marker=dict(size=5,color="rgba(150,150,150,0.35)"),name="기타",showlegend=True,
                        hovertemplate="%{customdata[0]}<extra></extra>",customdata=_bg[["종목명"]].values))
                    _kx = _mx[_mx["is_kodex"]]
                    for strat, color in _Q_C.items():
                        _sub = _kx[_kx["전략"]==strat]
                        if _sub.empty: continue
                        fig3.add_trace(go.Scatter(x=_sub["순매수"],y=_sub["수익률_pct"],mode="markers+text",
                            marker=dict(size=10,color=color,line=dict(color="white",width=1)),
                            text=_sub["종목명"].str.replace("KODEX ","",regex=False).str[:10],
                            textposition="top center",textfont=dict(size=8,color=color),
                            name=strat,
                            hovertemplate="%{customdata[0]}<br>수익률:%{y:.2f}%<extra></extra>",
                            customdata=_sub[["종목명"]].values))
                    xpad = (_mx["순매수"].max()-_mx["순매수"].min())*0.05 or 1e6
                    ypad = (_mx["수익률_pct"].max()-_mx["수익률_pct"].min())*0.05 or 0.5
                    for x0,y0,x1,y1,fc in [
                        (_med_net,_med_ret,_mx["순매수"].max()+xpad,_mx["수익률_pct"].max()+ypad,"rgba(5,177,105,0.07)"),
                        (_mx["순매수"].min()-xpad,_med_ret,_med_net,_mx["수익률_pct"].max()+ypad,"rgba(77,159,255,0.07)"),
                        (_med_net,_mx["수익률_pct"].min()-ypad,_mx["순매수"].max()+xpad,_med_ret,"rgba(240,192,64,0.07)"),
                        (_mx["순매수"].min()-xpad,_mx["수익률_pct"].min()-ypad,_med_net,_med_ret,"rgba(244,63,94,0.07)"),
                    ]:
                        fig3.add_shape(type="rect",x0=x0,y0=y0,x1=x1,y1=y1,fillcolor=fc,line_width=0,layer="below")
                    fig3.add_shape(type="line",x0=_med_net,x1=_med_net,y0=_mx["수익률_pct"].min()-ypad,y1=_mx["수익률_pct"].max()+ypad,line=dict(color="rgba(255,255,255,0.15)",width=1,dash="dot"))
                    fig3.add_shape(type="line",y0=_med_ret,y1=_med_ret,x0=_mx["순매수"].min()-xpad,x1=_mx["순매수"].max()+xpad,line=dict(color="rgba(255,255,255,0.15)",width=1,dash="dot"))
                    fig3.update_layout(height=400,paper_bgcolor="rgba(0,0,0,0)",plot_bgcolor="rgba(20,20,30,0.5)",
                        font=dict(color="#e8eaed"),margin=dict(t=10,b=50,l=50,r=10),
                        xaxis=dict(title="순매수(천원)",gridcolor="rgba(255,255,255,0.05)",tickformat=","),
                        yaxis=dict(title="수익률(%)",gridcolor="rgba(255,255,255,0.05)",ticksuffix="%"),
                        legend=dict(orientation="h",x=0.5,y=-0.18,xanchor="center",bgcolor="rgba(0,0,0,0)"))
                    st.plotly_chart(fig3, use_container_width=True)

# ════════════════════════════════════════════════════════
# 탭2: 마케팅 활동
# ════════════════════════════════════════════════════════
with tab2:
    if not all_events:
        st.info(f"이번 주({selected_week}) 감지된 마케팅 이벤트 없음")
    else:
        # 채널별 이벤트 카드 — 3열 그리드
        for sk, slabel in _SESS_LABEL.items():
            sess_events = [e for e in all_events if e.get("_sess")==sk]
            if not sess_events: continue
            color = _SESS_COLOR[sk]
            st.markdown(f'<div style="font-size:1rem;font-weight:700;color:{color};margin:14px 0 6px;">{slabel}</div>', unsafe_allow_html=True)
            _gcols = st.columns(3)
            for i, ev in enumerate(sess_events):
                mtype = ev.get("marketing_type","기타")
                tc = _TYPE_COLOR.get(mtype,"#aaa")
                icon = _TYPE_ICON.get(mtype,"📋")
                title = _html.escape(ev.get("title","")[:60])
                period = ev.get("event_period","") or ""
                etf = ev.get("target_etf","") or ""
                summary = _html.escape((ev.get("event_summary") or "")[:100])
                url = ev.get("url","") or ""
                title_html = f'<a href="{url}" target="_blank" style="color:#e8eaed;text-decoration:none;">{title}</a>' if url.startswith("http") else title
                with _gcols[i % 3]:
                    st.markdown(
                        f'<div class="ev-card" style="border-color:{color}33;background:{color}06;height:100%;">'
                        f'<span class="ev-type" style="background:{tc}18;color:{tc};border:1px solid {tc}44;">{icon} {mtype}</span>'
                        f'<div class="ev-title">{title_html}</div>'
                        f'<div class="ev-meta">{("📅 "+period+" · ") if period else ""}{"🎯 "+etf if etf else ""}</div>'
                        f'<div style="font-size:.76rem;color:#aaa;margin-top:4px;">{summary}</div>'
                        f'</div>', unsafe_allow_html=True)

    # ── 마케팅 활동 AI 해석 ──
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    st.markdown("#### 💡 마케팅 활동 해석")
    _mktg_insight_key = f"mktg_insight_{selected_week}"
    _mktg_cached = _load_report_cache().get(_mktg_insight_key)
    if _mktg_cached and not refresh:
        st.markdown(f'<div class="insight-box">{_mktg_cached}</div>', unsafe_allow_html=True)
        _api_key_tab2 = os.getenv("ANTHROPIC_API_KEY","")
        if _api_key_tab2:
            if st.button("🔄 마케팅 해석 재생성", key="btn_mktg_regen"):
                refresh = True
                _mktg_cached = None
    if not _mktg_cached or refresh:
        _api_key_tab2 = os.getenv("ANTHROPIC_API_KEY","")
        if _api_key_tab2 and all_events:
            if st.button("🤖 마케팅 활동 해석 생성", key="btn_mktg_insight"):
                _ev_lines = []
                for _ev in all_events[:30]:
                    _ev_lines.append(f"[{_SESS_LABEL.get(_ev.get('_sess',''),'')}] {_ev.get('title','')} / {_ev.get('event_summary','')[:80]}")
                from llm_client import call_llm
                from datetime import date as _d3; _y3 = _d3.today().year
                _mp = f"""삼성자산운용 KODEX ETF 마케팅 담당자입니다.
{_y3}년 {selected_week} 주간 수집된 마케팅 채널 활동 목록입니다:

{chr(10).join(_ev_lines)}

위 마케팅 활동들을 분석하여 아래 형식으로 상세하게 작성하세요 (마크다운):
## 이번 주 마케팅 흐름
(어떤 채널에서 어떤 유형의 마케팅이 집중됐는지, 주요 이벤트·프로모션 내용 포함 4~5문장)

## 채널별 주요 활동
(증권/은행/경쟁사 채널별로 이번 주 눈에 띄는 활동 각 2~3개 구체적으로)

## KODEX 관점 시사점
(KODEX 마케팅 담당자가 이 흐름에서 읽어야 할 시사점, 대응 방향 포함 3~4문장)

실무적이고 구체적으로 작성하세요."""
                with st.spinner("해석 생성 중..."):
                    try:
                        _mi = call_llm(_mp, anthropic_key=_api_key_tab2, max_tokens=3000)
                        _save_report_cache(_mktg_insight_key, _mi)
                        st.markdown(f'<div class="insight-box">{_mi}</div>', unsafe_allow_html=True)
                    except Exception as _e:
                        st.error(f"실패: {_e}")

    # 유튜브/카카오 썸네일
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    st.markdown("#### 📺 채널별 콘텐츠 썸네일")

    thumb_found = False
    for sk, slabel in _SESS_LABEL.items():
        sess = hist_entry.get(sk) or {}
        raw = sess.get("raw") or {}
        all_videos = []
        _seen_thumbs = set()
        for ch_key, ch_data in raw.items():
            for v in ch_data.get("videos",[]):
                thumb = v.get("thumbnail","")
                if v.get("title") and thumb and thumb not in _seen_thumbs:
                    _seen_thumbs.add(thumb)
                    all_videos.append(v)
        if not all_videos: continue
        thumb_found = True
        color = _SESS_COLOR[sk]
        st.markdown(f'<div style="font-size:.9rem;font-weight:700;color:{color};margin:10px 0 6px;">{slabel}</div>', unsafe_allow_html=True)
        all_videos.sort(key=lambda v: (0 if v.get("is_etf_related") else 1))
        cols = st.columns(4)
        for i, v in enumerate(all_videos[:8]):
            with cols[i % 4]:
                url = v.get("url","#")
                thumb = v.get("thumbnail","")
                title = v.get("title","")[:50]
                st.markdown(
                    f'<div class="thumb-card">'
                    f'<a href="{url}" target="_blank"><img src="{thumb}" style="width:100%;border-radius:8px 8px 0 0;display:block;"></a>'
                    f'<div class="thumb-title">{_html.escape(title)}</div>'
                    f'</div>', unsafe_allow_html=True)
    if not thumb_found:
        st.info("이번 주 수집된 썸네일 없음")

# ════════════════════════════════════════════════════════
# 탭3: 수급 분석
# ════════════════════════════════════════════════════════
with tab3:
    if krx_df.empty:
        st.info("KRX 수급 데이터 없음")
    else:
        num_cols = [c for c in ["금융투자","은행","개인"] if c in krx_df.columns]
        col_colors = {"금융투자":"#4d9fff","은행":"#05b169","개인":"#f0c040"}

        for col in num_cols:
            st.markdown(f"#### {col} 순매수 Top 10")
            top = krx_df.nlargest(10, col)[["종목명", col]].reset_index(drop=True)
            color = col_colors.get(col, "#888")
            fig = go.Figure(go.Bar(
                x=top[col] / 1000,
                y=top["종목명"].str[:18],
                orientation="h",
                marker=dict(color=color, opacity=0.8),
                text=[f"{v/1000:,.0f}M" for v in top[col]],
                textposition="outside",
                hovertemplate="%{y}<br>%{x:,.0f}백만원<extra></extra>",
            ))
            fig.update_layout(height=280, margin=dict(l=0,r=80,t=5,b=0),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#e8eaed", size=11),
                xaxis=dict(showgrid=False, ticksuffix="M"),
                yaxis=dict(autorange="reversed", showgrid=False))
            st.plotly_chart(fig, use_container_width=True)

        # KODEX ETF만 필터 후 합산 순매수
        kodex_df = krx_df[krx_df["종목명"].str.contains("KODEX", case=False, na=False)].copy()
        if not kodex_df.empty and num_cols:
            st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
            st.markdown("#### 🔵 KODEX ETF 투자자별 수급 합산")
            totals = {col: kodex_df[col].sum() for col in num_cols}
            tc1, tc2, tc3 = st.columns(3)
            for i, (col, val) in enumerate(totals.items()):
                [tc1,tc2,tc3][i].metric(f"{col} 합산", f"{val/1e6:,.1f}억원",
                    delta_color="normal" if val > 0 else "inverse")

# ════════════════════════════════════════════════════════
# 탭4: 경쟁사 동향
# ════════════════════════════════════════════════════════
with tab4:
    comp_sess = hist_entry.get("competitor") or {}
    comp_events = (comp_sess.get("events") or {}).get("events") or []
    comp_raw = comp_sess.get("raw") or {}

    # marketing_history에 없으면 channel_archive에서 직접 읽기
    if not comp_events:
        import json as _json
        try:
            _arch_all = _json.loads(open(os.path.join(_ROOT, "channel_archive.json"), encoding="utf-8").read())
        except Exception:
            _arch_all = {}
        for _wk in [f"competitor_{selected_week}", f"competitor_{hist_week}"]:
            _arch_entry = _arch_all.get(_wk, {})
            _arch_channels = _arch_entry.get("channels", {})
            if _arch_channels:
                # channels[key]["data"]["event_details"] 구조에서 이벤트 추출
                for _ch_key, _ch_snap in _arch_channels.items():
                    _ch_data = _ch_snap.get("data") or {}
                    for _ev in _ch_data.get("event_details", []):
                        _ev2 = dict(_ev)
                        _ev2.setdefault("marketing_type", "이벤트")
                        _ev2.setdefault("channel", _ch_snap.get("channel_name", _ch_key))
                        comp_events.append(_ev2)
                if not comp_raw:
                    comp_raw = {k: v.get("data") or {} for k, v in _arch_channels.items()}
                if comp_events:
                    break

    if not comp_events and not comp_raw:
        st.info("경쟁사 채널 수집 데이터 없음")
    else:
        if comp_events:
            st.markdown("#### 🏢 경쟁사 마케팅 이벤트")
            _comp_gcols = st.columns(3)
            for _ci, ev in enumerate(comp_events):
                mtype = ev.get("marketing_type","기타")
                tc = _TYPE_COLOR.get(mtype,"#aaa")
                icon = _TYPE_ICON.get(mtype,"📋")
                title = _html.escape(ev.get("title","")[:60])
                channel = ev.get("channel","") or ev.get("provider","") or ""
                summary = _html.escape((ev.get("event_summary") or "")[:100])
                url = ev.get("url","") or ""
                _, pc = _prov(channel)
                title_html = f'<a href="{url}" target="_blank" style="color:#e8eaed;text-decoration:none;">{title}</a>' if url.startswith("http") else title
                with _comp_gcols[_ci % 3]:
                    st.markdown(
                        f'<div class="ev-card" style="border-color:{pc}33;background:{pc}06;height:100%;">'
                        f'<span class="ev-type" style="background:{tc}18;color:{tc};border:1px solid {tc}44;">{icon} {mtype}</span>'
                        f'<span style="font-size:.65rem;color:{pc};margin-left:8px;">{_html.escape(channel)}</span>'
                        f'<div class="ev-title">{title_html}</div>'
                        f'<div style="font-size:.76rem;color:#aaa;margin-top:3px;">{summary}</div>'
                        f'</div>', unsafe_allow_html=True)

        # 경쟁사 채널별 활동 요약
        if comp_raw:
            st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
            st.markdown("#### 📡 채널별 수집 현황")
            rows = []
            for ch_key, ch_data in comp_raw.items():
                ok = "✅" if ch_data.get("success") else "❌"
                vids = len(ch_data.get("videos",[]))
                snippet = (ch_data.get("snippet") or "")[:60]
                rows.append({"채널": ch_data.get("channel_name", ch_key), "상태": ok, "영상": vids, "내용": snippet})
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        # ── 경쟁사 동향 AI 해석 ──
        st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
        st.markdown("#### 💡 경쟁사 동향 해석")
        _comp_insight_key = f"comp_insight_{selected_week}"
        _comp_cached = _load_report_cache().get(_comp_insight_key)
        if _comp_cached and not refresh:
            st.markdown(f'<div class="insight-box">{_comp_cached}</div>', unsafe_allow_html=True)
        else:
            _api_key_tab4 = os.getenv("ANTHROPIC_API_KEY","")
            if _api_key_tab4 and comp_events:
                if st.button("🤖 경쟁사 동향 해석 생성", key="btn_comp_insight"):
                    _ce_lines = [f"[{e.get('channel','')}] {e.get('title','')} / {(e.get('event_summary') or '')[:60]}" for e in comp_events[:20]]
                    from llm_client import call_llm
                    from datetime import date as _d4; _y4 = _d4.today().year
                    _cp = f"""삼성자산운용 KODEX ETF 마케팅 담당자입니다.
{_y4}년 {selected_week} 주간 경쟁사(TIGER/ACE/RISE/HANARO/SOL/PLUS) 마케팅 활동입니다:

{chr(10).join(_ce_lines)}

아래를 작성하세요 (마크다운):
## 경쟁사 이번 주 핵심 움직임
(어떤 운용사가 어떤 방향으로 마케팅하고 있는지 2~3문장)

## KODEX 대응 방향
(이 경쟁 구도에서 KODEX가 취해야 할 액션 2~3가지, 구체적으로)

간결하고 실무적으로."""
                    with st.spinner("해석 생성 중..."):
                        try:
                            _ci2 = call_llm(_cp, anthropic_key=_api_key_tab4, max_tokens=600)
                            _save_report_cache(_comp_insight_key, _ci2)
                            st.markdown(f'<div class="insight-box">{_ci2}</div>', unsafe_allow_html=True)
                        except Exception as _e:
                            st.error(f"실패: {_e}")

# ════════════════════════════════════════════════════════
# 탭5: AI 인사이트
# ════════════════════════════════════════════════════════
with tab5:
    api_key = os.getenv("ANTHROPIC_API_KEY","")

    _rcache = _load_report_cache()
    _cached = _rcache.get(selected_week)

    # 캐시 있으면 API 키 없어도 항상 표시
    if _cached and not refresh:
        st.caption("📦 저장된 AI 인사이트")
        st.markdown(f'<div class="insight-box">', unsafe_allow_html=True)
        st.markdown(_cached)
        st.markdown('</div>', unsafe_allow_html=True)
        # API 키 있으면 재생성 버튼 추가 제공
        if api_key:
            if st.button("🔄 AI 인사이트 재생성", key="regen_insight"):
                refresh = True
                _cached = None

    if not _cached or refresh:
        if not api_key:
            st.info("이번 주 AI 인사이트가 아직 생성되지 않았습니다. API 키를 입력하면 생성할 수 있습니다.")
        else:
            if st.button("🤖 AI 인사이트 생성", type="primary", use_container_width=True, key="gen_insight"):
                # 데이터 요약 준비
                krx_lines = []
                if not krx_df.empty:
                    for col in [c for c in ["금융투자","은행","개인"] if c in krx_df.columns]:
                        top3 = krx_df.nlargest(3, col)[["종목명",col]].values.tolist()
                        for name, val in top3:
                            krx_lines.append(f"  [{col}] {name}: {int(val):,}천원")
                krx_text = "\n".join(krx_lines) or "데이터 없음"

                ev_lines = []
                for ev in all_events[:20]:
                    ev_lines.append(f"  [{_SESS_LABEL.get(ev.get('_sess',''),'')}] {ev.get('title','')} [{ev.get('marketing_type','')}]")
                hist_text = "\n".join(ev_lines) or "이벤트 없음"

                from llm_client import call_llm
                from datetime import date as _date
                _year = _date.today().year
                prompt = f"""삼성자산운용 KODEX ETF 마케팅 전략 AI 어시스턴트입니다.
{_year}년 {selected_week} 주간 데이터를 분석해 마케팅 담당자용 인사이트를 작성하세요.
(작성 기준: {_year}년 {selected_week})

=== KRX 투자자별 순매수 Top3 ===
{krx_text}

=== 채널별 마케팅 이벤트 ===
{hist_text}

[리포트 형식 — 마크다운]
## 이번 주 핵심 요약
(3줄 이내)

## 주목할 시장 시그널
(수급 데이터에서 읽히는 패턴)

## 채널별 마케팅 평가
(증권/은행/경쟁사 채널 활동 평가)

## 다음 주 액션 제안
(우선순위 순 3~5개)

간결하고 실무적으로 작성하세요."""

                with st.spinner("AI 분석 중..."):
                    try:
                        md = call_llm(prompt, anthropic_key=api_key, max_tokens=8000)
                        _save_report_cache(selected_week, md)
                        st.markdown(f'<div class="insight-box">', unsafe_allow_html=True)
                        st.markdown(md)
                        st.markdown('</div>', unsafe_allow_html=True)
                    except Exception as e:
                        st.error(f"생성 실패: {e}")

# ── 종합 리포트 다운로드 ──────────────────────────────────────────────────────
st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

def _build_full_report_html():
    from datetime import date as _d
    import plotly.io as pio
    parts = []
    _year = _d.today().year

    # ── CSS ──
    parts.append(f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<title>KODEX 주간 리포트 {selected_week}</title>
<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable.min.css');
body{{font-family:'Pretendard',sans-serif;max-width:1100px;margin:40px auto;padding:0 28px;color:#1a1a2e;line-height:1.7;background:#fff;}}
h1{{color:#0052ff;border-bottom:3px solid #0052ff;padding-bottom:10px;font-size:1.8rem;}}
h2{{color:#1f3c88;margin-top:40px;font-size:1.2rem;border-left:4px solid #0052ff;padding-left:12px;}}
h3{{color:#2563eb;margin-top:24px;font-size:1rem;}}
.week-badge{{display:inline-block;background:#e8f0fe;color:#0052ff;border-radius:100px;padding:4px 16px;font-size:.85rem;font-weight:700;margin-bottom:24px;}}
.section{{margin:32px 0;}}
.chart-row{{display:flex;gap:20px;flex-wrap:wrap;margin:16px 0;}}
.chart-col{{flex:1;min-width:300px;}}
.ev-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px;margin:12px 0;}}
.ev-card{{border:1px solid #dde;border-radius:10px;padding:12px 14px;background:#f8faff;}}
.ev-type{{font-size:.68rem;font-weight:700;padding:2px 8px;border-radius:100px;display:inline-block;margin-bottom:6px;background:#e8f0fe;color:#0052ff;}}
.ev-title{{font-size:.9rem;font-weight:700;color:#1a1a2e;margin:4px 0;}}
.ev-meta{{font-size:.75rem;color:#666;}}
.ev-sum{{font-size:.78rem;color:#444;margin-top:4px;}}
.sess-label{{font-size:.95rem;font-weight:700;margin:18px 0 8px;}}
table{{border-collapse:collapse;width:100%;margin:12px 0;font-size:.88rem;}}
th{{background:#0052ff;color:white;padding:8px 12px;text-align:left;}}
td{{border:1px solid #dde;padding:8px 12px;}}
tr:nth-child(even){{background:#f5f8ff;}}
.insight-box{{background:linear-gradient(135deg,#e8f0fe,#ede9fe);border:1px solid #a5b4fc;border-radius:14px;padding:24px 28px;margin:16px 0;}}
.insight-box h2,.insight-box h3{{color:#1e40af;border:none;padding:0;}}
.divider{{height:1px;background:#e5e7eb;margin:32px 0;}}
.footer{{text-align:center;color:#888;font-size:.8rem;margin-top:48px;padding-top:16px;border-top:1px solid #e5e7eb;}}
</style></head><body>
<h1>📊 KODEX ETF 주간 마케팅 리포트</h1>
<span class="week-badge">📅 {_year}년 {selected_week}</span>
""")

    # ── 섹션1: 시장 트렌드 ──
    parts.append('<div class="section"><h2>📊 시장 트렌드</h2>')
    if not trend_df.empty and "수익률_pct" in trend_df.columns:
        df_r = trend_df.dropna(subset=["수익률_pct"])
        top10r = df_r.nlargest(10,"수익률_pct")
        fig_r = go.Figure(go.Bar(
            x=top10r["수익률_pct"], y=top10r["종목명"].str[:18],
            orientation="h", marker=dict(color="#0052ff",opacity=0.8),
            text=[f"{v:+.2f}%" for v in top10r["수익률_pct"]], textposition="outside"))
        fig_r.update_layout(height=320,margin=dict(l=0,r=60,t=30,b=0),
            title="수익률 Top 10",paper_bgcolor="white",plot_bgcolor="white",
            font=dict(color="#1a1a2e"),xaxis=dict(ticksuffix="%"),yaxis=dict(autorange="reversed"))
        parts.append('<div class="chart-row">')
        parts.append(f'<div class="chart-col">{pio.to_html(fig_r,full_html=False,include_plotlyjs=False)}</div>')

        if "거래대금_억" in trend_df.columns:
            df_v = trend_df[trend_df["거래대금_억"]>0]
            top10v = df_v.nlargest(10,"거래대금_억")
            vol_labels = [f"{v/10000:.1f}조" if v>=10000 else f"{v:.0f}억" for v in top10v["거래대금_억"]]
            fig_v = go.Figure(go.Bar(
                x=top10v["거래대금_억"], y=top10v["종목명"].str[:18],
                orientation="h", marker=dict(color="#10b981",opacity=0.8),
                text=vol_labels, textposition="outside"))
            fig_v.update_layout(height=320,margin=dict(l=0,r=80,t=30,b=0),
                title="거래대금 Top 10",paper_bgcolor="white",plot_bgcolor="white",
                font=dict(color="#1a1a2e"),xaxis=dict(title="억원"),yaxis=dict(autorange="reversed"))
            parts.append(f'<div class="chart-col">{pio.to_html(fig_v,full_html=False,include_plotlyjs=False)}</div>')
        parts.append('</div>')

        _mi = _load_report_cache().get(f"market_insight_{selected_week}","")
        if _mi:
            parts.append(f'<div class="insight-box"><strong>💡 시장요인 인사이트</strong><br>{_html.escape(_mi)}</div>')
    else:
        parts.append("<p>시장 트렌드 데이터 없음</p>")
    parts.append('</div><div class="divider"></div>')

    # ── 섹션2: 마케팅 활동 ──
    parts.append('<div class="section"><h2>📣 마케팅 활동</h2>')
    _SESS_COLOR_HEX = {"securities":"#0052ff","bank":"#10b981","mass":"#f59e0b","competitor":"#f43f5e"}
    if all_events:
        for sk, slabel in _SESS_LABEL.items():
            sevs = [e for e in all_events if e.get("_sess")==sk]
            if not sevs: continue
            c = _SESS_COLOR_HEX.get(sk,"#888")
            parts.append(f'<div class="sess-label" style="color:{c};">{slabel}</div><div class="ev-grid">')
            for ev in sevs:
                mtype = _html.escape(ev.get("marketing_type","기타"))
                title = _html.escape(ev.get("title","")[:60])
                period = _html.escape(ev.get("event_period","") or "")
                etf = _html.escape(ev.get("target_etf","") or "")
                summ = _html.escape((ev.get("event_summary") or "")[:120])
                url = ev.get("url","") or ""
                t_html = f'<a href="{url}" target="_blank" style="color:#1a1a2e;text-decoration:none;">{title}</a>' if url.startswith("http") else title
                parts.append(f'<div class="ev-card"><span class="ev-type">{mtype}</span>'
                              f'<div class="ev-title">{t_html}</div>'
                              f'<div class="ev-meta">{("📅 "+period+" · ") if period else ""}{"🎯 "+etf if etf else ""}</div>'
                              f'<div class="ev-sum">{summ}</div></div>')
            parts.append('</div>')
    else:
        parts.append("<p>이번 주 감지된 마케팅 이벤트 없음</p>")
    _mi2 = _load_report_cache().get(f"mktg_insight_{selected_week}","")
    if _mi2:
        parts.append(f'<div class="insight-box"><strong>💡 마케팅 활동 해석</strong><br>{_mi2}</div>')
    parts.append('</div><div class="divider"></div>')

    # ── 섹션3: 수급 분석 ──
    parts.append('<div class="section"><h2>💰 수급 분석</h2>')
    if not krx_df.empty:
        num_cols = [c for c in ["금융투자","은행","개인"] if c in krx_df.columns]
        col_colors = {"금융투자":"#0052ff","은행":"#10b981","개인":"#f59e0b"}
        parts.append('<div class="chart-row">')
        for col in num_cols:
            top = krx_df.nlargest(10,col)[["종목명",col]].reset_index(drop=True)
            fig_s = go.Figure(go.Bar(
                x=top[col]/1000, y=top["종목명"].str[:18], orientation="h",
                marker=dict(color=col_colors.get(col,"#888"),opacity=0.8),
                text=[f"{v/1000:,.0f}M" for v in top[col]], textposition="outside"))
            fig_s.update_layout(height=280,margin=dict(l=0,r=80,t=30,b=0),
                title=f"{col} 순매수 Top 10",paper_bgcolor="white",plot_bgcolor="white",
                font=dict(color="#1a1a2e"),xaxis=dict(title="백만원"),yaxis=dict(autorange="reversed"))
            parts.append(f'<div class="chart-col">{pio.to_html(fig_s,full_html=False,include_plotlyjs=False)}</div>')
        parts.append('</div>')
        kodex = krx_df[krx_df["종목명"].str.contains("KODEX",case=False,na=False)]
        if not kodex.empty and num_cols:
            parts.append('<h3>🔵 KODEX ETF 투자자별 순매수 합산</h3><table><tr>'+''.join(f'<th>{c}</th>' for c in num_cols)+'</tr><tr>'
                +''.join(f'<td>{kodex[c].sum()/1e6:,.1f}억원</td>' for c in num_cols)+'</tr></table>')
    else:
        parts.append("<p>KRX 수급 데이터 없음</p>")
    parts.append('</div><div class="divider"></div>')

    # ── 섹션4: 경쟁사 동향 ── (marketing_history + channel_archive 둘 다 확인)
    parts.append('<div class="section"><h2>🏢 경쟁사 동향</h2>')
    comp_evs = [e for e in all_events if e.get("_sess")=="competitor"]
    if not comp_evs:
        try:
            import json as _j2
            _arch2 = _j2.loads(open(os.path.join(_ROOT, "channel_archive.json"), encoding="utf-8").read())
            for _wk2 in [f"competitor_{selected_week}", f"competitor_{hist_week}"]:
                _ae = _arch2.get(_wk2, {})
                for _ck, _cv in _ae.get("channels", {}).items():
                    for _ev2 in (_cv.get("data") or {}).get("event_details", []):
                        _e2 = dict(_ev2)
                        _e2.setdefault("marketing_type", "이벤트")
                        _e2.setdefault("channel", _cv.get("channel_name", _ck))
                        comp_evs.append(_e2)
                if comp_evs: break
        except Exception:
            pass
    if comp_evs:
        parts.append('<div class="ev-grid">')
        for ev in comp_evs:
            ch = _html.escape(ev.get("channel","") or ev.get("provider","") or "")
            mtype = _html.escape(ev.get("marketing_type","기타"))
            title = _html.escape(ev.get("title","")[:60])
            summ = _html.escape((ev.get("event_summary") or "")[:100])
            url = ev.get("url","") or ""
            t_html = f'<a href="{url}" target="_blank" style="color:#1a1a2e;text-decoration:none;">{title}</a>' if url.startswith("http") else title
            parts.append(f'<div class="ev-card"><span class="ev-type">{mtype}</span>'
                          f'<span style="font-size:.7rem;color:#f43f5e;margin-left:6px;">{ch}</span>'
                          f'<div class="ev-title">{t_html}</div>'
                          f'<div class="ev-sum">{summ}</div></div>')
        parts.append('</div>')
    else:
        parts.append("<p>경쟁사 이벤트 데이터 없음</p>")
    _ci3 = _load_report_cache().get(f"comp_insight_{selected_week}","")
    if _ci3:
        parts.append(f'<div class="insight-box"><strong>💡 경쟁사 동향 해석</strong><br>{_ci3}</div>')
    parts.append('</div><div class="divider"></div>')

    # ── 섹션5: AI 인사이트 ──
    _ai_md = _load_report_cache().get(selected_week,"")
    if _ai_md:
        parts.append('<div class="section"><h2>🤖 AI 인사이트</h2><div class="insight-box">')
        try:
            import markdown as _mdlib
            parts.append(_mdlib.markdown(_ai_md, extensions=["tables","fenced_code"]))
        except:
            parts.append(f"<pre>{_html.escape(_ai_md)}</pre>")
        parts.append('</div></div>')

    parts.append(f'<div class="footer">KODEX ETF 마케팅 모니터링 AI Agent · Powered by Claude · {_year}</div>')
    parts.append('</body></html>')
    return "".join(parts)

if st.button("📥 종합 리포트 HTML 다운로드", type="primary", use_container_width=True, key="dl_full"):
    with st.spinner("리포트 생성 중..."):
        _full_html = _build_full_report_html()
    st.download_button(
        "⬇️ 다운로드",
        data=_full_html.encode("utf-8"),
        file_name=f"kodex_weekly_report_{selected_week.replace('.','_')}.html",
        mime="text/html",
        use_container_width=True,
        key="dl_full_btn"
    )
