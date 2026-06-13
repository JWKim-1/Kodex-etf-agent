"""
개인 채널 DiD 분석 — 삼성자산운용 직접 채널 (KODEX 유튜브·이벤트·뉴스)
대상 컬럼: 개인 순매수 (LP 노이즈 없음)
"""

import os, sys, json, re, logging
from datetime import datetime, timedelta, date
from typing import Dict, List

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd
import numpy as np
import streamlit as st
import plotly.graph_objects as go

from collector import DataCollector
from agents.mass.analyzer import MassAnalyzer
from analyzer import extract_target_etfs_with_llm
from krx_data_fetcher import load_cache_recent, load_cache, save_cache, _parse_week_label
from channel_archive import has_archive, save_channel_results, load_channel_results, get_archived_at

logger = logging.getLogger(__name__)

# ── 사이드바에서 변수 가져오기 (app.py exec context에서 상속됨) ──────────────
anthropic_key = st.session_state.get("_anthropic_key", "")
if not anthropic_key:
    with st.sidebar:
        st.header("⚙️ 설정")
        anthropic_key = st.text_input(
            "Anthropic API Key",
            value=os.getenv("ANTHROPIC_API_KEY", ""),
            type="password",
            help="마케팅 감지 LLM 분석용"
        )

# ── KRX 데이터 로드 ───────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def _load_krx():
    return load_cache_recent()

all_sheets = _load_krx()
if not all_sheets:
    all_sheets = load_cache()

if not all_sheets:
    st.error("KRX 캐시 데이터 없음. 먼저 증권사 채널 탭에서 분석을 실행해 주세요.")
    st.stop()

today_date = datetime.now().date()
_today = today_date
sheet_names = sorted(
    [s for s in all_sheets.keys() if _parse_week_label(s) is None or _parse_week_label(s) <= _today],
    key=lambda w: _parse_week_label(w) or date.min,
)
if not sheet_names:
    st.error("유효한 시트 없음")
    st.stop()

def _sheet_label(name):
    d = _parse_week_label(name)
    if d is None: return name
    days = (today_date - d).days
    return f"{name}  (이번 주)" if days == 0 else f"{name}  ({days}일 전)"

labeled = [_sheet_label(s) for s in sheet_names]
_is_friday = datetime.now().weekday() == 4
_default_idx = len(labeled) - 1
if not _is_friday and len(labeled) >= 2:
    _default_idx = len(labeled) - 2
    st.caption("💡 금요일 장 마감 후 이번 주 데이터가 완성됩니다.")

selected_label = st.selectbox("분석할 주차 시트 선택", labeled, index=_default_idx, key="mass_sheet")
current_sheet = sheet_names[labeled.index(selected_label)]

# 날짜 파싱
def _parse_sheet_dates(name):
    m = re.findall(r"(\d{1,2})[.\-](\d{1,2})", name)
    if not m: return None, None
    now = datetime.now()
    def to_date(mon, day):
        month, day = int(mon), int(day)
        year = now.year if month <= now.month else now.year - 1
        try: return date(year, month, day)
        except: return None
    start = to_date(*m[0])
    end   = to_date(*m[-1]) if len(m) > 1 else start
    return start, end

sheet_start, sheet_end = _parse_sheet_dates(current_sheet)
days_ago = (today_date - sheet_start).days if sheet_start else 0
IS_BACKTEST = sheet_start is not None and days_ago > 14

week_start_dt = datetime(sheet_start.year, sheet_start.month, sheet_start.day) if sheet_start else None
week_end_dt   = datetime(sheet_end.year, sheet_end.month, sheet_end.day, 23, 59) if sheet_end else None
_real_end = sheet_end + timedelta(days=1) if (sheet_end and sheet_end.weekday() == 3) else sheet_end
week_range_str = f"{sheet_start.strftime('%m/%d')}~{_real_end.strftime('%m/%d')}" if sheet_start else current_sheet

current_df = all_sheets[current_sheet]

# ── 개인 컬럼 존재 확인 ───────────────────────────────────────────────────────
_has_individual = "개인" in current_df.columns
if not _has_individual:
    st.error("현재 데이터에 '개인' 순매수 컬럼이 없습니다. KRX 데이터를 재수집해 주세요.")
    st.stop()

