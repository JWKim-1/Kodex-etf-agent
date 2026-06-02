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
from datetime import datetime

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
/* ── Coinbase-inspired design system ── */
/* Primary: #0052ff | Dark canvas: #0a0b0d | Elevated: #16181c */
/* Semantic up: #05b169 | Semantic down: #cf202f */

.step-header {
    font-size:1rem; font-weight:600; color:#0052ff;
    border-left:3px solid #0052ff; padding-left:12px; margin:20px 0 10px;
    letter-spacing:-0.01em;
}
.formula-box {
    border:1px solid rgba(0,82,255,0.2); border-radius:12px;
    padding:16px 20px; font-family:'JetBrains Mono','Courier New',monospace; font-size:0.85rem;
    white-space:pre-wrap; margin:10px 0;
    background:#16181c; color:#e8eaed;
}
.comp-grid { display:flex; gap:12px; margin:12px 0; flex-wrap:nowrap; }
.did-result { font-size:1.4rem; font-weight:700; padding:6px 0; font-family:'JetBrains Mono','Courier New',monospace; }
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
.num { font-family:'JetBrains Mono','Courier New',monospace; font-weight:500; }
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

    st.markdown('<div class="landing-title">📊 ETF 마케팅 효과 측정 AI Agent</div>', unsafe_allow_html=True)
    st.markdown('<div class="landing-sub">채널별 마케팅 활동을 자동 감지하고 이중차분법(DiD)으로 순매수 효과를 정량 측정합니다</div>', unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3, gap="large")

    with col1:
        st.markdown("""
        <div class="mode-card">
            <div class="mode-icon">📈</div>
            <div class="mode-title">증권사 채널</div>
            <div class="mode-desc">증권사의 마케팅 이벤트·유튜브·블로그를 자동 수집하고 KODEX ETF 금융투자 순매수 DiD를 측정합니다</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("증권사 채널 →", key="btn_securities", use_container_width=True, type="primary"):
            st.session_state.selected_mode = "securities"
            st.session_state["analysis_run"] = False
            st.rerun()

    with col2:
        st.markdown("""
        <div class="mode-card disabled">
            <div class="mode-icon">🏦</div>
            <div class="mode-title">은행 채널</div>
            <div class="mode-desc">KB·신한 등 은행의 신탁·퇴직연금 채널에서 ETF 마케팅 활동과 은행 투자자 순매수 효과를 측정합니다</div>
            <div class="coming-soon">🔒 추후 출시 예정</div>
        </div>
        """, unsafe_allow_html=True)
        st.button("은행 채널 (준비 중)", key="btn_bank", use_container_width=True, disabled=True)

    with col3:
        st.markdown("""
        <div class="mode-card disabled">
            <div class="mode-icon">🎯</div>
            <div class="mode-title">대고객 디지털 마케팅</div>
            <div class="mode-desc">삼성자산운용의 직접 디지털 마케팅(이벤트·SNS·유튜브) 효과를 개인 순매수 DiD로 측정합니다</div>
            <div class="coming-soon">🔒 추후 출시 예정</div>
        </div>
        """, unsafe_allow_html=True)
        st.button("자산운용사 채널 (준비 중)", key="btn_amc", use_container_width=True, disabled=True)

    st.markdown("---")
    st.caption("삼성자산운용 ETF 마케팅 모니터링 AI Agent · Powered by Claude")
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
    youtube_key  = ""
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
    """DiD 소수 → '평소 대비 +42%' 형식."""
    p = int(round(v * 100))
    return f"평소 대비 {p:+d}%"

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
    start, _ = _parse_sheet_dates(name)
    if start is None:
        return name
    days = (datetime.now().date() - start).days
    if days == 0:
        return f"{name}  (이번 주)"
    return f"{name}  ({days}일 전)"

def summarize_channel(r) -> str:
    if not r.data: return "데이터 없음"
    d = r.data
    if "videos"   in d: return f"영상 {len(d['videos'])}개 (ETF관련 {sum(1 for v in d['videos'] if v.get('is_etf_related'))}개)"
    if "articles" in d: return f"기사 {len(d['articles'])}건"
    if "events"   in d: return f"이벤트 {len(d['events'])}건"
    if "trends"   in d: return " | ".join(f"{k}: {v['change_pct']:+.1f}%" for k,v in d["trends"].items())
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
        # 채널 유형별 마케팅 분류 근거 명시
        ch = r.channel_name
        for v in d.get("videos", []):
            items.append({"title": v.get("title",""), "url": v.get("url",""),
                          "text": v.get("title",""),
                          "channel_reason": "증권사 공식 유튜브 채널에서 해당 ETF 관련 영상 게시 확인"})
        for p in d.get("posts", []):
            items.append({"title": p.get("title",""), "url": p.get("link",""),
                          "text": p.get("title","") + " " + p.get("description",""),
                          "channel_reason": "증권사 공식 블로그에서 해당 ETF 관련 포스트 게시 확인"})
        for a in d.get("articles", []):
            items.append({"title": a.get("title",""), "url": a.get("link",""),
                          "text": a.get("title","") + " " + a.get("description",""),
                          "channel_reason": "삼성증권 ETF 이벤트 관련 뉴스 기사에서 종목명 확인"})
        for ev in d.get("event_details", []):
            # full_text 있으면 본문까지 활용, 없으면 제목만
            text = ev.get("full_text", ev.get("title",""))
            items.append({"title": ev.get("title",""), "url": ev.get("url",""),
                          "text": text,
                          "channel_reason": "삼성자산운용 공식 이벤트 페이지에 '진행중' 이벤트로 등록됨 (이벤트 제목·기간 명시)"})
        if not d.get("event_details"):
            for e in d.get("events", []):
                items.append({"title": e, "url": d.get("url",""), "text": e,
                              "channel_reason": "이벤트 페이지에서 ETF 관련 내용 확인"})
        if not d.get("event_details") and not d.get("events") and "raw_text" in d:
            items.append({"title": "(이벤트 페이지)", "url": d.get("url",""),
                          "text": d["raw_text"][:1000],
                          "channel_reason": "이벤트 페이지 텍스트에서 ETF명 확인"})

        # 전체 KODEX ETF 이름으로 검색
        for item in items:
            text = item["text"]
            matched_codes, matched_names = [], []

            for code, etf_name in all_kodex_etfs.items():
                if etf_name in text or code in text:
                    matched_codes.append(code)
                    matched_names.append(etf_name)

            if matched_codes:
                for code in matched_codes:
                    if code not in found:
                        found.append(code)
                evidence.append({
                    "channel": r.channel_name,
                    "title": item["title"][:80],
                    "url": item["url"],
                    "reason": f"감지: {', '.join(matched_names[:3])}",
                    "marketing_reason": item.get("channel_reason", ""),
                    "etf_codes": matched_codes[:3],
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
st.title("📊 증권사 채널 KODEX ETF 마케팅 효과 측정 Agent")
st.caption("마케팅 활동 감지 → ETF 특정 → 비교군 매핑 → DiD 분석")

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

# 기본 파일 자동 로드
if os.path.exists(DEFAULT_EXCEL):
    with st.spinner("기본 데이터 로드 중..."):
        all_sheets = load_excel_path(DEFAULT_EXCEL)
    base_loaded = True
else:
    all_sheets = {}
    base_loaded = False

# 신규 주차 파일 추가 업로드
st.header("📂 추가 데이터 업로드")

col_upload = st.container()
with col_upload:
    if base_loaded:
        uploaded_new = st.file_uploader(
            "📄 신규 주차 파일 추가 (선택)",
            type=["xlsx"],
            help="멘토님께 받은 이번 주 파일. 업로드 시 기존 데이터에 자동 병합됩니다.",
            label_visibility="collapsed",
        )
    else:
        uploaded_new = st.file_uploader(
            "📁 ETF 순매수 엑셀 파일 업로드",
            type=["xlsx"],
            help="멘토님께 받은 ETF 순매수 데이터 엑셀 (여러 시트 포함)",
        )
        if uploaded_new:
            file_bytes_base = uploaded_new.read()
            with st.spinner("파일 로드 중..."):
                all_sheets = load_excel(file_bytes_base)
            base_loaded = True
            uploaded_new = None  # 이미 base로 처리됨

if not base_loaded and not uploaded_new:
    st.stop()

# 신규 파일 병합
if uploaded_new:
    new_bytes = uploaded_new.read()
    with st.spinner("신규 파일 병합 중..."):
        new_sheets = load_excel(new_bytes)
    added = []
    for sname, sdf in new_sheets.items():
        if sname not in all_sheets:
            all_sheets[sname] = sdf
            added.append(sname)
    if added:
        st.success(f"✅ 신규 시트 추가됨: {', '.join(added)}")
    else:
        st.info("신규 파일의 시트가 이미 누적 파일에 있습니다. 중복 제외됨.")

# 참고사항 등 데이터 시트가 아닌 것 제외
SKIP_SHEETS = {"참고사항", "설명", "readme", "README", "시트설명"}
sheet_names = [s for s in all_sheets.keys()
               if s not in SKIP_SHEETS and not s.lower().startswith("sheet")]
if not sheet_names:
    st.error("유효한 데이터 시트를 찾지 못했습니다.")
    st.stop()


# 시트명에 날짜 경과 여부 라벨 추가
def _sheet_label(name: str) -> str:
    start, _ = _parse_sheet_dates(name)
    if start is None:
        return name
    days = (datetime.now().date() - start).days
    if days == 0:
        return f"{name}  (이번 주)"
    return f"{name}  ({days}일 전)"

labeled = [_sheet_label(s) for s in sheet_names]
selected_label = st.selectbox("분석할 주차 시트 선택", labeled, index=len(labeled)-1)
current_sheet = sheet_names[labeled.index(selected_label)]

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
    st.dataframe(all_sheets[current_sheet].head(15), use_container_width=True)

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

week_range_str = f"{sheet_start.strftime('%m/%d')}~{sheet_end.strftime('%m/%d')}" if sheet_start else current_sheet

if IS_BACKTEST:
    st.markdown(
        f'<span class="mode-badge-weekly">📡 주간 분석 — {current_sheet}</span>'
        f'<br><small style="opacity:.7;">⚠️ {days_ago}일 전 주차 — 채널 데이터가 일부 또는 전부 없을 수 있음 (RSS 보관 기간 초과 가능). DiD 계산은 정상 수행됩니다.</small>',
        unsafe_allow_html=True)
else:
    st.markdown(
        f'<span class="mode-badge-weekly">📡 주간 분석 — {current_sheet}</span>'
        f'<br><small style="opacity:.7;">채널 수집 기준: <b>{week_range_str}</b></small>',
        unsafe_allow_html=True)
st.markdown("")

if st.button("🚀 분석 시작", type="primary", use_container_width=True):
    st.session_state["analysis_run"] = True

if not st.session_state.get("analysis_run", False):
    st.stop()

# ════════════════════════════════════════════════════════════════════
# STEP 1: 마케팅 채널 수집 (주간 분석 모드만)
# ════════════════════════════════════════════════════════════════════
st.markdown('<div class="step-header">Step 1 · 마케팅 채널 수집</div>', unsafe_allow_html=True)

if IS_BACKTEST:
    st.info(f"📼 백테스팅 모드: {current_sheet}은 {days_ago}일 전 데이터입니다. "
            f"과거 채널 내용을 실시간으로 재수집할 수 없어 채널 수집을 건너뜁니다.")
    collection_results = {}
else:
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
              <div style='font-size:0.65rem; opacity:.45; margin-top:2px;'>{int(pct*100)}% — {name[:35]}</div>
            </div>""",
            unsafe_allow_html=True
        )

    t0 = time.time()
    collection_results = collector.collect_all(progress_callback=on_prog)
    elapsed = time.time() - t0
    ok   = sum(1 for r in collection_results.values() if r.success)
    fail = len(collection_results) - ok
    # 완료: 황소가 바 끝(100%)에 서있고 불 꺼짐
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
          <div style='font-size:0.65rem; opacity:.6; margin-top:2px;'>
            완료 {elapsed:.1f}초 — 성공 {ok}개 / 실패 {fail}개</div>
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
etf_universe = current_df[["종목코드","종목명"]].dropna(subset=["종목명"])
all_kodex_etfs = {
    str(row["종목코드"]): str(row["종목명"])
    for _, row in etf_universe[etf_universe["종목명"].str.contains("KODEX", na=False)].iterrows()
}

