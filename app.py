"""
KODEX ETF 마케팅 효과 측정 AI Agent — Streamlit 메인 앱
마케팅 감지 → ETF 특정 → 자동 비교군 매핑 → DiD 계산 과정을 투명하게 표시
"""

import base64
import io
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, date

# .env 파일 로드 (KRX_ID, KRX_PW 등)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

st.set_page_config(
    page_title="ETF 마케팅 효과 측정 AI Agent",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* ── Pretendard 폰트 로드 ── */
@font-face {
    font-family: 'Pretendard';
    src: url('app/static/PretendardVariable.woff2') format('woff2-variations');
    font-weight: 100 900;
    font-style: normal;
}
/* CDN fallback */
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable.min.css');

body, p, h1, h2, h3, h4, h5, h6,
.stMarkdown, .stText, .stCaption,
.stButton > button, input, select, label {
    font-family: 'Pretendard', 'Pretendard Variable', -apple-system, BlinkMacSystemFont,
                 'Segoe UI', sans-serif !important;
}

/* ── Coinbase-inspired design system ── */
.step-header {
    font-size:1rem; font-weight:600; color:#ffffff;
    border-left:3px solid #0052ff; padding-left:12px; margin:20px 0 10px;
    letter-spacing:-0.01em;
}
.formula-box {
    border:1px solid rgba(0,82,255,0.2); border-radius:12px;
    padding:16px 20px; font-family:'Pretendard','JetBrains Mono','D2Coding','Courier New',monospace; font-size:0.85rem;
    white-space:pre-wrap; margin:10px 0;
    background:#16181c; color:#e8eaed;
}
.comp-grid { display:flex; gap:12px; margin:12px 0; flex-wrap:nowrap; }
.did-result { font-size:1.4rem; font-weight:700; padding:6px 0; font-family:'Pretendard','JetBrains Mono','D2Coding','Courier New',monospace; }
.badge-lp  { background:rgba(244,176,0,0.15); color:#f4b000; padding:3px 10px;
              border-radius:100px; font-size:0.72rem; font-weight:600; border:1px solid rgba(244,176,0,0.3); }
.badge-ok  { background:rgba(5,177,105,0.12); color:#05b169; padding:3px 10px;
              border-radius:100px; font-size:0.72rem; font-weight:600; border:1px solid rgba(5,177,105,0.3); }
.mode-badge-weekly { background:rgba(0,82,255,0.1); border:1px solid rgba(0,82,255,0.3);
                     color:#0052ff; padding:5px 16px; border-radius:100px;
                     font-weight:600; font-size:0.88rem; display:inline-block; }
/* Streamlit button override → Coinbase Blue pill */
.stButton > button[kind="primary"] {
    background:#0052ff !important; color:#fff !important;
    border-radius:100px !important; border:none !important;
    font-weight:600 !important; letter-spacing:0.01em !important;
}
.stButton > button[kind="primary"]:hover { background:#003ecc !important; }
/* Number emphasis */
.num { font-family:'Pretendard','JetBrains Mono','D2Coding','Courier New',monospace; font-weight:500; }
/* Provider colored badges */
.prov-badge {
    display:inline-flex; align-items:center; justify-content:center;
    width:32px; height:32px; border-radius:9999px;
    font-size:0.7rem; font-weight:700; flex-shrink:0;
}
.ch-pill { display:inline-block; padding:2px 8px; border-radius:100px; font-size:0.72rem;
           font-weight:600; margin:2px; }
.ch-ok   { background:rgba(5,177,105,0.12); color:#05b169; border:1px solid rgba(5,177,105,0.3); }
.ch-fail { background:rgba(207,32,47,0.1); color:#cf202f; border:1px solid rgba(207,32,47,0.25); }

/* 공룡 달리기 애니메이션 */
@keyframes dino-run {
  0%   { transform: translateX(0) scaleX(1); }
  49%  { transform: translateX(calc(var(--track-width, 300px) - 48px)) scaleX(1); }
  50%  { transform: translateX(calc(var(--track-width, 300px) - 48px)) scaleX(-1); }
  99%  { transform: translateX(0) scaleX(-1); }
  100% { transform: translateX(0) scaleX(1); }
}
@keyframes dino-sweat {
  0%,100% { opacity:0; transform:translateY(0); }
  50% { opacity:1; transform:translateY(6px); }
}
.dino-track {
    position:relative; height:52px; overflow:hidden;
    background:rgba(255,255,255,0.05); border-radius:8px; margin:8px 0;
}
.dino-char {
    position:absolute; font-size:2rem; top:4px; left:0;
    animation: dino-run 2.4s linear infinite;
}
.dino-dust {
    position:absolute; font-size:0.7rem; top:32px; left:0;
    animation: dino-run 2.4s linear infinite;
    opacity:0.5;
}
/* ── 이벤트 카드 보드 (증권/개인/은행/히스토리 공용) ── */
.ev-board { display:flex; gap:14px; flex-wrap:wrap; margin:14px 0 20px; }
.ev-card {
    flex:1; min-width:240px; max-width:340px;
    border-radius:14px; padding:0; overflow:hidden;
    border:1px solid rgba(255,255,255,0.08);
    background:rgba(255,255,255,0.03);
    transition:transform .15s, background .15s;
}
.ev-card:hover { transform:translateY(-2px); background:rgba(255,255,255,0.06); }
.ev-card-img {
    width:100%; height:120px; object-fit:cover;
    display:block; background:linear-gradient(135deg,#1a1d23,#16181c);
}
.ev-card-img-placeholder {
    width:100%; height:80px;
    background:linear-gradient(135deg,rgba(0,82,255,0.15),rgba(77,159,255,0.08));
    display:flex; align-items:center; justify-content:center;
    font-size:2rem;
}
.ev-card-body { padding:12px 14px; }
.ev-card-type {
    font-size:.63rem; font-weight:700; padding:2px 8px; border-radius:100px;
    display:inline-block; margin-bottom:6px;
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
.ev-period { font-size:.72rem; color:#4d9fff; margin:3px 0; }
.ev-etf    { font-size:.70rem; margin:3px 0; }
.ev-summary { font-size:.77rem; color:#aaa; line-height:1.5; margin:6px 0 0; }
.ev-channel { font-size:.65rem; color:#555; margin-top:6px; }
</style>
""", unsafe_allow_html=True)

# ── 모드 선택 랜딩 페이지 ─────────────────────────────────────────────────────
if "selected_mode" not in st.session_state:
    st.session_state.selected_mode = None

if st.session_state.selected_mode is None:
    st.markdown("""
    <style>
    .landing-title { font-size:2.2rem; font-weight:800; text-align:center; margin-bottom:0.3rem; }
    .landing-sub   { font-size:1rem; text-align:center; opacity:.65; margin-bottom:2.5rem; }
    .mode-card {
        border:2px solid rgba(255,255,255,0.12);
        border-radius:16px;
        padding:2rem 1.5rem;
        text-align:center;
        cursor:pointer;
        transition:all .2s;
        height:260px;
        display:flex; flex-direction:column; align-items:center; justify-content:center;
        gap:0.7rem;
    }
    .mode-card:hover { border-color:#4d9fff; background:rgba(77,159,255,0.08); }
    .mode-card.disabled {
        opacity:.4;
        cursor:not-allowed;
        border-color:rgba(255,255,255,0.06);
    }
    .mode-icon  { font-size:3rem; }
    .mode-title { font-size:1.2rem; font-weight:700; }
    .mode-desc  { font-size:0.82rem; opacity:.7; line-height:1.5; }
    .coming-soon{
        background:rgba(255,200,50,0.15); border:1px solid rgba(255,200,50,0.4);
        color:#f0c040; border-radius:20px; padding:3px 12px;
        font-size:0.72rem; font-weight:700; margin-top:0.3rem;
    }
    </style>
    """, unsafe_allow_html=True)

    st.markdown('<div class="landing-title">📊 ETF 마케팅 모니터링 AI Agent</div>', unsafe_allow_html=True)
    st.markdown('<div class="landing-sub">채널별 마케팅 활동을 자동 감지하고 이중차분법(DiD)으로 순매수 효과를 정량 측정합니다</div>', unsafe_allow_html=True)

    # ── 1행: 구현 완료 3개 ──
    col1, col2, col3 = st.columns(3, gap="large")

    with col1:
        st.markdown("""
        <div class="mode-card">
            <div class="mode-icon">📈</div>
            <div class="mode-title">증권 채널</div>
            <div class="mode-desc">삼성·미래에셋·키움·한투·신한·KB 유튜브·이벤트·카카오 수집 → 금융투자 순매수 DiD + Z-score 측정</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("증권 채널 →", key="btn_securities", use_container_width=True, type="primary"):
            st.session_state.selected_mode = "securities"
            st.session_state["analysis_run"] = False
            st.rerun()

    with col2:
        st.markdown("""
        <div class="mode-card">
            <div class="mode-icon">🏦</div>
            <div class="mode-title">은행 채널</div>
            <div class="mode-desc">KB·신한·하나·우리·농협 유튜브·블로그·카카오 수집 → 은행 순매수 AUM DiD + Z-score 역방향 이상 감지</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("은행 채널 →", key="btn_bank", use_container_width=True, type="primary"):
            st.session_state.selected_mode = "bank"
            st.rerun()

    with col3:
        st.markdown("""
        <div class="mode-card">
            <div class="mode-icon">🎯</div>
            <div class="mode-title">개인 채널</div>
            <div class="mode-desc">KODEX 유튜브·공식블로그·카카오·이벤트·뉴스 수집 → 개인 순매수 DiD로 대고객 마케팅 효과 측정</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("개인 채널 →", key="btn_mass", use_container_width=True, type="primary"):
            st.session_state.selected_mode = "mass"
            st.rerun()

    st.markdown("<div style='margin:8px 0;'></div>", unsafe_allow_html=True)

    # ── 2행: 추후 출시 3개 ──
    col4, col5, col6 = st.columns(3, gap="large")

    with col4:
        st.markdown("""
        <div class="mode-card">
            <div class="mode-icon">📊</div>
            <div class="mode-title">ETF 시장 트렌드</div>
            <div class="mode-desc">국내 ETF 수익률·거래대금 Top 10 + 운용사별 점유율 + 수익률×순매수 전략 매트릭스</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("시장 트렌드 →", key="btn_trend", use_container_width=True, type="primary"):
            st.session_state.selected_mode = "trend"
            st.rerun()

    with col5:
        st.markdown("""
        <div class="mode-card">
            <div class="mode-icon">🏢</div>
            <div class="mode-title">경쟁사 채널</div>
            <div class="mode-desc">TIGER·ACE·RISE·HANARO·SOL·PLUS 유튜브·블로그·카카오 수집 + 주차별 전체 채널 히스토리·백테스트·이벤트 캘린더</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("경쟁사 채널 →", key="btn_competitor", use_container_width=True, type="primary"):
            st.session_state.selected_mode = "competitor"
            st.rerun()

    with col6:
        st.markdown("""
        <div class="mode-card">
            <div class="mode-icon">🏷️</div>
            <div class="mode-title">ETF 사후관리</div>
            <div class="mode-desc">신규상장 감지 · 상폐 LLM 자동검증 · 만기청산 분류 · 뉴스·DART 공시 연동</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("ETF 사후관리 →", key="btn_lifecycle", use_container_width=True, type="primary"):
            st.session_state.selected_mode = "lifecycle"
            st.rerun()

    # ── 전체 통합 수집 ───────────────────────────────────────────────────────────
    st.markdown("<div style='margin:24px 0 8px;'></div>", unsafe_allow_html=True)
    st.markdown("""
    <style>
    .collect-banner {
        border: 1px solid rgba(0,198,100,0.35);
        border-radius: 14px;
        background: linear-gradient(90deg, rgba(0,198,100,0.06) 0%, rgba(0,82,255,0.06) 100%);
        padding: 18px 24px;
        margin-bottom: 8px;
        display: flex; align-items: center; gap: 16px;
    }
    .collect-banner-icon { font-size: 2rem; }
    .collect-banner-text { flex: 1; }
    .collect-banner-title { font-weight: 700; font-size: 1rem; margin-bottom: 2px; }
    .collect-banner-desc  { font-size: 0.82rem; opacity: .65; }
    .krx-banner {
        border: 1px solid rgba(255,170,0,0.35);
        border-radius: 14px;
        background: linear-gradient(90deg, rgba(255,170,0,0.06) 0%, rgba(255,80,0,0.04) 100%);
        padding: 18px 24px;
        margin-bottom: 8px;
        display: flex; align-items: center; gap: 16px;
    }
    </style>
    <div class="collect-banner">
        <div class="collect-banner-icon">🔄</div>
        <div class="collect-banner-text">
            <div class="collect-banner-title">전체 수집</div>
            <div class="collect-banner-desc">증권 · 은행 · 개인 · 경쟁사 마케팅 채널 수집 + 시장 트렌드 + 사후관리 — 주차 선택 후 한 번에 전부</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # 주차 선택
    from datetime import date as _date, timedelta as _td
    _today = _date.today()
    _week_opts = {}
    for _i in range(12):  # 최근 12주
        _m = _today - _td(days=_today.weekday()) - _td(weeks=_i)
        _f = _m + _td(days=4)
        _l = f"{_m.month}.{_m.day}-{_f.month}.{_f.day}"
        _week_opts[_l] = (_m, _f)
    _week_labels = list(_week_opts.keys())
    _sel_week = st.selectbox("수집 주차 선택", _week_labels, index=0, key="collect_week_select")
    _sel_mon, _sel_fri = _week_opts[_sel_week]
    _lbl = _sel_week

    if st.button(f"🔄  {_lbl} 전체 수집 — 증권·은행·개인·경쟁사·시장트렌드·사후관리", key="btn_collect_all", use_container_width=True):
        import scheduled_collect as _sc
        from datetime import datetime as _dt

        # 1. 마케팅 채널 4개 세션
        with st.spinner(f"[1/3] {_lbl} 마케팅 채널 수집 중..."):
            try:
                # run()은 항상 이번 주 기준이라 직접 호출
                _wstart = _dt(_sel_mon.year, _sel_mon.month, _sel_mon.day)
                _wend   = _dt(_sel_fri.year, _sel_fri.month, _sel_fri.day, 23, 59)
                _sc.run_for_week(_lbl, _wstart, _wend) if hasattr(_sc, 'run_for_week') else _sc.run()
                st.caption("✅ 마케팅 채널 완료")
            except Exception as _e:
                st.caption(f"⚠️ 마케팅 채널 오류: {_e}")

        # 2. 시장 트렌드 (네이버 금융)
        with st.spinner("[2/3] 시장 트렌드 수집 중..."):
            try:
                from krx_data_fetcher import fetch_etf_market_summary_naver, load_trend_cache, save_trend_cache
                _tc = load_trend_cache()
                if _lbl not in _tc:
                    _trend_df = fetch_etf_market_summary_naver()
                    if not _trend_df.empty:
                        save_trend_cache(_lbl, _trend_df)
                        st.caption(f"✅ 시장 트렌드 완료 ({len(_trend_df)}개 ETF)")
                    else:
                        st.caption("⚠️ 시장 트렌드 수집 실패")
                else:
                    st.caption(f"📦 {_lbl} 시장 트렌드 캐시 사용")
            except Exception as _e:
                st.caption(f"⚠️ 시장 트렌드 오류: {_e}")

        # 3. 사후관리 (상폐/신규상장 뉴스)
        with st.spinner("[3/3] 사후관리 수집 중..."):
            try:
                from dart_lifecycle import collect_lifecycle as _lc_run
                _lc_run(days=7)
                st.caption("✅ 사후관리 완료")
            except Exception as _e:
                st.caption(f"⚠️ 사후관리 오류: {_e}")

        st.success(f"✅ {_lbl} 전체 수집 완료 — 각 세션에서 버튼 없이 결과가 바로 표시됩니다.")

    # ── KRX 순매수 데이터 수집 (VPN 필수) ────────────────────────────────────────
    st.markdown("""
    <div class="krx-banner">
        <div class="collect-banner-icon">📊</div>
        <div class="collect-banner-text">
            <div class="collect-banner-title">KRX 순매수 데이터 수집</div>
            <div class="collect-banner-desc">⚠️ VPN 필수 — KRX 망 접속 필요. DiD 분석의 기반 데이터 (주 1회, 금요일 장 마감 후)</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    _krx_id = os.getenv("KRX_ID", "")
    if _krx_id:
        from datetime import date as _kdate, timedelta as _ktd
        _ktoday  = _kdate.today()
        _kmon    = _ktoday - _ktd(days=_ktoday.weekday())
        _kfri    = _kmon + _ktd(days=4)
        _kweeks  = {}
        for _ki in range(8):
            _kws = _kmon - _ktd(weeks=_ki)
            _kwe = _kws + _ktd(days=4)
            _klbl = f"{_kws.month}.{_kws.day}-{_kwe.month}.{_kwe.day}"
            _kweeks[_klbl] = (_kws, _kwe)
        _kcol_w, _kcol_btn = st.columns([3, 1])
        _ksel = _kcol_w.selectbox("수집 주차", list(_kweeks.keys()), index=0, key="landing_krx_week")
        _kstart, _kend = _kweeks[_ksel]
        if _kcol_btn.button("📊 KRX 수집", type="primary", use_container_width=True, key="landing_krx_btn"):
            try:
                from krx_data_fetcher import fetch_weekly_etf_data, load_cache, save_cache
                with st.spinner(f"KRX 수집 중... {_ksel} (수분 소요, VPN 연결 확인)"):
                    _knew = fetch_weekly_etf_data(_kstart, _kend)
                if not _knew.empty:
                    _kexist = load_cache()
                    _kexist[_ksel] = _knew
                    save_cache(_kexist)
                    st.success(f"✅ KRX {_ksel} 수집 완료 — {len(_knew)}개 ETF 저장됨")
                else:
                    st.error("수집된 데이터 없음 — VPN 연결 및 날짜 확인")
            except Exception as _ke:
                st.error(f"KRX 수집 실패: {_ke}")
    else:
        st.warning("KRX 계정 미설정 — `.env`에 `KRX_ID` / `KRX_PW` 를 추가하면 사용 가능합니다.")

    st.markdown("<div style='margin:16px 0 4px;'></div>", unsafe_allow_html=True)

    st.markdown("<div style='margin:4px 0;'></div>", unsafe_allow_html=True)
    st.markdown("""
    <style>
    div[data-testid="stButton"] button[kind="secondary"].report-bar {
        background: linear-gradient(90deg, #1a1f3a 0%, #0d1226 100%) !important;
        border: 1px solid rgba(59,130,246,0.35) !important;
        border-radius: 14px !important;
        padding: 18px 32px !important;
        font-size: 1.05rem !important;
        font-weight: 700 !important;
        color: #93c5fd !important;
        letter-spacing: 0.02em !important;
    }
    </style>
    """, unsafe_allow_html=True)
    if st.button(
        "📋  주간 종합 리포트  —  6개 채널 데이터를 통합 분석해 마케팅 인사이트 & 액션 제안을 생성합니다",
        key="btn_report",
        use_container_width=True,
    ):
        st.session_state.selected_mode = "report"
        st.rerun()

    st.markdown("---")
    st.caption("삼성자산운용 ETF 마케팅 모니터링 AI Agent · Powered by Claude")
    st.stop()

# ETF 사후관리 모드
if st.session_state.selected_mode == "lifecycle":
    with st.sidebar:
        if st.button("← 채널 선택", key="back_lifecycle"):
            st.session_state.selected_mode = None
            st.rerun()
    exec(open(os.path.join(os.path.dirname(__file__), "agents/lifecycle/app_lifecycle.py"), encoding="utf-8").read())
    st.stop()

# 주간 종합 리포트 모드
if st.session_state.selected_mode == "report":
    with st.sidebar:
        if st.button("← 채널 선택", key="back_report"):
            st.session_state.selected_mode = None
            st.rerun()
    exec(open(os.path.join(os.path.dirname(__file__), "report_weekly.py"), encoding="utf-8").read())
    st.stop()

# 개인 채널 모드
if st.session_state.selected_mode == "mass":
    with st.sidebar:
        if st.button("← 채널 선택", key="back_mass"):
            st.session_state.selected_mode = None
            st.rerun()
    exec(open(os.path.join(os.path.dirname(__file__), "agents/mass/app_mass.py"), encoding="utf-8").read())
    st.stop()

# 경쟁사 채널 모드
if st.session_state.selected_mode == "competitor":
    with st.sidebar:
        if st.button("← 채널 선택", key="back_competitor"):
            st.session_state.selected_mode = None
            st.rerun()
    exec(open(os.path.join(os.path.dirname(__file__), "agents/competitor/app_competitor.py"), encoding="utf-8").read())
    st.stop()

# ETF 시장 트렌드 모드
if st.session_state.selected_mode == "trend":
    with st.sidebar:
        if st.button("← 채널 선택", key="back_trend"):
            st.session_state.selected_mode = None
            st.rerun()
    exec(open(os.path.join(os.path.dirname(__file__), "agents/market/app_market.py"), encoding="utf-8").read())
    st.stop()

# 은행 모드
if st.session_state.selected_mode == "bank":
    with st.sidebar:
        if st.button("← 채널 선택", key="back_bank"):
            st.session_state.selected_mode = None
            st.rerun()
    exec(open(os.path.join(os.path.dirname(__file__), "agents/bank/app_bank.py"), encoding="utf-8").read())
    st.stop()

# 증권사 모드 선택됨 → 사이드바에 뒤로가기 추가
with st.sidebar:
    if st.button("← 채널 선택"):
        st.session_state.selected_mode = None
        st.session_state["analysis_run"] = False
        st.rerun()
    st.divider()

# ── 사이드바 ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 설정")
    anthropic_key = st.text_input(
        "Anthropic API Key",
        value=os.getenv("ANTHROPIC_API_KEY", ""),
        type="password",
        help="마케팅 감지 정확도 향상. 없으면 키워드 방식으로 대체 (선택사항)"
    )
    youtube_key  = os.getenv("YOUTUBE_API_KEY", "")
    naver_id     = os.getenv("NAVER_CLIENT_ID", "")
    naver_secret = os.getenv("NAVER_CLIENT_SECRET", "")
    st.divider()
    st.markdown("""
**분석 흐름**
1. 🔍 11개 채널 마케팅 수집
2. 🤖 LLM으로 대상 ETF 특정
3. 🔗 비교군 자동 매핑
4. 📊 베이스라인 + LP 감지
5. 🧮 DiD 계산
6. 📄 리포트 생성
""")

# ── 헬퍼 ─────────────────────────────────────────────────────────────────────
def did_pct(v: float) -> str:
    """Z-score 표시."""
    return f"Z={v:+.2f}"

def score_label(res) -> str:
    """마케팅 점수 표시."""
    score  = float(getattr(res, 'marketing_score', None) or 0.0)
    zscore = float(getattr(res, 'zscore', None) or 0.0)
    # 점수가 유효하게 산출된 경우 (50점 고착이 아닌 경우)
    if zscore != 0.0 or score not in (0.0, 50.0):
        return f"{score:.0f}점"
    raw = getattr(res, 'raw_did_value', None)
    if raw is not None and raw != 0.0:
        return f"산출중 ({float(raw)*100:+.0f}%)"
    return "산출중"

def _parse_sheet_dates(sheet_name: str):
    """시트명에서 시작/종료 날짜 추출. 예: '5.25-5.28' → (date(5/25), date(5/28))"""
    from datetime import date
    m = re.findall(r"(\d{1,2})[.\-](\d{1,2})", sheet_name)
    if not m:
        return None, None
    today = datetime.now()
    def to_date(mon, day):
        month, day = int(mon), int(day)
        year = today.year if month <= today.month else today.year - 1
        try:
            return date(year, month, day)
        except Exception:
            return None
    start = to_date(*m[0])
    end   = to_date(*m[-1]) if len(m) > 1 else start
    return start, end

def _sheet_label(name: str) -> str:
    start, end = _parse_sheet_dates(name)
    if start is None:
        return name
    days = (datetime.now().date() - start).days
    # KRX 데이터는 월~금 — 끝날이 목요일이면 금요일로 보정
    try:
        from datetime import timedelta as _td
        if end and end.weekday() == 3:
            real_end = end + _td(days=1)
            display = f"{start.month}.{start.day}-{real_end.month}.{real_end.day}"
        else:
            display = name
    except Exception:
        display = name
    if days == 0:
        return f"{display}  (이번 주)"
    return f"{display}  ({days}일 전)"

def summarize_channel(r) -> str:
    if not r.data: return "데이터 없음"
    d = r.data
    if "videos"   in d: return f"영상 {len(d['videos'])}개 (ETF관련 {sum(1 for v in d['videos'] if v.get('is_etf_related'))}개)"
    if "articles" in d: return f"기사 {len(d['articles'])}건"
    if "events"   in d: return f"이벤트 {len(d['events'])}건"
    if "trends"   in d:
        parts = []
        for k, v in d["trends"].items():
            if isinstance(v, dict):
                if "ratio" in v: parts.append(f"{k}: {v['ratio']:.0f}({v.get('change',0):+.0f})")
                elif "etf_hits" in v: parts.append(f"인기검색어 ETF포함:{len(v.get('etf_hits',[]))}")
        return " | ".join(parts) if parts else "트렌드 데이터 수집"
    if "news"     in d: return f"보도자료 {len(d['news'])}건"
    if "posts"    in d: return f"게시물 {len(d['posts'])}건"
    return "수집 완료"

def keyword_fallback(collection_results, all_kodex_etfs: dict) -> dict:
    """
    all_kodex_etfs: {종목코드: 종목명} — 엑셀에서 읽은 전체 KODEX ETF 목록
    수집된 채널 텍스트에서 ETF 이름/코드를 스캔해 감지된 ETF 반환.
    """
    found = []      # 감지된 코드 목록
    evidence = []

    for r in collection_results.values():
        if not r.success or not r.data:
            continue
        d = r.data

        # 채널별 아이템 추출
        items = []
        is_event_channel = getattr(r, "channel", "") == "samsung_fund_event"
        # 채널 유형별 마케팅 분류 근거 명시
        ch = r.channel_name
        for v in d.get("videos", []):
            if not v.get("is_etf_related"):
                continue
            items.append({"title": v.get("title",""), "url": v.get("url",""),
                          "text": v.get("title",""),
                          "channel_reason": "증권 유튜브 채널 ETF 관련 영상"})
        for p in d.get("posts", []):
            items.append({"title": p.get("title",""), "url": p.get("link",""),
                          "text": p.get("title",""),
                          "channel_reason": "증권 공식 블로그에서 해당 ETF 관련 포스트 게시 확인"})
        for a in d.get("articles", []):
            items.append({"title": a.get("title",""), "url": a.get("link",""),
                          "text": a.get("title",""),
                          "channel_reason": "삼성증권 ETF 이벤트 관련 뉴스 기사에서 종목명 확인"})
        for ev in d.get("event_details", []):
            # samsung_fund_event: 수집 시 추출한 etf_names 직접 사용 (full_text 스캔 금지 — 본문에 무관한 ETF명 범람)
            if is_event_channel and ev.get("etf_names"):
                pre_codes = [k for k, v in all_kodex_etfs.items() if any(v in n or n in v for n in ev["etf_names"])]
                if pre_codes:
                    for code in pre_codes:
                        if code not in found:
                            found.append(code)
                    evidence.append({
                        "channel": r.channel_name,
                        "title": ev.get("title","")[:80],
                        "url": ev.get("url",""),
                        "image_url": ev.get("image_url",""),
                        "marketing_type": "이벤트",
                        "event_summary": "삼성자산운용 공식 이벤트 페이지에 '진행중' 이벤트로 등록됨",
                        "reason": f"감지: {', '.join(ev['etf_names'][:3])}",
                        "marketing_reason": "삼성자산운용 공식 이벤트 페이지에 '진행중' 이벤트로 등록됨",
                        "etf_codes": pre_codes[:3],
                        "target_etf": ", ".join(ev["etf_names"][:2]),
                    })
                continue
            # 그 외: 제목만 스캔 (full_text 금지)
            items.append({"title": ev.get("title",""), "url": ev.get("url",""),
                          "image_url": ev.get("image_url",""),
                          "text": ev.get("title",""),
                          "channel_reason": "삼성자산운용 공식 이벤트 페이지에 '진행중' 이벤트로 등록됨 (이벤트 제목·기간 명시)"})
        if not d.get("event_details"):
            for e in d.get("events", []):
                items.append({"title": e, "url": d.get("url",""), "text": e,
                              "channel_reason": "이벤트 페이지에서 ETF 관련 내용 확인"})
        if not d.get("event_details") and not d.get("events") and "raw_text" in d:
            items.append({"title": "(이벤트 페이지)", "url": d.get("url",""),
                          "text": d["raw_text"][:1000],
                          "channel_reason": "이벤트 페이지 텍스트에서 ETF명 확인"})

        # 뉴스/블로그 채널에서 마케팅 제외 ETF 태그
        # 레버리지·인버스·단일종목은 신규 상장 기사에 자주 나오지만 이벤트 대상 아님
        _EXCL_TAGS = ["레버리지", "인버스", "2X", "단일종목", "선물인버스"]
        is_event_channel = getattr(r, "channel", "") == "samsung_fund_event"

        # 전체 KODEX ETF 이름으로 검색
        for item in items:
            text = item["text"]
            matched_codes, matched_names = [], []

            for code, etf_name in all_kodex_etfs.items():
                # 이벤트 페이지 아닌 뉴스/블로그에서 레버리지류는 마케팅으로 감지 안 함
                if not is_event_channel and any(t in etf_name for t in _EXCL_TAGS):
                    continue
                if etf_name in text or code in text:
                    matched_codes.append(code)
                    matched_names.append(etf_name)

            if matched_codes:
                for code in matched_codes:
                    if code not in found:
                        found.append(code)
                channel_reason = item.get("channel_reason", "")
                _title = item.get("title","")
                ev_type = "이벤트" if "이벤트" in channel_reason or "이벤트" in _title else \
                          "프로모션" if "프로모션" in _title or "혜택" in _title else \
                          "추천콘텐츠" if "유튜브" in channel_reason else "기타"
                evidence.append({
                    "channel": r.channel_name,
                    "title": _title[:80],
                    "url": item["url"],
                    "image_url": item.get("image_url",""),
                    "marketing_type": ev_type,
                    "event_summary": channel_reason,
                    "reason": f"감지: {', '.join(matched_names[:3])}",
                    "marketing_reason": channel_reason,
                    "etf_codes": matched_codes[:3],
                    "target_etf": ", ".join(matched_names[:2]),
                })

    if found:
        names = [all_kodex_etfs.get(c, c) for c in found[:5]]
        return {
            "marketing_detected": True,
            "etf_codes": found,
            "summary": f"키워드 기반 감지 (Anthropic API 없음): {', '.join(names)}" + (f" 외 {len(found)-5}개" if len(found) > 5 else ""),
            "evidence": evidence[:8],
        }
    return {"marketing_detected": False, "etf_codes": [], "summary": "마케팅 활동 미감지 (키워드 방식)", "evidence": []}

# ── 메인 ──────────────────────────────────────────────────────────────────────
st.title("📊 증권 채널 KODEX ETF 마케팅 효과 측정 Agent")
st.caption("마케팅 활동 감지 → ETF 특정 → 비교군 매핑 → DiD 분석")

with st.expander("📐 마케팅 점수(0~100) 산정 방식", expanded=False):
    st.markdown("""
**증권 채널**은 유튜브·블로그·이벤트에서 마케팅 활동을 감지한 뒤 해당 ETF의 금융투자 순매수 변화를 측정합니다.

| 단계 | 내용 |
|------|------|
| ① 변화율 | `(현재 금융투자순매수 − 8주평균) ÷ (8주절댓값평균 + 라플라스α)` |
| ② DiD | `KODEX 변화율 − 경쟁사평균 변화율` (시장 공통 효과 제거) |
| ③ Z-score | `(이번주 DiD − 15주 DiD평균) ÷ 15주 DiD표준편차` |
| ④ sigmoid 점수 | `100 ÷ (1 + exp(−Z × 1.5))` → 0~100점 |

**점수의 통계적 의미:**
경쟁사 대비 초과 순매수 변화가 이번 주만큼 크게 발생할 확률이 과거 이력(15주) 분포에서 얼마나 드문가를 나타냅니다.

| 점수 | Z-score | 통계적 의미 |
|------|---------|------------|
| 88점 | +2.0 | 과거 이력 중 상위 2% — 매우 드문 초과 변화 |
| 81점 | +1.5 | 과거 이력 중 상위 7% — 통계적으로 유의미 |
| 73점 | +1.0 | 과거 이력 중 상위 16% — 다소 두드러짐 |
| 50점 | 0.0 | 경쟁사 대비 평균 수준 (중립) |
| 27점 | −1.0 | 과거 이력 중 하위 16% — 경쟁사 상대 열위 |

예시: KODEX +12% vs TIGER +5% → DiD = **+7%p**, 과거 15주 평균 +2%p, Z=+1.5 → **81점 (상위 7%)**

**판정 기준:** 🟢 ≥75점 효과 있음 / 🟡 ≥60점 가능성 / ⚪ ≥40점 중립 / 🔴 <40점 경쟁사 우위

**베이스라인 부족 시 (신규상장 ETF, 8주 미만) — AUM 상대강도 방식:**
> `KODEX비율 = 순매수/AUM` vs `경쟁사비율 = 순매수/AUM` → AUM DiD → sigmoid 0~100점
> AUM(시가총액)으로 나눠 체급 차이 제거. AUM DiD > 0 = KODEX 상대 유입 우위. ⚠️ Z-score 이력 없이 이번 주 단면만 측정하므로 다른 종목과 직접 비교 시 주의.

*기준 컬럼: 금융투자 순매수 (LP 노이즈 감지 시 개인 컬럼으로 자동 전환)*
    """)

# ── 데이터 로드 ───────────────────────────────────────────────────────────────
from analyzer import (ExcelLoader, MarketingAnalyzer, COMPARISON_MAP,
                      auto_map_competitors, extract_keyword,
                      extract_target_etfs_with_llm)
from collector import DataCollector, CHANNEL_LABELS
from report import build_report, export_html

DEFAULT_EXCEL = "ETF 순매수 데이터_260529.xlsx"

@st.cache_data(show_spinner=False)
def load_excel(file_bytes: bytes):
    return ExcelLoader().load(io.BytesIO(file_bytes))

@st.cache_data(show_spinner=False)
def load_excel_path(path: str):
    return ExcelLoader().load(path)

# ── 데이터 로드 우선순위: KRX 캐시 → 기존 엑셀 ───────────────────────────
from krx_data_fetcher import load_cache, load_cache_recent, save_cache, BASELINE_WEEKS

all_sheets = {}
base_loaded = False

# 1순위: KRX 캐시 최근 (8+1)주만 로드 — 분석에 필요한 만큼만
krx_cache = load_cache_recent(BASELINE_WEEKS + 3)  # 과거 주차 선택 시 베이스라인 확보
if krx_cache:
    all_sheets = krx_cache
    base_loaded = True
    st.toast(f"✅ 캐시 로드 — 최근 {len(all_sheets)}주차", icon="📊")

# 2순위: 기존 엑셀 파일 (캐시 없을 때)
elif os.path.exists(DEFAULT_EXCEL):
    with st.spinner("기본 데이터 로드 중..."):
        all_sheets = load_excel_path(DEFAULT_EXCEL)
    base_loaded = True

if not base_loaded:
    st.info("📊 KRX 데이터 없음 — 랜딩 페이지에서 **'📊 KRX 수집'** 을 먼저 실행하세요. (VPN 필수)")
    st.stop()


# 참고사항 등 데이터 시트가 아닌 것 제외
SKIP_SHEETS = {"참고사항", "설명", "readme", "README", "시트설명"}
sheet_names = [s for s in all_sheets.keys()
               if s not in SKIP_SHEETS and not s.lower().startswith("sheet")]

# 미래 주차 제외 + 시간순 정렬 (삽입 순서 의존 금지)
from krx_data_fetcher import _parse_week_label
_today = datetime.now().date()
sheet_names = [s for s in sheet_names
               if (_parse_week_label(s) is None or _parse_week_label(s) <= _today)]
sheet_names = sorted(sheet_names, key=lambda s: _parse_week_label(s) or _today)
if not sheet_names:
    st.error("유효한 데이터 시트를 찾지 못했습니다.")
    st.stop()


# 시트명에 날짜 경과 여부 라벨 추가
def _sheet_label(name: str) -> str:
    start, end = _parse_sheet_dates(name)
    if start is None:
        return name
    days = (datetime.now().date() - start).days
    # KRX 데이터는 월~금 — 끝날이 목요일이면 금요일로 보정
    try:
        from datetime import timedelta as _td
        if end and end.weekday() == 3:
            real_end = end + _td(days=1)
            display = f"{start.month}.{start.day}-{real_end.month}.{real_end.day}"
        else:
            display = name
    except Exception:
        display = name
    if days == 0:
        return f"{display}  (이번 주)"
    return f"{display}  ({days}일 전)"

labeled = [_sheet_label(s) for s in sheet_names]
_is_friday = datetime.now().weekday() == 4
_default_idx = len(labeled) - 1
if not _is_friday and len(labeled) >= 2:
    _default_idx = len(labeled) - 2
    st.caption("💡 금요일 장 마감 후 이번 주 데이터가 완성됩니다.")
# sheet 키(예: "6.15-6.19")로 저장 → 레이블이 날짜마다 바뀌어도 유지
_saved_sheet = st.session_state.get("sec_sheet_key")
if _saved_sheet and _saved_sheet in sheet_names:
    _default_idx = sheet_names.index(_saved_sheet)
selected_label = st.selectbox("분석할 주차 시트 선택", labeled, index=_default_idx, key="sec_selected_label")
current_sheet = sheet_names[labeled.index(selected_label)]
st.session_state["sec_sheet_key"] = current_sheet

# 과거 주차 선택 시 신뢰도 경고
_sel_start, _ = _parse_sheet_dates(current_sheet)
if _sel_start:
    _days = (datetime.now().date() - _sel_start).days
    if _days > 14:
        st.markdown(
            f"<div style='background:rgba(255,200,50,0.08); border:1px solid rgba(255,200,50,0.3); "
            f"border-radius:6px; padding:8px 12px; font-size:0.85rem; opacity:0.75; margin:4px 0;'>"
            f"⚠️ <b>{_days}일 전 주차</b> — 과거 채널 데이터(RSS)가 소실됐을 수 있어 마케팅 감지 신뢰도가 낮을 수 있습니다. "
            f"DiD 계산(순매수)은 정상 수행됩니다.</div>",
            unsafe_allow_html=True
        )

with st.expander("📋 선택 시트 미리보기", expanded=False):
    _preview_df = all_sheets[current_sheet]
    _code_col_p = "단축코드" if "단축코드" in _preview_df.columns else "종목코드"
    _kodex_rows = _preview_df[_preview_df["종목명"].str.contains("KODEX", na=False)]
    _preview = _kodex_rows.head(15) if len(_kodex_rows) >= 5 else _preview_df.head(15)
    st.dataframe(_preview, use_container_width=True)

st.header("🚀 분석 실행")

# ── 모드 자동 판별 ─────────────────────────────────────────────────────────
# 시트명에서 날짜 파싱 시도 (예: "5.25-5.28" → 5월 25일)
sheet_start, sheet_end = _parse_sheet_dates(current_sheet)
today_date = datetime.now().date()
days_ago   = (today_date - sheet_start).days if sheet_start else 0
IS_BACKTEST = sheet_start is not None and days_ago > 14

# collector에 넘길 datetime
week_start_dt = datetime(sheet_start.year, sheet_start.month, sheet_start.day) if sheet_start else None
week_end_dt   = datetime(sheet_end.year,   sheet_end.month,   sheet_end.day, 23, 59) if sheet_end else None

# KRX 데이터는 월~금 — 끝날이 목요일이면 금요일로 보정
from datetime import timedelta as _td
_real_end = sheet_end + _td(days=1) if (sheet_end and sheet_end.weekday() == 3) else sheet_end
week_range_str = f"{sheet_start.strftime('%m/%d')}~{_real_end.strftime('%m/%d')}" if sheet_start else current_sheet

# ── 5일 미만(불완전 주) 차단 ──
from datetime import timedelta as _td2
_trading_days = 0
if sheet_start and sheet_end:
    d = sheet_start
    while d <= sheet_end:
        if d.weekday() < 5:  # 월~금
            _trading_days += 1
        d += _td2(days=1)

# 5일 미만 체크 — 오직 "이번 주가 아직 진행 중"일 때만 (과거 완료 주차는 통과)
from datetime import date as _d2
_week_already_ended = _real_end < _d2.today() if sheet_end else False
if _trading_days < 5 and not _week_already_ended:
    st.error(
        f"⛔ 이번 주는 {_trading_days}일치 데이터만 있어요 ({week_range_str}). "
        f"거래일 5일이 완성되는 **금요일 장 마감(15:30) 후** 분석을 시작하세요. "
        f"3일 데이터를 5일 평균으로 나누면 DiD가 왜곡됩니다."
    )
    st.stop()

if IS_BACKTEST:
    st.markdown(
        f'<small style="opacity:.7;">⚠️ {days_ago}일 전 주차 — 채널 데이터가 일부 또는 전부 없을 수 있음 (RSS 보관 기간 초과 가능). DiD 계산은 정상 수행됩니다.</small>',
        unsafe_allow_html=True)
else:
    st.markdown(
        f'<small style="opacity:.7;">채널 수집 기준: <b>{week_range_str}</b></small>',
        unsafe_allow_html=True)
st.markdown("")

# 아카이브 있으면 버튼 없이 자동 진행
if not st.session_state.get("analysis_run", False):
    from channel_archive import has_archive as _has_arch
    if _has_arch(current_sheet):
        st.session_state["analysis_run"] = True

if not st.session_state.get("analysis_run", False):
    st.info("📦 이번 주 수집 데이터 없음 — 랜딩 페이지에서 **'🔄 전체 수집 시작'** 을 먼저 실행하세요.")
    st.stop()

# ════════════════════════════════════════════════════════════════════
# STEP 1: 마케팅 채널 수집 (주간 분석 모드만)
# ════════════════════════════════════════════════════════════════════
st.markdown('<div class="step-header">Step 1 · 마케팅 채널 수집</div>', unsafe_allow_html=True)

if True:  # 과거/현재 모두 수집 시도
    collector = DataCollector(
        youtube_api_key=youtube_key, naver_client_id=naver_id,
        naver_client_secret=naver_secret, anthropic_api_key=anthropic_key,
        week_start=week_start_dt, week_end=week_end_dt,
    )
    # 공룡 달리기 로딩 애니메이션
    dino_ph = st.empty()
    status = st.empty()

    _bull_tick = [0]

    def on_prog(idx, total, name):
        pct = idx / total

        _bull_tick[0] += 1
        # 불 사이클: 🔥 크기 변화 (작→중→큰)
        fire_cycle = _bull_tick[0] % 3
        fire_size = ["1rem", "1.3rem", "1.6rem"][fire_cycle]

        bar_w = int(pct * 100)

        dino_ph.markdown(
            f"""<div style='background:rgba(255,255,255,0.03); border-radius:8px; padding:8px 8px 4px; margin:6px 0;'>
              <!-- 황소+불 애니메이션 영역 -->
              <div style='position:relative; height:50px;'>
                <!-- 배경 트랙 -->
                <div style='position:absolute; left:0; top:50%; transform:translateY(-50%);
                            height:5px; width:100%; background:rgba(255,255,255,0.06); border-radius:3px;'></div>
                <!-- 진행 바 -->
                <div style='position:absolute; left:0; top:50%; transform:translateY(-50%);
                            height:5px; width:{bar_w}%;
                            background:linear-gradient(90deg,#4d9fff,#00c6ff);
                            border-radius:3px; transition:width 0.25s;'></div>
                <!-- 🔥 불 (황소 바로 위) -->
                <div style='position:absolute;
                            left:calc({bar_w}% - 1.1rem);
                            top:0px; font-size:{fire_size};
                            transition:left 0.25s, font-size 0.15s; line-height:1;'>🔥</div>
                <!-- 🐂 황소 (바 바로 위에 서있음) -->
                <div style='position:absolute;
                            left:calc({bar_w}% - 1.4rem);
                            top:12px; font-size:1.8rem;
                            transform:scaleX(-1);
                            transition:left 0.25s; line-height:1;'>🐂</div>
              </div>
              <!-- 텍스트 (별도 줄) -->
              <div style='font-size:0.8rem; opacity:.45; margin-top:2px;'>{int(pct*100)}% — {name[:35]}</div>
            </div>""",
            unsafe_allow_html=True
        )

    t0 = time.time()

    # ── 주차별 채널 수집 결과 아카이브: 같은 주차를 다시 분석할 때 ──
    # RSS/유튜브/블로그는 "현재 시점 기준 최근 글"만 반환하므로, 1주만 지나도
    # 그 시점에 감지했던 글/링크가 사라져 재조회가 안 됨 → 최초 수집 시 보존,
    # 이후엔 보존된 결과를 그대로 사용 (그 주차 시점의 데이터를 영구 확보)
    from channel_archive import has_archive, save_channel_results, load_channel_results, get_archived_at, save_raw_data, load_raw_data

    _from_archive = False
    _days_old = (today_date - sheet_start).days if sheet_start else 0
    if has_archive(current_sheet):
        collection_results = load_channel_results(current_sheet)
        _from_archive = True
        _archived_at = get_archived_at(current_sheet)
    else:
        collection_results = collector.collect_all(progress_callback=on_prog)
        if _days_old <= 14:
            save_channel_results(current_sheet, collection_results)

    elapsed = time.time() - t0
    ok   = sum(1 for r in collection_results.values() if r.success)
    fail = len(collection_results) - ok
    # 완료: 황소가 바 끝(100%)에 서있고 불 꺼짐
    _src_label = f"📦 보존된 결과 사용 (최초 수집: {_archived_at})" if _from_archive else f"완료 {elapsed:.1f}초"
    dino_ph.markdown(
        f"""<div style='background:rgba(255,255,255,0.03); border-radius:8px; padding:8px 8px 4px; margin:6px 0;'>
          <div style='position:relative; height:50px;'>
            <div style='position:absolute; left:0; top:50%; transform:translateY(-50%);
                        height:5px; width:100%;
                        background:linear-gradient(90deg,#4d9fff,#00c6ff); border-radius:3px;'></div>
            <div style='position:absolute; left:calc(100% - 1.1rem); top:0px; font-size:1rem;'>✅</div>
            <div style='position:absolute; left:calc(100% - 1.4rem); top:12px;
                        font-size:1.8rem; transform:scaleX(-1);'>🐂</div>
          </div>
          <div style='font-size:0.8rem; opacity:.6; margin-top:2px;'>
            {_src_label} — 성공 {ok}개 / 실패 {fail}개</div>
        </div>""",
        unsafe_allow_html=True
    )
    status.empty()  # 채널 이름 pills 제거

    with st.expander("📡 채널별 상세", expanded=False):
        cols = st.columns(3)
        for i, r in enumerate(collection_results.values()):
            icon = "✅" if r.success else "❌"
            detail = summarize_channel(r) if r.success else (r.error_label or r.error or "")
            cols[i%3].markdown(
                f"{icon} **{r.channel_name}**  \n<small style='opacity:.7;'>{detail[:60]}</small>",
                unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════════
# STEP 2: LLM 마케팅 감지 → 대상 ETF 특정
# ════════════════════════════════════════════════════════════════════
st.markdown('<div class="step-header">Step 2 · 마케팅 활동 감지 & 대상 ETF 특정</div>', unsafe_allow_html=True)

# 엑셀 전체 KODEX ETF 목록 (코드 → 이름) — Step 2, 3에서 모두 사용
current_df = all_sheets[current_sheet]
# KRX 캐시는 load_cache_recent()에서 '단축코드'→'종목코드' 이미 정규화됨
_code_col = "종목코드" if "종목코드" in current_df.columns else "단축코드"
etf_universe = current_df[[_code_col,"종목명"]].dropna(subset=["종목명"]).rename(columns={_code_col:"종목코드"}).copy()
etf_universe["종목코드"] = etf_universe["종목코드"].astype(str).str.split("*").str[0].str.strip()
all_kodex_etfs = {
    str(row["종목코드"]).split("*")[0].strip(): str(row["종목명"])
    for _, row in etf_universe[etf_universe["종목명"].str.contains("KODEX", na=False)].iterrows()
}

if IS_BACKTEST and not collection_results:
    # 채널 수집 결과가 아예 없을 때만 직접 선택 안내
    st.info("채널 수집 결과 없음 — 분석할 ETF를 아래에서 직접 선택하세요.")
    detected_codes = []
    llm_result = {"marketing_detected": False, "etf_codes": [],
                  "summary": f"{current_sheet} 채널 수집 결과 없음"}
else:
    _sec_llm_key = f"sec_llm_{current_sheet}"
    _sec_cached = load_raw_data(_sec_llm_key) if has_archive(_sec_llm_key) else None
    _sec_cache_valid = bool(
        _sec_cached and not (
            _sec_cached.get("marketing_detected") is False
            and "실패" in _sec_cached.get("summary", "")
        )
    )
    if _sec_cached and _sec_cache_valid:
        llm_result = _sec_cached
        st.caption(f"📦 LLM 분석 결과 캐시 사용 ({_sec_llm_key})")
    else:
        with st.spinner("LLM 분석 중..."):
            if anthropic_key:
                llm_result = extract_target_etfs_with_llm(collection_results, anthropic_key)
            else:
                llm_result = keyword_fallback(collection_results, all_kodex_etfs)
        _sec_llm_failed = "실패" in llm_result.get("summary", "")
        if _sec_llm_failed:
            llm_result = keyword_fallback(collection_results, all_kodex_etfs)
        if llm_result and llm_result.get("marketing_detected") is not None and not _sec_llm_failed:
            save_raw_data(_sec_llm_key, llm_result)

    if llm_result.get("marketing_detected"):
        etf_names_det = [all_kodex_etfs.get(c, COMPARISON_MAP.get(c, {}).get("name", c))
                         for c in llm_result.get("etf_codes", [])]
        st.success(f"📣 마케팅 활동 감지 — 대상 ETF: **{', '.join(etf_names_det)}**")
        if llm_result.get("summary"):
            st.caption(llm_result["summary"])

        # ── 이벤트 보드 ──────────────────────────────────────────────────────
        evidence = llm_result.get("evidence", [])
        # top-level etf_codes를 개별 evidence 항목에 fallback으로 채워줌
        _top_codes = llm_result.get("etf_codes", [])
        for _ev in (evidence or []):
            if not _ev.get("etf_codes") and _top_codes:
                _ev["etf_codes"] = _top_codes
        events_with_info = [ev for ev in (evidence or []) if ev.get("event_summary") or ev.get("event_period") or ev.get("etf_codes")]
        if events_with_info:
            _type_cls  = {"이벤트":"ev-type-event","프로모션":"ev-type-promo","추천콘텐츠":"ev-type-content","수수료혜택":"ev-type-fee"}
            _type_icon = {"이벤트":"🎁","프로모션":"💰","추천콘텐츠":"📺","수수료혜택":"🎯"}
            _ch_icon   = {"삼성자산운용 이벤트 페이지":"🎪","삼성증권 이벤트":"🏦","네이버/구글 뉴스":"📰","증권 유튜브":"▶️"}
            cards_html = '<div class="ev-board">'
            for ev in events_with_info[:8]:
                mtype   = ev.get("marketing_type","기타")
                cls     = _type_cls.get(mtype,"ev-type-etc")
                icon    = _type_icon.get(mtype,"📋")
                title   = ev.get("title","")[:60]
                period  = ev.get("event_period") or ""
                summary = ev.get("event_summary") or ev.get("marketing_reason") or ""
                channel = ev.get("channel","")
                url     = ev.get("url","")
                target  = ev.get("target_etf") or ""
                ev_etf_codes = ev.get("etf_codes",[])
                ev_etf_names = [all_kodex_etfs.get(c,c) for c in ev_etf_codes if c]
                etf_label = target or (", ".join(ev_etf_names[:2]) if ev_etf_names else "")
                img_url = ev.get("image_url","")
                # 썸네일 없으면 채널 수집 결과에서 자동 매칭
                if not img_url and collection_results:
                    # 1) URL/title 직접 매칭
                    for _cr in collection_results.values():
                        if not _cr.success or not _cr.data: continue
                        for _v in (_cr.data.get("videos") or []):
                            if _v.get("thumbnail") and (_v.get("url","") == url or title[:20] in _v.get("title","")):
                                img_url = _v["thumbnail"]; break
                        for _a in (_cr.data.get("articles") or []):
                            if _a.get("thumbnail") and (_a.get("url","") == url or title[:20] in _a.get("title","")):
                                img_url = _a["thumbnail"]; break
                        if img_url: break
                    # 2) 없으면 카카오 채널에서 썸네일 풀로 대체
                    if not img_url and collection_results:
                        for _cr in collection_results.values():
                            if not _cr.success or not _cr.data: continue
                            for _a in (_cr.data.get("articles") or []):
                                _th = _a.get("thumbnail","")
                                if _th and "kakaocdn" in _th:
                                    img_url = _th; break
                            if img_url: break
                title_html  = f'<a href="{url}" target="_blank" style="color:#e8eaed;text-decoration:none;">{title}</a>' if url and url.startswith("http") else title
                period_html = f'<div class="ev-period">📅 {period}</div>' if period and period not in ("","null") else ""
                etf_html    = f'<div class="ev-etf" style="color:#4d9fff;">🎯 {etf_label}</div>' if etf_label else ""
                img_html    = f'<img class="ev-card-img" src="{img_url}" onerror="this.style.display=\'none\'">' if img_url else f'<div class="ev-card-img-placeholder">🎯</div>'
                cards_html += (
                    f'<div class="ev-card">'
                    f'{img_html}'
                    f'<div class="ev-card-body">'
                    f'<span class="ev-card-type {cls}">{icon} {mtype}</span>'
                    f'<div class="ev-title">{title_html}</div>'
                    f'{period_html}'
                    f'{etf_html}'
                    f'<div class="ev-summary">{summary[:140]}</div>'
                    f'<div class="ev-channel">📡 {channel}</div>'
                    f'</div></div>'
                )
            cards_html += "</div>"
            st.markdown(cards_html, unsafe_allow_html=True)

        # 감지 근거 — 탭 형태 (접이식)
        evidence = llm_result.get("evidence", [])
        from collections import defaultdict
        by_channel = defaultdict(list)
        for ev in (evidence or []):
            by_channel[ev.get("channel", "기타")].append(ev)

        # 미감지 채널도 탭에 포함
        detected_channels = set(by_channel.keys())
        non_detected_tabs = {}
        for r in collection_results.values():
            if not r.success or not r.data or r.channel_name in detected_channels:
                continue
            d = r.data
            items = []
            if "videos"   in d: items = [v.get("title","") for v in d["videos"][:3]]
            elif "posts"  in d: items = [p.get("title","") for p in d["posts"][:3]]
            elif "articles" in d: items = [a.get("title","") for a in d["articles"][:3]]
            elif "events" in d: items = d["events"][:3]
            if items:
                non_detected_tabs[r.channel_name] = items

        _SKIP_EXPANDER_CHANNELS = {"삼성자산운용 이벤트 페이지", "삼성자산운용 웹사이트", "삼성자산운용 공식 웹사이트"}
        if by_channel:
            for ch_name, evs in by_channel.items():
                if ch_name in _SKIP_EXPANDER_CHANNELS:
                    continue  # 카드로 이미 표시됨
                with st.expander(f"📡 {ch_name}", expanded=True):
                    for ev in evs:
                        title  = ev.get("title", "")
                        url    = ev.get("url", "")
                        reason = ev.get("reason", "")
                        etf_codes = ev.get("etf_codes", [])
                        etf_names = [all_kodex_etfs.get(c, c) for c in etf_codes]
                        link_md = f"[{title}]({url})" if url and url.startswith("http") else f"**{title}**"
                        etf_str = f" → `{'`, `'.join(etf_names[:3])}`" if etf_names else ""
                        mkt_reason = ev.get("marketing_reason", "")
                        st.markdown(f"• {link_md}{etf_str}")
                        if reason:
                            st.caption(f"↳ {reason}")
                        if mkt_reason:
                            st.caption(f"📋 {mkt_reason}")
            # 미감지 채널 하나의 expander에 탭으로 묶기
            if non_detected_tabs:
                with st.expander(f"📋 수집됐으나 ETF 미감지 채널 ({len(non_detected_tabs)}개)", expanded=False):
                    st.caption("콘텐츠는 수집됐으나 KODEX ETF 관련 내용 없어 감지 제외")
                    nd_tabs = st.tabs([f"📡 {ch}" for ch in non_detected_tabs])
                    for ti, (ch_name, items) in enumerate(non_detected_tabs.items()):
                        with nd_tabs[ti]:
                            for it in items:
                                st.markdown(f"• {it}")
        else:
            st.caption("감지 근거 없음")
    else:
        st.warning("이번 주 마케팅 활동 없음 — 베이스라인 업데이트만 수행됩니다.")
        if llm_result.get("summary"):
            st.caption(llm_result["summary"])

        # 수집된 채널 데이터 확인 (왜 감지 안 됐는지)
        with st.expander("🔍 수집된 채널 데이터 확인", expanded=False):
            for r in collection_results.values():
                if not r.success or not r.data:
                    continue
                d = r.data
                items = []
                if "videos"   in d: items = [v.get("title","") for v in d["videos"][:3]]
                elif "posts"  in d: items = [p.get("title","") for p in d["posts"][:3]]
                elif "articles" in d: items = [a.get("title","") for a in d["articles"][:3]]
                if items:
                    st.markdown(f"**{r.channel_name}**")
                    for it in items:
                        st.markdown(f"- {it}")

    detected_codes = llm_result.get("etf_codes", [])

# ETF 자동 확정
target_codes = detected_codes

if not target_codes:
    if IS_BACKTEST:
        # 과거 주차 — 채널 감지 실패해도 직접 선택해서 DiD 가능
        st.info("채널 감지 없음 — 분석할 KODEX ETF를 직접 선택하세요.")
        all_kodex_names = {v: k for k, v in all_kodex_etfs.items()}
        selected_names = st.multiselect(
            "분석할 ETF 선택",
            sorted(all_kodex_names.keys()),
            key="backtest_etf_select"
        )
        if not selected_names:
            st.stop()
        target_codes = [all_kodex_names[n] for n in selected_names]
    else:
        st.warning("감지된 마케팅 활동 없음 — 이번 주 분석을 종료합니다.")
        st.stop()

# ════════════════════════════════════════════════════════════════════
# STEP 3: 비교군 자동 매핑 (미리보기 + 확인)
# ════════════════════════════════════════════════════════════════════
st.markdown('<div class="step-header">Step 3 · 비교군 매핑</div>', unsafe_allow_html=True)

with st.expander("🔗 비교군 매핑", expanded=True):
    st.caption("📌 매핑 근거: 사전 매핑(수익률 상관계수 0.7+ 검증) → 없으면 실시간 이름 유사도 탐색 → 운용사별 최대 2개")
    analyzer = MarketingAnalyzer()
    for code in target_codes:
        row_etf = analyzer.loader.get_etf_row(current_df, code, code)
        etf_name = row_etf.name if row_etf else code

        from etf_mapping_loader import get_competitors as _get_comp_step3
        _code_s = code.replace("*001","").strip()
        comps = _get_comp_step3(_code_s) or auto_map_competitors(etf_name, _code_s, etf_universe)

        _pc = {"KODEX":"#4d9fff","TIGER":"#f4a261","ACE":"#e76f51","PLUS":"#2a9d8f","SOL":"#e9c46a","RISE":"#6b9fff","HANARO":"#a78bfa"}
        total_cards = 1 + len(comps)
        card_w = f"flex:1; min-width:0; max-width:calc(100%/{total_cards});"

        def _card(provider, name, code_str, color, corr=None):
            initial = provider[0] if provider else "?"
            corr_line = f'<div style="font-size:.62rem;color:#888;margin-top:3px;">r={corr:.3f}</div>' if corr is not None else ""
            return (
                f'<div style="{card_w} border:2px solid {color}; border-radius:24px; '
                f'padding:16px 14px; text-align:center; background:#16181c;">'
                f'<div class="prov-badge" style="background:{color}20;color:{color};margin:0 auto 8px;">{initial}</div>'
                f'<div style="font-size:0.7rem;color:{color};font-weight:700;margin-bottom:3px;letter-spacing:.05em;">{provider}</div>'
                f'<div style="font-size:1rem;font-weight:700;color:#e8eaed;line-height:1.2;">{name}</div>'
                f'<div style="font-size:0.68rem;color:#5b616e;margin-top:4px;">{code_str}</div>'
                + corr_line +
                f'</div>'
            )

        cards_html = '<div style="display:flex; gap:12px; margin:10px 0;">'
        cards_html += _card("KODEX", etf_name.replace("KODEX ",""), code, "#0052ff")
        if comps:
            for comp in comps:
                c = _pc.get(comp['provider'], "#adb5bd")
                short_name = comp["name"].replace("TIGER ","").replace("PLUS ","").replace("ACE ","").replace("SOL ","").replace("RISE ","").replace("HANARO ","")
                cards_html += _card(comp['provider'], short_name, comp["code"], c, corr=comp.get("corr"))
            cards_html += '</div>'
            st.markdown(cards_html, unsafe_allow_html=True)
            if len(comps) == 1:
                st.caption("※ 동일 유형 ETF 시장에 1종만 존재 — 통상 2개 비교군 기준이나 1개만 매핑됨")
        else:
            cards_html += '</div>'
            st.markdown(cards_html, unsafe_allow_html=True)
            st.warning("⚫ 비교군 없음 — DiD 측정 불가")
        st.divider()

# ════════════════════════════════════════════════════════════════════
# STEP 4: 베이스라인 + LP 감지
# ════════════════════════════════════════════════════════════════════
st.markdown('<div class="step-header">Step 4 · 베이스라인 (직전 8주 평균) & LP 노이즈 감지</div>', unsafe_allow_html=True)

# 현재 주 이전 시트만 history로 사용 (시트 순서 = 시간 순서)
current_idx = sheet_names.index(current_sheet)
history_sheets = {k: all_sheets[k] for k in sheet_names[:current_idx]}

with st.expander("📊 베이스라인 상세", expanded=False):
    for code in target_codes:
        row_etf = analyzer.loader.get_etf_row(current_df, code, code)
        etf_name = row_etf.name if row_etf else code
        bl = analyzer._compute_baseline(code, etf_name, history_sheets)
        cur = analyzer.loader.get_etf_row(current_df, code, etf_name)

        st.markdown(f"**{etf_name}**")
        c1,c2,c3,c4,c5 = st.columns(5)
        c1.metric("이번주 금융투자", f"{cur.financial_investment/1e6:.1f}M" if cur else "N/A")
        c2.metric("이번주 개인",     f"{cur.individual/1e6:.1f}M"            if cur else "N/A")
        c3.metric("8주평균 금융투자", f"{bl.fi_avg/1e6:.1f}M")
        c4.metric("8주평균 개인",    f"{bl.ind_avg/1e6:.1f}M")
        c5.metric("데이터 주수",     f"{bl.weeks_used}주")

        if bl.history:
            hdf = pd.DataFrame(bl.history).rename(columns={"week":"시트","fi":"금융투자","ind":"개인"})

            # 베이스라인 추세 라인 차트
            fig_bl = go.Figure()
            fig_bl.add_trace(go.Scatter(
                x=hdf["시트"], y=hdf["금융투자"]/1e6,
                mode="lines+markers", name="금융투자",
                line=dict(color="#4d9fff", width=2),
                hovertemplate="%{x}<br>금융투자: %{y:.1f}M<extra></extra>",
            ))
            fig_bl.add_trace(go.Scatter(
                x=hdf["시트"], y=hdf["개인"]/1e6,
                mode="lines+markers", name="개인",
                line=dict(color="#28a745", width=2, dash="dot"),
                hovertemplate="%{x}<br>개인: %{y:.1f}M<extra></extra>",
            ))
            # 이번 주 값 표시
            if cur:
                fig_bl.add_trace(go.Scatter(
                    x=[current_sheet], y=[cur.financial_investment/1e6],
                    mode="markers", name="이번주(금융투자)",
                    marker=dict(color="#4d9fff", size=12, symbol="star"),
                ))
                fig_bl.add_trace(go.Scatter(
                    x=[current_sheet], y=[cur.individual/1e6],
                    mode="markers", name="이번주(개인)",
                    marker=dict(color="#28a745", size=12, symbol="star"),
                ))
            fig_bl.update_layout(
                title=f"{etf_name} 순매수 추세 (단위: M)",
                template="plotly_dark", height=280,
                margin=dict(t=40, b=20),
                legend=dict(orientation="h", y=-0.25),
            )
            st.plotly_chart(fig_bl, use_container_width=True)
            st.dataframe(hdf, use_container_width=True, hide_index=True)
        st.divider()

# ════════════════════════════════════════════════════════════════════
# STEP 5: DiD 계산 (핵심 과정 투명 표시)
# ════════════════════════════════════════════════════════════════════
st.markdown('<div class="step-header">Step 5 · DiD 계산 (이중차분법)</div>', unsafe_allow_html=True)

# did_history.parquet에서 먼저 읽기 → session_state → 없으면 계산
_did_cache_key = f"sec_did_{current_sheet}"
did_results = None

if _did_cache_key not in st.session_state:
    # parquet 캐시에서 로드 시도
    try:
        import pandas as _pd_did
        _did_hist = _pd_did.read_parquet(os.path.join(os.path.dirname(__file__), "did_history.parquet"))
        _week_hist = _did_hist[(_did_hist["week"] == current_sheet) & (_did_hist["channel"] == "securities")]
        if not _week_hist.empty:
            # parquet → ETFDiDResult 재구성 (판정/점수만 복원)
            _hist_map = {}
            for _, _hrow in _week_hist.iterrows():
                _code = str(_hrow.get("code",""))
                import math as _math
                _z = float(_hrow.get("value",0) or 0)
                _score = round(100 / (1 + _math.exp(-_z * 1.5)), 1)
                _j = str(_hrow.get("judgement",""))
                _emoji = "🟢" if _score>=75 else "🟡" if _score>=60 else "⚪" if _score>=40 else "🔴"
                _hist_map[_code] = type("_R", (), {
                    "kodex_code": _code,
                    "kodex_name": str(_hrow.get("name",_code)),
                    "did_value": _z,
                    "raw_did_value": _z,
                    "zscore": _z,
                    "marketing_score": _score,
                    "judgement": _j,
                    "judgement_emoji": _emoji,
                    "competitors": [], "no_competitors": bool(_hrow.get("no_competitors",False)),
                    "notes": [], "calculation_log": [],
                    "lp": None, "current": None, "baseline": None,
                    "kodex_change_pct": 0.0, "control_avg_pct": 0.0,
                })()
            if _hist_map and all(c in _hist_map for c in target_codes):
                # target_codes에 해당하는 것만 — 과거 분석 결과 전체가 뜨는 버그 방지
                st.session_state[_did_cache_key] = {c: _hist_map[c] for c in target_codes if c in _hist_map}
    except Exception:
        pass

_did_from_parquet = False
if _did_cache_key in st.session_state:
    did_results = st.session_state[_did_cache_key]
    _did_from_parquet = True
    st.caption(f"📦 저장된 분석 결과 사용 ({current_sheet}) · 재계산하려면 분석 실행 버튼 클릭")
    if st.button("🔄 DiD 재계산", key="rerun_did"):
        del st.session_state[_did_cache_key]
        st.rerun()
else:
    with st.spinner("DiD 분석 중..."):
        did_results = analyzer.analyze(all_sheets, target_codes, current_sheet)
    if did_results:
        st.session_state[_did_cache_key] = did_results

# ── DiD 결과 요약 바 차트 (Plotly) ──
if did_results:
    color_map = {"🟢":"#28a745","🟡":"#ffc107","⚪":"#6c757d","🔴":"#dc3545","⚫":"#343a40"}

    # 판정 카드
    summary_cols = st.columns(len(did_results))
    for col, (code, res) in zip(summary_cols, did_results.items()):
        c = color_map.get(res.judgement_emoji, "#6c757d")
        with col:
            st.markdown(
                f"<div style='border:2px solid {c};border-radius:8px;padding:14px;text-align:center;'>"
                f"<div style='font-size:2rem;'>{res.judgement_emoji}</div>"
                f"<div style='font-weight:700;font-size:0.85rem;'>{res.kodex_name}</div>"
                f"<div class='did-result' style='color:{c};'>{score_label(res)}</div>"
                f"<div style='font-size:0.78rem;color:#555;'>{res.judgement}</div>"
                f"</div>", unsafe_allow_html=True)

    st.markdown("")

    # 마케팅 점수 바 차트
    etf_names  = [r.kodex_name for r in did_results.values()]
    score_vals = [float(getattr(r, 'marketing_score', None) or 50.0) for r in did_results.values()]
    zscore_vals= [float(getattr(r, 'zscore', None) or 0.0) for r in did_results.values()]
    bar_colors = [color_map.get(r.judgement_emoji, "#6c757d") for r in did_results.values()]

    # ── 마케팅 점수 (0~100) 가로 막대 ──
    short_names = [n.replace("KODEX ", "") for n in etf_names]

    fig_did = go.Figure()
    for name, short, score, z, color in zip(etf_names, short_names, score_vals, zscore_vals, bar_colors):
        label = f"{score:.0f}점"
        fig_did.add_trace(go.Bar(
            y=[short], x=[score],
            orientation="h",
            marker_color=color,
            marker_line_width=0,
            text=label,
            textposition="outside",
            textfont=dict(size=12, color="white"),
            hovertemplate=f"<b>{name}</b><br>마케팅 점수: {score:.1f}점<br>Z-score: {z:+.3f}<extra></extra>",
            showlegend=False,
        ))
    fig_did.add_vline(x=75, line_dash="dot", line_color="#28a745", line_width=1.5,
                      annotation=dict(text="75 효과있음", font_color="#28a745", font_size=10, y=1.08))
    fig_did.add_vline(x=60, line_dash="dot", line_color="#ffc107", line_width=1.5,
                      annotation=dict(text="60 가능성", font_color="#ffc107", font_size=10, y=1.08))
    fig_did.add_vline(x=40, line_dash="dot", line_color="#6c757d", line_width=1.5,
                      annotation=dict(text="40 중립", font_color="#aaa", font_size=10, y=1.08))
    fig_did.add_vline(x=25, line_dash="dot", line_color="#dc3545", line_width=1.5,
                      annotation=dict(text="25 경쟁사↑", font_color="#dc3545", font_size=10, y=1.08))
    fig_did.update_layout(
        title=dict(text="📊 ETF별 마케팅 점수 (0~100)", font_size=15, x=0),
        xaxis=dict(title="마케팅 점수 (0~100)", range=[0, 115],
                   gridcolor="rgba(255,255,255,0.08)", zeroline=False),
        yaxis=dict(title="", autorange="reversed", tickfont=dict(size=12)),
        template="plotly_dark",
        height=max(180, len(did_results) * 72 + 100),
        margin=dict(t=70, b=40, l=10, r=120),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_did, use_container_width=True)

    # ── KODEX vs 비교군 그룹 바 차트 (parquet 복원 시 변화율 데이터 없으므로 스킵) ──
    if _did_from_parquet:
        st.caption("ℹ️ 변화율 차트는 DiD 재계산 후 표시됩니다.")
    chart_rows = []
    if not _did_from_parquet:
        for res in did_results.values():
            short = res.kodex_name.replace("KODEX ", "")
            chart_rows.append({"ETF": short, "구분": "KODEX", "변화율": res.kodex_change_pct * 100, "order": 0, "no_comp": res.no_competitors})
            if res.competitors:
                for i, comp in enumerate(res.competitors):
                    label = comp.provider
                    chart_rows.append({"ETF": short, "구분": label, "변화율": comp.change_pct * 100, "order": i+1, "no_comp": False})
            else:
                chart_rows.append({"ETF": short, "구분": "비교군없음", "변화율": 0, "order": 1, "no_comp": True})

    if chart_rows:
        from plotly.subplots import make_subplots
        _pc2 = {"KODEX":"#4d9fff","TIGER":"#f4a261","ACE":"#e76f51","PLUS":"#2a9d8f","SOL":"#e9c46a","RISE":"#6b9fff","HANARO":"#a78bfa"}
        etf_groups = list(dict.fromkeys(r["ETF"] for r in chart_rows))
        n = len(etf_groups)

        # ETF별 서브플롯 (가로로 나란히)
        fig_comp = make_subplots(
            rows=1, cols=n,
            subplot_titles=etf_groups,
            shared_yaxes=True,
        )
        for col_i, etf in enumerate(etf_groups):
            etf_rows = [r for r in chart_rows if r["ETF"] == etf]
            labels = [r["구분"] for r in etf_rows]
            vals   = [r["변화율"] for r in etf_rows]
            colors = [_pc2.get(lbl, "#adb5bd") for lbl in labels]
            texts  = [f"{v:+.0f}%" if not r.get("no_comp") else "없음" for r, v in zip(etf_rows, vals)]
            fig_comp.add_trace(
                go.Bar(
                    x=labels, y=vals,
                    marker_color=colors,
                    marker_line_width=0,
                    text=texts,
                    textposition="outside",
                    textfont=dict(size=11, color="white"),
                    hovertemplate="%{x}: %{y:+.0f}%<extra></extra>",
                    showlegend=False,
                ),
                row=1, col=col_i+1
            )
            fig_comp.add_hline(y=0, line_dash="dash", line_color="rgba(200,200,200,0.4)", line_width=1, row=1, col=col_i+1)

        fig_comp.update_layout(
            title=dict(text="🔵 KODEX vs 비교군 변화율", font_size=15, x=0),
            template="plotly_dark",
            height=320,
            margin=dict(t=60, b=40, l=20, r=20),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        fig_comp.update_yaxes(title_text="비교군 대비 초과(%)", gridcolor="rgba(255,255,255,0.08)", row=1, col=1)
        st.plotly_chart(fig_comp, use_container_width=True)

st.markdown("---")

# ── LP 감지 & Z-score 설명 (1회 표시) ──
with st.expander("🔬 LP 노이즈 감지 & 지표 전환 기준", expanded=False):
    st.markdown("""
**LP(유동성공급자)란?**
ETF 시장에서 설정·해지 헤징을 위해 기계적으로 매수·매도하는 증권사 트레이더.
마케팅과 무관한 거래라 DiD 왜곡 원인이 됩니다.

**감지 조건 (둘 다 해당 시 의심):**
- z > 2.0 : 이번 주 금융투자 값이 8주 평균에서 표준편차 2배 이상 벗어남
- 부호 반전 : 8주 평균은 음수인데 이번 주는 양수 (또는 반대)

단, 비교군(TIGER 등)도 같은 패턴이면 → LP 아닌 **장세 전환**으로 처리

**감지 시 조치:** 금융투자 → 개인 컬럼으로 전환 후 DiD 재계산 (추정값 표시)

> ※ z=2.0 임계값은 통계적 관례(95% 신뢰구간). 금리인하·지정학 이슈 등 장세 전반 전환 시 오탐 가능 — 당일 시장 상황 병행 확인 권장
""")

# ── ETF별 상세 계산 과정 ──
for code, res in did_results.items():
    c_map = {"🟢":"#28a745","🟡":"#ffc107","⚪":"#6c757d","🔴":"#dc3545","⚫":"#343a40"}
    border_c = c_map.get(res.judgement_emoji, "#6c757d")
    metric_label = "금융투자" if (res.lp and res.lp.use_metric == "financial") else "개인"

    with st.expander(
        f"{res.judgement_emoji} {res.kodex_name}  |  {score_label(res)}  —  {res.judgement}",
        expanded=False
    ):
        # ── 상단: 핵심 수치 3컬럼 ──
        c1, c2, c3 = st.columns(3)
        c1.metric("KODEX 변화율", f"{int(res.kodex_change_pct*100):+d}%", help="평소 대비")
        c2.metric("비교군 평균", f"{int(res.control_avg_pct*100):+d}%" if not res.no_competitors else "N/A")
        _score = getattr(res, 'marketing_score', 50.0)
        c3.metric("마케팅 점수 (0~100)", f"{_score:.0f}점",
                  delta=res.judgement,
                  delta_color="normal" if _score >= 60 else ("off" if _score >= 40 else "inverse"))

        # ── 베이스라인 부족 경고 ──
        bw = res.baseline.weeks_used if res.baseline else None
        if bw is not None and bw < 8:
            st.warning(
                f"⚠️ 베이스라인 {bw}주만 확보 (8주 미만) — 신규 상장 ETF로 데이터 부족. "
                f"DiD 신뢰도 낮음. {8 - bw}주 더 쌓이면 정상화됩니다."
            )

        # ── LP 상태 + 지표 한 줄 ──
        if res.lp:
            lp_badge = '<span class="badge-lp">⚠️ LP 의심</span>' if res.lp.suspicious else '<span class="badge-ok">✅ 정상</span>'
            st.markdown(
                f"<small>{lp_badge} &nbsp;|&nbsp; 사용 지표: <b>{metric_label}</b> &nbsp;|&nbsp; {res.lp.note}</small>",
                unsafe_allow_html=True
            )

        st.divider()

        # ── 비교군 그리드 ──
        if res.competitors:
            provider_colors = {"TIGER":"#f4a261","ACE":"#e76f51","PLUS":"#2a9d8f","SOL":"#e9c46a"}
            cards = ""
            for comp in res.competitors:
                c = provider_colors.get(comp.provider, "#adb5bd")
                pct_disp = f"{int(comp.change_pct*100):+d}%"
                cur = comp.current_fi if comp.metric_used=="financial" else comp.current_ind
                short2 = comp.name.replace("TIGER ","").replace("PLUS ","").replace("ACE ","").replace("SOL ","").replace("RISE ","").replace("HANARO ","")
                initial2 = comp.provider[0] if comp.provider else "?"
                cards += (
                    f'<div style="flex:1; min-width:110px; border:2px solid {c}; border-radius:24px; '
                    f'padding:14px 10px; text-align:center; background:#16181c;">'
                    f'<div style="width:32px;height:32px;border-radius:9999px;background:{c}20;color:{c};'
                    f'display:flex;align-items:center;justify-content:center;font-size:.7rem;font-weight:700;'
                    f'margin:0 auto 6px;">{initial2}</div>'
                    f'<div style="font-size:.68rem;color:{c};font-weight:700;letter-spacing:.05em;">{comp.provider}</div>'
                    f'<div style="font-size:.95rem;font-weight:700;color:#e8eaed;">{short2}</div>'
                    f'<div style="font-size:1.1rem;font-weight:700;color:{c};font-family:monospace;">{pct_disp}</div>'
                    f'<div style="font-size:.68rem;color:#5b616e;">{cur/1e6:.1f}M</div>'
                    + (f'<div style="font-size:.65rem;color:#888;margin-top:4px;">r={comp.corr:.3f}</div>' if comp.corr is not None else '')
                    + f'</div>'
                )
            st.markdown(f'<div class="comp-grid">{cards}</div>', unsafe_allow_html=True)
            if len(res.competitors) == 1:
                st.caption("※ 동일 유형 ETF 1종만 존재 — 단일 비교 (÷1)")
        # ── DiD 계산식 (이쁘게) ──
        if not res.no_competitors and res.lp and res.current and res.baseline:
            metric = res.lp.use_metric
            cur_val  = res.current.financial_investment if metric=="financial" else res.current.individual
            avg_val  = res.baseline.fi_avg  if metric=="financial" else res.baseline.ind_avg
            mabs_val = res.baseline.fi_mabs if metric=="financial" else res.baseline.ind_mabs
            n = len(res.competitors)
            single_note = f"\n\n  ※ 비교군 1개만 존재 (÷1 적용)" if n == 1 else ""
            # 비교군 각각 중간 계산 표시
            comp_lines = ""
            for comp in res.competitors:
                c_cur  = comp.current_fi   if metric=="financial" else comp.current_ind
                c_avg  = comp.baseline_fi_avg  if metric=="financial" else comp.baseline_ind_avg
                c_mabs = (getattr(comp, 'baseline_fi_mabs', None) or getattr(comp, 'fi_mabs', None) or 1_000_000) if metric == "financial" else (getattr(comp, 'baseline_ind_mabs', None) or getattr(comp, 'ind_mabs', None) or 1_000_000)
                c_raw_mabs = c_mabs - 1_000_000
                comp_lines += (
                    f"     · {comp.name}: ({c_cur:,.0f} − {c_avg:,.0f}) ÷ (mean(절댓값) {c_raw_mabs:,.0f} + 100만 = {c_mabs:,.0f}) = {int(comp.change_pct*100):+d}%\n"
                )
            ctrl_str = " + ".join(f"{int(c.change_pct*100):+d}%" for c in res.competitors)
            raw_mabs_val = mabs_val - 1_000_000
            formula = (
                f"[ 지표: {metric_label} ]\n\n"
                f"  ① KODEX = ({cur_val:,.0f} − {avg_val:,.0f}) ÷ (mean(절댓값) {raw_mabs_val:,.0f} + 100만 = {mabs_val:,.0f})\n"
                f"          = {int(res.kodex_change_pct*100):+d}%\n\n"
                f"  ② 비교군 (각 ETF):\n"
                f"{comp_lines}"
                f"     평균  = ({ctrl_str}) ÷ {n} = {int(res.control_avg_pct*100):+d}%\n\n"
                f"  ③ DiD   = ① − ② = {int(res.kodex_change_pct*100):+d}% − {int(res.control_avg_pct*100):+d}% = {float(res.raw_did_value or 0):+.4f}\n\n"
                f"  ④ Z-score = (DiD − 이력평균) ÷ 이력표준편차 = {float(res.zscore or 0):+.3f}\n"
                f"  ⑤ 점수   = 100 ÷ (1 + exp(−Z×1.5)) = {float(res.marketing_score or 50):.1f}점\n\n"
                f"  판정   {res.judgement_emoji} {res.judgement}\n"
                f"  기준   ≥75점: 마케팅 효과 있음 / ≥60점: 효과 있을 수 있음 / ≥40점: 중립 / <40점: 경쟁사 우위{single_note}"
            )
            st.markdown(f"<div class='formula-box'>{formula}</div>", unsafe_allow_html=True)

        # ── 전체 로그 (이쁘게) ──
        with st.expander("📋 단계별 계산 로그", expanded=False):
            log_html = ""
            icons = {"[KODEX":"🟦","[베이스라인":"📊","[LP":"🔬","[비교군":"🆚","[DiD":"🧮","[판정":"🏁","[비교군 기준":"⚖️","[비교군 매핑":"🗺️"}
            for line in res.calculation_log:
                icon = "▸"
                for k, v in icons.items():
                    if line.startswith(k):
                        icon = v
                        break
                color = "#4d9fff" if "KODEX" in line[:15] else \
                        "#f4a261" if "비교군" in line[:10] else \
                        "#4ec880" if "판정" in line else \
                        "#e9c46a" if "LP" in line[:5] else "inherit"
                log_html += f"<div style='padding:3px 0; border-bottom:1px solid rgba(255,255,255,0.04);'><span style='opacity:.5;margin-right:6px;'>{icon}</span><span style='color:{color};font-size:0.82rem;font-family:monospace;'>{line}</span></div>"
            st.markdown(f"<div style='padding:8px;'>{log_html}</div>", unsafe_allow_html=True)

        if res.notes:
            st.warning("  |  ".join(res.notes))