st.header("🎯 개인 채널 분석")
st.caption(f"채널: 삼성자산운용 직접 채널 (KODEX 유튜브·이벤트·뉴스) | 기준: 개인 순매수 | {week_range_str}")

if st.button("🚀 분석 시작", type="primary", use_container_width=True, key="mass_run"):
    st.session_state["mass_analysis_run"] = True

if not st.session_state.get("mass_analysis_run", False):
    st.stop()

# ── STEP 1: 채널 수집 ─────────────────────────────────────────────────────────
st.markdown('<div class="step-header">Step 1 · 삼성자산운용 채널 수집</div>', unsafe_allow_html=True)

_days_old = (today_date - sheet_start).days if sheet_start else 0
_from_archive = False

if has_archive(f"mass_{current_sheet}"):
    collection_results = load_channel_results(f"mass_{current_sheet}")
    _from_archive = True
    _archived_at = get_archived_at(f"mass_{current_sheet}")
    st.caption(f"📦 보존된 결과 사용 (최초 수집: {_archived_at})")
else:
    collector = DataCollector(
        youtube_api_key="",
        naver_client_id=os.getenv("NAVER_CLIENT_ID", ""),
        naver_client_secret=os.getenv("NAVER_CLIENT_SECRET", ""),
        anthropic_api_key=anthropic_key,
        week_start=week_start_dt,
        week_end=week_end_dt,
    )

    prog_ph = st.empty()
    def on_prog(idx, total, name):
        prog_ph.progress(idx / total, text=f"수집 중: {name}")

    collection_results = collector.collect_all_mass(progress_callback=on_prog)
    prog_ph.empty()

    ok = sum(1 for r in collection_results.values() if r.success)
    fail = len(collection_results) - ok
    st.caption(f"수집 완료 — 성공 {ok}개 / 실패 {fail}개")

    if _days_old <= 14:
        save_channel_results(f"mass_{current_sheet}", collection_results)

# 채널별 간단 표시
with st.expander("📡 채널별 상세", expanded=False):
    for r in collection_results.values():
        icon = "✅" if r.success else "❌"
        st.markdown(f"{icon} **{r.channel_name}**" + (f" — {r.error_label or r.error}" if not r.success else ""))

# ── STEP 2: LLM 마케팅 감지 ──────────────────────────────────────────────────
st.markdown('<div class="step-header">Step 2 · 마케팅 감지 (삼성자산운용 직접 채널)</div>', unsafe_allow_html=True)

# 전체 KODEX ETF 목록
_code_col = "단축코드" if "단축코드" in current_df.columns else "종목코드"
all_kodex_etfs = {
    row[_code_col].split("*")[0].strip(): row["종목명"]
    for _, row in current_df.iterrows()
    if "KODEX" in str(row.get("종목명", ""))
}

with st.spinner("LLM 분석 중..."):
    if anthropic_key:
        # 프롬프트를 개인 채널 기준으로 조정
        llm_result = extract_target_etfs_with_llm(
            collection_results, anthropic_key,
            channel_context="삼성자산운용 KODEX ETF 직접 채널 (유튜브, 이벤트, 뉴스)"
        )
    else:
        detected_etfs = []
        for r in collection_results.values():
            if r.success and r.data:
                etf_names = r.data.get("etf_names", [])
                for n in etf_names:
                    for code, name in all_kodex_etfs.items():
                        if any(kw in n for kw in name.split()[:2]):
                            detected_etfs.append(code)
        llm_result = {
            "marketing_detected": bool(detected_etfs),
            "etf_codes": list(set(detected_etfs))[:3],
            "summary": "키워드 기반 감지 (API 키 없음)",
        }