if IS_BACKTEST:
    st.info("📼 백테스팅 모드: 분석할 ETF를 아래에서 직접 선택하세요.")
    detected_codes = []
    llm_result = {"marketing_detected": False, "etf_codes": [],
                  "summary": f"백테스팅 모드 — {current_sheet} 채널 수집 불가"}
else:
    with st.spinner("LLM 분석 중..."):

        if anthropic_key:
            llm_result = extract_target_etfs_with_llm(collection_results, anthropic_key)
        else:
            llm_result = keyword_fallback(collection_results, all_kodex_etfs)

    if llm_result.get("marketing_detected"):
        etf_names_det = [all_kodex_etfs.get(c, COMPARISON_MAP.get(c, {}).get("name", c))
                         for c in llm_result.get("etf_codes", [])]
        st.success(f"📣 마케팅 활동 감지 — 대상 ETF: **{', '.join(etf_names_det)}**")
        if llm_result.get("summary"):
            st.caption(llm_result["summary"])

        # 감지 근거 — 탭 형태
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

        if by_channel:
            for ch_name, evs in by_channel.items():
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
    st.warning("감지된 마케팅 활동 없음 — 이번 주 분석을 종료합니다. 베이스라인은 업데이트됩니다.")
    st.stop()

# ════════════════════════════════════════════════════════════════════
# STEP 3: 비교군 자동 매핑 (미리보기 + 확인)
# ════════════════════════════════════════════════════════════════════
st.markdown('<div class="step-header">Step 3 · 비교군 매핑</div>', unsafe_allow_html=True)

