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
import importlib as _ilib
import channel_archive as _ch_arch_mod
_ilib.reload(_ch_arch_mod)
has_archive         = _ch_arch_mod.has_archive
save_channel_results = _ch_arch_mod.save_channel_results
load_channel_results = _ch_arch_mod.load_channel_results
get_archived_at     = _ch_arch_mod.get_archived_at
save_raw_data       = _ch_arch_mod.save_raw_data
load_raw_data       = _ch_arch_mod.load_raw_data
from analyzer import COMPARISON_MAP, auto_map_competitors

logger = logging.getLogger(__name__)

# ── 사이드바에서 변수 가져오기 (app.py exec context에서 상속됨) ──────────────
with st.sidebar:
    st.header("⚙️ 설정")
    anthropic_key = st.text_input(
        "Anthropic API Key",
        value=os.getenv("ANTHROPIC_API_KEY", ""),
        type="password",
        key="mass_ant_key",
        help="Anthropic Claude 사용 시 입력"
    )
    gemini_key = st.text_input(
        "Gemini API Key",
        value=os.getenv("GEMINI_API_KEY", ""),
        type="password",
        key="mass_gem_key",
        help="Google Gemini 무료 사용 시 입력 (둘 중 하나만 있으면 됨)"
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

with st.expander("📐 마케팅 점수(0~100) 산정 방식", expanded=False):
    st.markdown("""
**개인(대고객) 채널**은 KODEX 공식 채널·운용사 SNS의 마케팅 활동이 개인투자자 순매수에 미친 영향을 측정합니다.

| 단계 | 내용 |
|------|------|
| ① 변화율 | `(현재 개인순매수 − 8주평균) ÷ (8주절댓값평균 + 라플라스α)` |
| ② DiD | `KODEX 변화율 − 경쟁사평균 변화율` (시장 공통 효과 제거) |
| ③ Z-score | `(이번주 DiD − 15주 DiD평균) ÷ 15주 DiD표준편차` |
| ④ sigmoid 점수 | `100 ÷ (1 + exp(−Z × 1.5))` → 0~100점 |

**판정 기준:** 🟢 ≥75점 효과 있음 / 🟡 ≥60점 가능성 / ⚪ ≥40점 중립 / 🔴 <40점 경쟁사 우위

*기준 컬럼: 개인 순매수 (금융투자·은행 제외)*
    """)

# 아카이브 있으면 버튼 없이 자동 진행
if not st.session_state.get("mass_analysis_run", False):
    if has_archive(f"mass_{current_sheet}") or has_archive(f"mass_llm_{current_sheet}"):
        st.session_state["mass_analysis_run"] = True

if not st.session_state.get("mass_analysis_run", False):
    st.info("📦 이번 주 수집 데이터 없음 — 랜딩 페이지에서 **'🔄 전체 수집 시작'** 을 먼저 실행하세요.")
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
        youtube_api_key=os.getenv("YOUTUBE_API_KEY", ""),
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
    if fail > 0:
        st.caption(f"수집 완료 — 성공 {ok}개 / 미수집 {fail}개 (이번 주 게시물 없거나 YouTube 쿼터 초과 포함)")
    else:
        st.caption(f"수집 완료 — 전체 {ok}개 채널 완료")

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

_mass_llm_arch_key = f"mass_llm_{current_sheet}"

# 캐시에서 LLM 결과 먼저 확인
_mass_llm_failed = False
_cached_llm = load_raw_data(_mass_llm_arch_key)
_mass_cache_valid = bool(
    _cached_llm and not (
        _cached_llm.get("marketing_detected") is False
        and "실패" in _cached_llm.get("summary", "")
    )
)
if _cached_llm and _mass_cache_valid:
    llm_result = _cached_llm
    st.caption("📦 LLM 분석 결과: 캐시 사용")
elif anthropic_key:
    with st.spinner("LLM 분석 중..."):
        llm_result = extract_target_etfs_with_llm(
            collection_results, anthropic_key,
            channel_context="ETF 운용사 전체 (KODEX/TIGER/ACE/RISE/HANARO/SOL) — 개인 투자자 대상 ETF 매수 유도 이벤트·프로모션·혜택 (운용사 구분 없이 모든 ETF 마케팅 포함)"
        )
    _mass_llm_failed = "실패" in llm_result.get("summary", "")
    if _mass_llm_failed:
        st.warning("LLM 호출 실패 — 키워드 기반으로 전환합니다.")
    elif _days_old <= 14:
        save_raw_data(_mass_llm_arch_key, llm_result)

if not anthropic_key or _mass_llm_failed:
    _kw_evidence = []
    _kw_etf_codes = []
    _ETF_KW = ["ETF", "KODEX", "이벤트", "프로모션", "혜택", "매수", "출시", "상장", "수익률"]
    for r in collection_results.values():
        if not r.success or not r.data:
            continue
        items = []
        for v in r.data.get("videos", []):
            items.append({"title": v.get("title",""), "url": v.get("url",""),
                          "pub_date": v.get("published",""), "type": "추천콘텐츠"})
        for e in r.data.get("event_details", []):
            items.append({"title": e.get("title",""), "url": e.get("url",""),
                          "pub_date": e.get("pub_date",""), "type": "이벤트"})
        for p in r.data.get("posts", []):
            items.append({"title": p.get("title",""), "url": p.get("link", p.get("url","")),
                          "pub_date": p.get("pub_date",""), "type": "추천콘텐츠"})
        for item in items:
            t = item["title"]
            if not any(kw in t for kw in _ETF_KW):
                continue
            # ETF 코드 매칭
            matched_codes = []
            for code, name in all_kodex_etfs.items():
                kw = name.replace("KODEX","").strip()[:6]
                if len(kw) >= 2 and kw in t:
                    matched_codes.append(code)
                    _kw_etf_codes.append(code)
            mtype = item["type"]
            if "이벤트" in t or "프로모션" in t or "혜택" in t:
                mtype = "이벤트"
            _kw_evidence.append({
                "title": t[:60],
                "url": item["url"],
                "channel": r.channel_name,
                "marketing_type": mtype,
                "event_period": None,
                "event_summary": f"{r.channel_name}에서 감지된 콘텐츠",
                "etf_codes": matched_codes,
            })
    llm_result = {
        "marketing_detected": bool(_kw_evidence),
        "etf_codes": list(dict.fromkeys(_kw_etf_codes))[:5],
        "evidence": _kw_evidence[:8],
        "summary": f"키워드 기반 감지 (API 키 없음) — {len(_kw_evidence)}건 · ETF 귀속은 부정확할 수 있음",
    }

_type_cls  = {"이벤트":"ev-type-event","프로모션":"ev-type-promo","추천콘텐츠":"ev-type-content","수수료혜택":"ev-type-fee"}
_type_icon = {"이벤트":"🎁","프로모션":"💰","추천콘텐츠":"📺","수수료혜택":"🎯"}

if llm_result.get("marketing_detected"):
    etf_names_det = [all_kodex_etfs.get(c, c) for c in llm_result.get("etf_codes", [])]
    st.success(f"📣 마케팅 활동 감지 — 대상 ETF: **{', '.join(etf_names_det)}**")
    if llm_result.get("summary"):
        st.caption(llm_result["summary"])

    # 이벤트 보드
    evidence = llm_result.get("evidence", [])
    _top_codes = llm_result.get("etf_codes", [])
    for _ev in (evidence or []):
        if not _ev.get("etf_codes") and _top_codes:
            _ev["etf_codes"] = _top_codes
    events_with_info = [ev for ev in (evidence or []) if ev.get("event_summary") or ev.get("event_period") or ev.get("etf_codes")]
    if events_with_info:
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
            ev_etf_codes = ev.get("etf_codes", [])
            ev_etf_names = [all_kodex_etfs.get(c, c) for c in ev_etf_codes if c]
            title_html = f'<a href="{url}" target="_blank" style="color:#e8eaed;text-decoration:none;">{title}</a>' if url and url.startswith("http") else title
            period_html = f'<div class="ev-period">📅 {period}</div>' if period and period != "null" else ""
            etf_html = f'<div style="font-size:.7rem;color:#f0c040;margin-top:4px;">🎯 {", ".join(ev_etf_names)}</div>' if ev_etf_names else ""
            import html as _html_m
            cards_html += (
                f'<div class="ev-card">'
                f'<span class="ev-card-type {cls}">{icon} {mtype}</span>'
                f'<div class="ev-title">{title_html}</div>'
                + period_html + etf_html +
                f'<div class="ev-summary">{_html_m.escape(str(summary)[:120])}</div>'
                f'<div class="ev-channel">출처: {_html_m.escape(str(channel))}</div>'
                f'</div>'
            )
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

# ════════════════════════════════════════════════════════════════════
# STEP 3: 비교군 자동 매핑
# ════════════════════════════════════════════════════════════════════
st.markdown('<div class="step-header">Step 3 · 비교군 매핑</div>', unsafe_allow_html=True)

_code_col2 = "단축코드" if "단축코드" in current_df.columns else "종목코드"
etf_universe = current_df[[_code_col2, "종목명"]].rename(columns={_code_col2: "종목코드"}).dropna(subset=["종목명"])

analyzer = MassAnalyzer()

with st.expander("🔗 비교군 매핑", expanded=True):
    st.caption("📌 매핑 근거: 사전 매핑(수익률 상관계수 0.7+ 검증) → 없으면 실시간 이름 유사도 탐색 → 운용사별 최대 2개")
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
                + corr_line + f'</div>'
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
# STEP 4: 베이스라인 (직전 4주 평균)
# ════════════════════════════════════════════════════════════════════
st.markdown('<div class="step-header">Step 4 · 베이스라인 (직전 4주 평균) · 개인 순매수</div>', unsafe_allow_html=True)

current_idx = sheet_names.index(current_sheet)
history_sheets = {k: all_sheets[k] for k in sheet_names[:current_idx]}

with st.expander("📊 베이스라인 상세", expanded=False):
    for code in target_codes:
        row_etf = analyzer.loader.get_etf_row(current_df, code, code)
        etf_name = row_etf.name if row_etf else code
        bl = analyzer._compute_baseline(code, etf_name, history_sheets)
        cur = analyzer.loader.get_etf_row(current_df, code, etf_name)

        st.markdown(f"**{etf_name}**")
        c1, c2, c3 = st.columns(3)
        c1.metric("이번주 개인",  f"{cur.individual/1e6:.1f}M" if cur else "N/A")
        c2.metric("8주평균 개인", f"{bl.ind_avg/1e6:.1f}M")
        c3.metric("데이터 주수",  f"{bl.weeks_used}주")

        if bl.history:
            hdf = pd.DataFrame(bl.history).rename(columns={"week":"시트","fi":"금융투자","ind":"개인"})

            fig_bl = go.Figure()
            fig_bl.add_trace(go.Scatter(
                x=hdf["시트"], y=hdf["개인"]/1e6,
                mode="lines+markers", name="개인",
                line=dict(color="#f0c040", width=2),
                hovertemplate="%{x}<br>개인: %{y:.1f}M<extra></extra>",
            ))
            if cur:
                fig_bl.add_trace(go.Scatter(
                    x=[current_sheet], y=[cur.individual/1e6],
                    mode="markers", name="이번주(개인)",
                    marker=dict(color="#f0c040", size=12, symbol="star"),
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
# STEP 5: DiD 계산
# ════════════════════════════════════════════════════════════════════
st.markdown('<div class="step-header">Step 5 · DiD 계산 (이중차분법) · 개인 순매수</div>', unsafe_allow_html=True)

with st.spinner("DiD 분석 중..."):
    did_results = analyzer.analyze(all_sheets, target_codes, current_sheet)

if not did_results:
    st.warning("DiD 계산 결과 없음")
    st.stop()

def _score_label_m(res) -> str:
    score = getattr(res, 'marketing_score', 50.0)
    z = getattr(res, 'zscore', 0.0)
    if z != 0.0 or score != 50.0:
        return f"{score:.0f}점"
    return "산출중"

color_map = {"🟢":"#28a745","🟡":"#ffc107","⚪":"#6c757d","🔴":"#dc3545","⚫":"#343a40"}

summary_cols = st.columns(len(did_results))
for col, (code, res) in zip(summary_cols, did_results.items()):
    c = color_map.get(res.judgement_emoji, "#6c757d")
    with col:
        st.markdown(
            f"<div style='border:2px solid {c};border-radius:8px;padding:14px;text-align:center;'>"
            f"<div style='font-size:2rem;'>{res.judgement_emoji}</div>"
            f"<div style='font-weight:700;font-size:0.85rem;'>{res.kodex_name}</div>"
            f"<div style='font-size:1.4rem;font-weight:800;color:{c};'>{_score_label_m(res)}</div>"
            f"<div style='font-size:0.78rem;color:#555;'>{res.judgement}</div>"
            f"</div>", unsafe_allow_html=True)

st.markdown("")

etf_names_did = [r.kodex_name for r in did_results.values()]
score_vals    = [float(getattr(r, 'marketing_score', None) or 50.0) for r in did_results.values()]
zscore_vals   = [float(getattr(r, 'zscore', None) or 0.0) for r in did_results.values()]
bar_colors    = [color_map.get(r.judgement_emoji, "#6c757d") for r in did_results.values()]
short_names   = [n.replace("KODEX ", "") for n in etf_names_did]

fig_did = go.Figure()
for name, short, score, z, color in zip(etf_names_did, short_names, score_vals, zscore_vals, bar_colors):
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
    title="📊 개인 채널 마케팅 점수 (0~100)",
    template="plotly_dark",
    height=max(200, 80 * len(did_results) + 100),
    xaxis=dict(title="마케팅 점수 (0~100)", range=[0, 115], zeroline=False),
    yaxis=dict(autorange="reversed"),
    margin=dict(t=60, b=40, l=10, r=60),
    bargap=0.3,
)
st.plotly_chart(fig_did, use_container_width=True)

st.caption("삼성자산운용 ETF 마케팅 AI Agent · 개인 채널 분석")