if llm_result.get("marketing_detected"):
    etf_names_det = [all_kodex_etfs.get(c, c) for c in llm_result.get("etf_codes", [])]
    st.success(f"📣 마케팅 활동 감지 — 대상 ETF: **{', '.join(etf_names_det)}**")
    if llm_result.get("summary"):
        st.caption(llm_result["summary"])

    # ── 이벤트 보드 ───────────────────────────────────────────────────────────
    evidence = llm_result.get("evidence", [])
    events_with_info = [ev for ev in (evidence or []) if ev.get("event_summary") or ev.get("event_period")]
    if events_with_info:
        _type_cls = {"이벤트":"ev-type-event","프로모션":"ev-type-promo","추천콘텐츠":"ev-type-content","수수료혜택":"ev-type-fee"}
        _type_icon = {"이벤트":"🎁","프로모션":"💰","추천콘텐츠":"📺","수수료혜택":"🎯"}
        cards_html = '<div class="ev-board">'
        for ev in events_with_info[:6]:
            mtype = ev.get("marketing_type","기타")
            cls = _type_cls.get(mtype,"ev-type-etc")
            icon = _type_icon.get(mtype,"📋")
            title = ev.get("title","")[:60]
            period = ev.get("event_period") or ""
            summary = ev.get("event_summary") or ev.get("reason") or ""
            channel = ev.get("channel","")
            url = ev.get("url","")
            title_html = f'<a href="{url}" target="_blank" style="color:#e8eaed;text-decoration:none;">{title}</a>' if url and url.startswith("http") else title
            period_html = f'<div class="ev-period">📅 {period}</div>' if period and period != "null" else ""
            cards_html += f"""
            <div class="ev-card">
              <span class="ev-card-type {cls}">{icon} {mtype}</span>
              <div class="ev-title">{title_html}</div>
              {period_html}
              <div class="ev-summary">{summary[:120]}</div>
              <div class="ev-channel">출처: {channel}</div>
            </div>"""
        cards_html += "</div>"
        st.markdown(cards_html, unsafe_allow_html=True)
else:
    st.warning("이번 주 마케팅 활동 없음 — 베이스라인 업데이트만 수행됩니다.")
    if llm_result.get("summary"):
        st.caption(llm_result["summary"])

detected_codes = llm_result.get("etf_codes", [])
target_codes = detected_codes

if not target_codes:
    if IS_BACKTEST:
        st.info("채널 감지 없음 — 직접 선택하세요.")
        all_kodex_names = {v: k for k, v in all_kodex_etfs.items()}
        selected_names = st.multiselect("분석할 ETF 선택", sorted(all_kodex_names.keys()), key="mass_etf_select")
        if not selected_names:
            st.stop()
        target_codes = [all_kodex_names[n] for n in selected_names]
    else:
        st.warning("감지된 마케팅 없음 — 종료합니다.")
        st.stop()

# ── STEP 3: DiD 계산 ─────────────────────────────────────────────────────────
st.markdown('<div class="step-header">Step 3 · 개인 순매수 DiD 계산</div>', unsafe_allow_html=True)
st.caption("기준 컬럼: **개인 순매수** | LP 노이즈 없음 | 4주 베이스라인")

analyzer = MassAnalyzer()
did_results = analyzer.analyze(all_sheets, target_codes, current_sheet)

if not did_results:
    st.warning("DiD 계산 결과 없음")
    st.stop()

# ── 결과 표시 ─────────────────────────────────────────────────────────────────
st.markdown('<div class="step-header">Step 4 · 결과</div>', unsafe_allow_html=True)

for code, res in did_results.items():
    did_val = getattr(res, "did_score", None) or getattr(res, "did", None)
    etf_name = getattr(res, "etf_name", code)
    sign = "+" if (did_val or 0) >= 0 else ""
    color = "#05b169" if (did_val or 0) >= 0 else "#cf202f"

    st.markdown(f"""
    <div style="border:1px solid rgba(255,255,255,0.1);border-radius:12px;padding:16px 20px;margin:8px 0;">
      <div style="font-size:1rem;font-weight:700;margin-bottom:4px;">{etf_name}</div>
      <div style="font-size:1.8rem;font-weight:800;color:{color};font-family:'JetBrains Mono',monospace;">
        {sign}{(did_val or 0)*100:.1f}% <span style="font-size:0.9rem;opacity:.6;">DiD</span>
      </div>
      <div style="font-size:0.8rem;opacity:.5;margin-top:4px;">개인 순매수 기준</div>
    </div>
    """, unsafe_allow_html=True)

st.caption("삼성자산운용 ETF 마케팅 AI Agent · 개인 채널 분석")