with st.expander("🔗 비교군 매핑", expanded=True):
    st.caption("📌 매핑 근거: 네이버 금융 기초지수 일치 우선 → 이름 유사도 → 운용사별 최대 2개 선정")
    analyzer = MarketingAnalyzer()
    for code in target_codes:
        row_etf = analyzer.loader.get_etf_row(current_df, code, code)
        etf_name = row_etf.name if row_etf else code

        if code in COMPARISON_MAP:
            comps = COMPARISON_MAP[code]["competitors"]
        else:
            comps = auto_map_competitors(etf_name, code, etf_universe)

        _pc = {"KODEX":"#4d9fff","TIGER":"#f4a261","ACE":"#e76f51","PLUS":"#2a9d8f","SOL":"#e9c46a","RISE":"#6b9fff","HANARO":"#a78bfa"}
        total_cards = 1 + len(comps)
        card_w = f"flex:1; min-width:0; max-width:calc(100%/{total_cards});"

        def _card(provider, name, code_str, color, label=""):
            initial = provider[0] if provider else "?"
            return (
                f'<div style="{card_w} border:2px solid {color}; border-radius:24px; '
                f'padding:16px 14px; text-align:center; background:#16181c;">'
                f'<div class="prov-badge" style="background:{color}20;color:{color};margin:0 auto 8px;">{initial}</div>'
                f'<div style="font-size:0.7rem;color:{color};font-weight:700;margin-bottom:3px;letter-spacing:.05em;">{provider}</div>'
                f'<div style="font-size:1rem;font-weight:700;color:#e8eaed;line-height:1.2;">{name}</div>'
                f'<div style="font-size:0.68rem;color:#5b616e;margin-top:4px;">{code_str}</div>'
                f'</div>'
            )

        cards_html = '<div style="display:flex; gap:12px; margin:10px 0;">'
        cards_html += _card("KODEX", etf_name.replace("KODEX ",""), code, "#0052ff")
        if comps:
            for comp in comps:
                c = _pc.get(comp['provider'], "#adb5bd")
                short_name = comp["name"].replace("TIGER ","").replace("PLUS ","").replace("ACE ","").replace("SOL ","").replace("RISE ","").replace("HANARO ","")
                cards_html += _card(comp['provider'], short_name, comp["code"], c)
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
st.markdown('<div class="step-header">Step 4 · 베이스라인 (직전 4주 평균) & LP 노이즈 감지</div>', unsafe_allow_html=True)

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
        c3.metric("4주평균 금융투자", f"{bl.fi_avg/1e6:.1f}M")
        c4.metric("4주평균 개인",    f"{bl.ind_avg/1e6:.1f}M")
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

with st.spinner("DiD 분석 중..."):
    did_results = analyzer.analyze(all_sheets, target_codes, current_sheet)

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
                f"<div class='did-result' style='color:{c};'>{did_pct(res.did_value)}</div>"
                f"<div style='font-size:0.78rem;color:#555;'>{res.judgement}</div>"
                f"</div>", unsafe_allow_html=True)

    st.markdown("")

    # DiD 비교 바 차트
    etf_names = [r.kodex_name for r in did_results.values()]
    did_vals   = [r.did_value for r in did_results.values()]
    bar_colors = [color_map.get(r.judgement_emoji, "#6c757d") for r in did_results.values()]

    # ── DiD 결과: 가로 막대 — % 표시 ──
    short_names = [n.replace("KODEX ", "") for n in etf_names]
    did_pct_vals = [v * 100 for v in did_vals]  # % 단위로 변환

    fig_did = go.Figure()
    for name, short, val_raw, val_pct, color in zip(etf_names, short_names, did_vals, did_pct_vals, bar_colors):
        label = f"{val_pct:+.0f}%"
        tpos = "inside" if val_pct < -20 else "outside"
        fig_did.add_trace(go.Bar(
            y=[short], x=[val_pct],
            orientation="h",
            marker_color=color,
            marker_line_width=0,
            text=label,
            textposition=tpos,
            textfont=dict(size=12, color="white"),
            hovertemplate=f"<b>{name}</b><br>평소 대비 {val_pct:+.0f}%<br>(DiD={val_raw:+.3f})<extra></extra>",
            showlegend=False,
        ))
    # x축 범위: 데이터 기반이되 0이 중앙에 오도록
    _max_abs = max(abs(v) for v in did_pct_vals) if did_pct_vals else 100
    _x_range = max(_max_abs * 1.3, 120)
    fig_did.add_vline(x=0,    line_dash="solid", line_color="rgba(200,200,200,0.5)", line_width=1.5)
    fig_did.add_vline(x=100,  line_dash="dot",   line_color="#28a745", line_width=1.5,
                      annotation=dict(text="+100%", font_color="#28a745", font_size=10, y=1.08))
    fig_did.add_vline(x=30,   line_dash="dot",   line_color="#ffc107", line_width=1.5,
                      annotation=dict(text="+30%", font_color="#ffc107", font_size=10, y=1.08))
    fig_did.add_vline(x=-30,  line_dash="dot",   line_color="#dc3545", line_width=1.5,
                      annotation=dict(text="-30%", font_color="#dc3545", font_size=10, y=1.08))
    fig_did.add_vline(x=-100, line_dash="dot",   line_color="#dc3545", line_width=1,
                      annotation=dict(text="-100%", font_color="#dc3545", font_size=10, y=1.08))
    fig_did.update_layout(
        title=dict(text="📊 ETF별 DiD 마케팅 효과", font_size=15, x=0),
        xaxis=dict(title="비교군 대비 초과 순매수 (%)", range=[-_x_range, _x_range],
                   gridcolor="rgba(255,255,255,0.08)", zeroline=False),
        yaxis=dict(title="", autorange="reversed", tickfont=dict(size=12)),
        template="plotly_dark",
        height=max(180, len(did_results) * 72 + 100),
        margin=dict(t=70, b=40, l=10, r=120),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_did, use_container_width=True)

    # ── KODEX vs 비교군 그룹 바 차트 ──
    chart_rows = []
    for res in did_results.values():
        short = res.kodex_name.replace("KODEX ", "")
        chart_rows.append({"ETF": short, "구분": "KODEX", "변화율": res.kodex_change_pct * 100, "order": 0, "no_comp": res.no_competitors})
        if res.competitors:
            for i, comp in enumerate(res.competitors):
                label = comp.provider
                chart_rows.append({"ETF": short, "구분": label, "변화율": comp.change_pct * 100, "order": i+1, "no_comp": False})
        else:
            # 비교군 없을 때 더미 막대 (0, 빗금 표시용)
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

# ── ETF별 상세 계산 과정 ──
for code, res in did_results.items():
    c_map = {"🟢":"#28a745","🟡":"#ffc107","⚪":"#6c757d","🔴":"#dc3545","⚫":"#343a40"}
    border_c = c_map.get(res.judgement_emoji, "#6c757d")
    metric_label = "금융투자" if res.lp.use_metric == "financial" else "개인"

    with st.expander(
        f"{res.judgement_emoji} {res.kodex_name}  |  {did_pct(res.did_value)}  —  {res.judgement}",
        expanded=False
    ):
        # ── 상단: 핵심 수치 3컬럼 ──
        c1, c2, c3 = st.columns(3)
        c1.metric("KODEX 변화율", f"{int(res.kodex_change_pct*100):+d}%", help="평소 대비")
        c2.metric("비교군 평균", f"{int(res.control_avg_pct*100):+d}%" if not res.no_competitors else "N/A")
        c3.metric("DiD (마케팅 효과)", did_pct(res.did_value),
                  delta=res.judgement,
                  delta_color="normal" if res.did_value >= 0.3 else ("off" if res.did_value >= -0.3 else "inverse"))

        # ── LP 상태 + 지표 한 줄 ──
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
                    f'</div>'
                )
            st.markdown(f'<div class="comp-grid">{cards}</div>', unsafe_allow_html=True)
            if len(res.competitors) == 1:
                st.caption("※ 동일 유형 ETF 1종만 존재 — 단일 비교 (÷1)")
        # ── DiD 계산식 (이쁘게) ──
        if not res.no_competitors:
            metric = res.lp.use_metric
            cur_val  = res.current.financial_investment if metric=="financial" else res.current.individual
            avg_val  = res.baseline.fi_avg  if metric=="financial" else res.baseline.ind_avg
            mabs_val = res.baseline.fi_mabs if metric=="financial" else res.baseline.ind_mabs
            ctrl_str = " + ".join(f"{int(c.change_pct*100):+d}%" for c in res.competitors)
            n = len(res.competitors)
            single_note = f"\n\n  ※ 비교군 1개만 존재 (÷1 적용)" if n == 1 else ""
            formula = (
                f"[ 지표: {metric_label} ]\n\n"
                f"  ① KODEX = ({cur_val:,.0f} − {avg_val:,.0f}) ÷ {mabs_val:,.0f}\n"
                f"          = {int(res.kodex_change_pct*100):+d}%\n\n"
                f"  ② 비교군 = ({ctrl_str}) ÷ {n}\n"
                f"           = {int(res.control_avg_pct*100):+d}%\n\n"
                f"  ③ DiD   = ① − ② = {did_pct(res.did_value)}\n\n"
                f"  판정   {res.judgement_emoji} {res.judgement}\n"
                f"  기준   >+100%: 강함 / >+30%: 효과있음 / <-30%: 불분명 / <-100%: 확인어려움{single_note}"
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

# ════════════════════════════════════════════════════════════════════
# STEP 6: 리포트 생성 + HTML 내보내기
# ════════════════════════════════════════════════════════════════════
st.markdown('<div class="step-header">Step 6 · 주간 리포트 생성</div>', unsafe_allow_html=True)

week_label = current_sheet
trends_r = collection_results.get("google_trends") if collection_results else None
trends_data = trends_r.data.get("trends") if (trends_r and trends_r.success and trends_r.data) else None

report_data = build_report(
    collection_results=collection_results,
    llm_result=llm_result,
    did_results=did_results,
    week_label=week_label,
    google_trends_data=trends_data,
)

with st.expander("💡 마케팅 개선 제안", expanded=True):
    st.markdown(
        "<div style='text-align:center; padding:24px; opacity:.5;'>"
        "🔒 추후 출시 예정<br>"
        "<small>DiD 누적 데이터 기반 LLM 마케팅 제안 자동 생성</small>"
        "</div>",
        unsafe_allow_html=True
    )

html_content = export_html(report_data)
dl1, dl2 = st.columns(2)
with dl1:
    st.download_button("⬇️ HTML 리포트 다운로드", data=html_content.encode("utf-8"),
                       file_name=f"kodex_report_{week_label}.html", mime="text/html",
                       type="primary", use_container_width=True)
with dl2:
    st.download_button("⬇️ JSON 데이터 다운로드",
                       data=json.dumps(report_data, ensure_ascii=False, indent=2).encode("utf-8"),
                       file_name=f"kodex_report_{week_label}.json", mime="application/json",
                       use_container_width=True)

with st.expander("🌐 HTML 리포트 미리보기", expanded=False):
    b64 = base64.b64encode(html_content.encode("utf-8")).decode()
    st.markdown(
        f'<iframe src="data:text/html;base64,{b64}" width="100%" height="640px" '
        f'style="border:1px solid #ddd;border-radius:6px;"></iframe>',
        unsafe_allow_html=True)

