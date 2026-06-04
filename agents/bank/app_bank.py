"""
은행 채널 KODEX ETF 마케팅 효과 측정 Agent UI
증권사 app.py와 동일한 Step 구조, 완전 격리
기준 컬럼: 은행 / 이벤트 감지: 순매수 이상감지(역방향) + 뉴스/유튜브
"""

import io
import os
import sys
import re
from datetime import datetime, timedelta, date

import streamlit as st
import pandas as pd

# exec()로 실행 시 __file__ 신뢰 불가 → app.py 위치 기준으로 ROOT 결정
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
# fallback: streamlit 실행 디렉토리
if not os.path.exists(os.path.join(ROOT, "krx_data_fetcher.py")):
    ROOT = os.getcwd()
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv()

from krx_data_fetcher import (
    load_cache_recent, save_cache,
    fetch_weekly_etf_data, get_week_dates, BASELINE_WEEKS
)
import importlib.util

def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_bank_collector = _load_module(os.path.join(ROOT, "agents", "bank", "collector.py"), "bank_collector")
_bank_analyzer  = _load_module(os.path.join(ROOT, "agents", "bank", "analyzer.py"),  "bank_analyzer")

BankChannelCollector = _bank_collector.BankChannelCollector
CHANNEL_LABELS       = _bank_collector.CHANNEL_LABELS
BankAnalyzer         = _bank_analyzer.MarketingAnalyzer

# ── 페이지 설정 ───────────────────────────────────────────────────────────────
st.title("🏦 은행 채널 KODEX ETF 마케팅 효과 측정 Agent")
st.caption("은행 순매수 이상 감지 → ETF 특정 → DiD 분석 → 역추적")

# ── Step Header 스타일 ────────────────────────────────────────────────────────
st.markdown("""
<style>
.step-header {
    font-size:1rem; font-weight:600; color:#ffffff;
    border-left:3px solid #0052ff; padding-left:12px; margin:20px 0 10px;
}
.bank-metric {
    background:#16181c; border:1px solid rgba(0,82,255,0.2);
    border-radius:10px; padding:14px 18px; margin:6px 0;
}
.spike-tag {
    background:rgba(0,255,120,0.12); border:1px solid rgba(0,255,120,0.3);
    color:#00ff78; border-radius:20px; padding:2px 10px;
    font-size:0.72rem; font-weight:700;
}
</style>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════
# Step 1 · 데이터 로드
# ══════════════════════════════════════════════════════════════════
st.markdown('<div class="step-header">Step 1 · 데이터 로드</div>', unsafe_allow_html=True)

all_sheets = {}
base_loaded = False

# KRX 공유 캐시 로드 — 은행은 2단계 Z-score 16주 윈도우 필요
# 1단계 8주 베이스라인 + 2단계 16주 히스토리 + 현재 1주 = 25주 필요
BANK_LOAD_WEEKS = 25
krx_cache = load_cache_recent(BANK_LOAD_WEEKS)
if krx_cache:
    all_sheets = krx_cache
    base_loaded = True
    st.toast(f"✅ 캐시 로드 — 최근 {len(all_sheets)}주차", icon="📊")
elif os.path.exists(os.path.join(ROOT, "ETF 순매수 데이터_260529.xlsx")):
    sys.path.insert(0, ROOT)
    from analyzer import ExcelLoader
    with st.spinner("기본 데이터 로드 중..."):
        all_sheets = ExcelLoader().load(os.path.join(ROOT, "ETF 순매수 데이터_260529.xlsx"))
    base_loaded = True

if not base_loaded:
    st.warning("데이터 없음 — 증권사 Agent에서 KRX 수집 후 다시 시도하세요")
    st.stop()

# 주차 선택 — 금요일 이전이면 현재 주차 제외 (불완전 데이터)
SKIP = {"참고사항", "설명", "README"}
sheet_names = [s for s in all_sheets if s not in SKIP]
if not sheet_names:
    st.error("유효한 주차 없음")
    st.stop()

is_friday = date.today().weekday() == 4  # 0=월 4=금
default_idx = len(sheet_names) - 1
if not is_friday and len(sheet_names) >= 2:
    default_idx = len(sheet_names) - 2  # 전주 완성된 주차
    st.caption("💡 금요일 장 마감 후 이번 주 데이터가 완성됩니다.")

selected = st.selectbox("분석할 주차", sheet_names,
                        index=default_idx, key="bank_week")

with st.expander("📋 미리보기", expanded=False):
    df_prev = all_sheets[selected]
    bank_rows = df_prev[df_prev["종목명"].str.contains("KODEX", na=False)] if "종목명" in df_prev.columns else df_prev
    st.dataframe(bank_rows.head(15), use_container_width=True)

# 주차 변경 또는 앱 재진입 시 수집 결과 초기화
_bank_session_key = f"bank_session_{selected}"
if st.session_state.get("bank_collect_week") != selected:
    st.session_state.pop("bank_collect_results", None)
    st.session_state["bank_collect_week"] = selected

# ══════════════════════════════════════════════════════════════════
# Step 2 · 은행 채널 수집
# ══════════════════════════════════════════════════════════════════
st.markdown('<div class="step-header">Step 2 · 은행 채널 수집</div>', unsafe_allow_html=True)

if st.button("📡 은행 채널 수집 시작", type="primary", use_container_width=True, key="bank_collect"):
    prog_bar = st.progress(0)
    status = st.empty()

    def on_prog(idx, total, name):
        prog_bar.progress(idx / total)
        status.caption(f"수집 중 ({idx}/{total}): {name}")

    with st.spinner("은행 9개 채널 수집 중..."):
        # 선택 주차 기간으로 수집 범위 설정
        import re as _re
        _m = _re.match(r"(\d+)\.(\d+)-(\d+)\.(\d+)", selected)
        if _m:
            _y = date.today().year
            _ws = datetime(_y, int(_m.group(1)), int(_m.group(2)))
            _we = datetime(_y, int(_m.group(3)), int(_m.group(4)), 23, 59)
        else:
            _ws, _we = None, None
        collector = BankChannelCollector(week_start=_ws, week_end=_we)
        results = collector.collect_all(progress_callback=on_prog)
    st.session_state["bank_collect_results"] = results

    # LLM으로 마케팅 활동 판단
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        with st.spinner("Claude가 채널 내용 분석 중..."):
            from agents.bank.analyzer import extract_target_etfs_with_llm
            llm_result = extract_target_etfs_with_llm(results, anthropic_key)
    else:
        llm_result = {"marketing_detected": False, "etf_codes": [], "evidence": []}
    st.session_state["bank_llm_result"] = llm_result

    prog_bar.empty()
    status.empty()
    st.success("✅ 수집 완료")

if "bank_collect_results" in st.session_state:
    results = st.session_state["bank_collect_results"]
    llm_result = st.session_state.get("bank_llm_result", {})

    col_a, col_b = st.columns(2)
    col_a.metric("수집 채널", f"{len(results)}개")
    col_b.metric("마케팅 감지", "있음" if llm_result.get("marketing_detected") else "없음")

    # LLM 판단 결과 표시
    if llm_result.get("marketing_detected"):
        st.success(f"📣 마케팅 활동 감지")
        if llm_result.get("summary"):
            st.caption(llm_result["summary"])

        # 감지 근거 — 채널별 링크
        from collections import defaultdict
        by_channel = defaultdict(list)
        for ev in llm_result.get("evidence", []):
            by_channel[ev.get("channel", "기타")].append(ev)

        for ch_name, evs in by_channel.items():
            with st.expander(f"📡 {ch_name}", expanded=True):
                for ev in evs:
                    title = ev.get("title", "")
                    url   = ev.get("url", "")
                    reason = ev.get("reason", "")
                    link_md = f"[{title}]({url})" if url and url.startswith("http") else f"**{title}**"
                    st.markdown(f"• {link_md}")
                    if reason:
                        st.caption(f"↳ {reason}")
    else:
        st.info("이번 주 은행 채널에서 ETF 마케팅 활동 미감지")

    # 전체 수집 내용 (접어서)
    with st.expander("📋 전체 수집 내용 보기", expanded=False):
        for key, r in results.items():
            items = r.data.get("articles", r.data.get("videos", r.data.get("posts", [])))
            if items:
                st.markdown(f"**{r.channel_name}**")
                for item in items[:3]:
                    title = item.get("title", "")
                    link  = item.get("link", item.get("url", ""))
                    link_md = f"[{title}]({link})" if link and link.startswith("http") else title
                    st.caption(f"  · {link_md}")

analyzer = BankAnalyzer()

# ══════════════════════════════════════════════════════════════════
# Step 3 · ETF별 은행 순매수 DiD
# ══════════════════════════════════════════════════════════════════
st.markdown('<div class="step-header">Step 3 · KODEX ETF 은행 순매수 DiD</div>', unsafe_allow_html=True)

current_df_bank = all_sheets[selected]
_code_col = "단축코드" if "단축코드" in current_df_bank.columns else "종목코드"

# Step 2 수집 결과에서 ETF 이름 추출 (없으면 전체 KODEX로 진행)
detected_etf_codes = []
if "bank_collect_results" in st.session_state:
    _collect = st.session_state["bank_collect_results"]
    all_articles = []
    for r in _collect.values():
        all_articles += r.data.get("articles", [])
        all_articles += [{"title": v["title"]} for v in r.data.get("videos", [])]

    # 뉴스/영상 제목에서 KODEX ETF명 매칭
    kodex_universe = current_df_bank[
        current_df_bank["종목명"].str.contains("KODEX", na=False)
    ][["종목명", _code_col]].dropna()

    for _, etf_row in kodex_universe.iterrows():
        etf_name = str(etf_row["종목명"])
        # KODEX 뒤 핵심 키워드 추출
        keyword = etf_name.replace("KODEX", "").strip()[:8]
        if len(keyword) < 2:
            continue
        for article in all_articles:
            if keyword in str(article.get("title", "")):
                detected_etf_codes.append(str(etf_row[_code_col]))
                break

    detected_etf_codes = list(set(detected_etf_codes))

# DiD는 항상 실행 — Step 2 감지 여부 무관
# Step 2에서 특정 ETF 감지됐으면 해당 ETF만, 없으면 전체 KODEX(레버리지 제외)
llm_etf_codes = st.session_state.get("bank_llm_result", {}).get("etf_codes", [])
if llm_etf_codes:
    st.info(f"📡 채널 감지 ETF {len(llm_etf_codes)}개 기준 분석")
    bank_target_codes = llm_etf_codes
else:
    st.caption("채널 감지 없음 — 전체 KODEX ETF 기준 DiD")
    bank_target_codes = current_df_bank[
        current_df_bank["종목명"].str.contains("KODEX", na=False)
    ][_code_col].tolist()

with st.spinner(f"KODEX ETF {len(bank_target_codes)}개 은행 순매수 DiD 분석 중..."):
    summary = analyzer.analyze(all_sheets, bank_target_codes, selected)
did_results = list(summary.values()) if summary else []

if not did_results:
    st.info("비교군 없음 — 경쟁사 동일 유형 ETF가 데이터에 없습니다.")
else:
    spikes = [r for r in did_results if r.did_value >= 0.5 or r.did_value <= -0.5]

    if spikes:
        st.markdown(f"**이상 감지 ETF: {len(spikes)}개**")
        for r in sorted(spikes, key=lambda x: abs(x.did_value), reverse=True)[:5]:
            direction = "🔺 급증" if r.did_value > 0 else "🔻 급감"
            comp_str = " / ".join([f"{c.name} {c.change_pct*100:+.1f}%" for c in r.competitors])
            st.markdown(
                f"{r.judgement_emoji} **{r.kodex_name}** {direction} **DiD {r.did_value*100:+.1f}%p**"
                f' <span class="spike-tag">SPIKE</span>',
                unsafe_allow_html=True
            )
            st.caption(f"  KODEX {r.kodex_change_pct*100:+.1f}% vs 경쟁사 {r.control_avg_pct*100:+.1f}% | {comp_str}")
    else:
        st.info("이번 주 이상 스파이크 없음 — 평상 범위 내 은행 채널 거래")

    # 전체 테이블
    with st.expander("전체 ETF DiD 결과 (경쟁사 비교)", expanded=True):
        rows = []
        for r in sorted(did_results, key=lambda x: abs(x.did_value), reverse=True):
            comp_names = " / ".join([c.name for c in r.competitors])
            comp_norms = " / ".join([f"{c.change_pct*100:+.1f}%" for c in r.competitors])
            rows.append({
                "판정": f"{r.judgement_emoji} {r.judgement}",
                "KODEX ETF": r.kodex_name,
                "KODEX 은행변화율": f"{r.kodex_change_pct*100:+.1f}%",
                "비교군": comp_names if comp_names else "시장평균",
                "비교군 변화율": comp_norms if comp_norms else f"{r.control_avg_pct*100:+.1f}%",
                "DiD(Z)": f"{r.did_value:+.2f}",
                "매핑출처": r.mapping_source,
                "⚡": "⚡" if abs(r.did_value) >= 1.0 else "",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True)

# ══════════════════════════════════════════════════════════════════
# Step 5 · 요약
# ══════════════════════════════════════════════════════════════════
st.markdown('<div class="step-header">Step 5 · 주간 요약</div>', unsafe_allow_html=True)

spikes = [r for r in did_results if abs(r.did_value) >= 0.5]
spike_names = [r.kodex_name for r in sorted(spikes, key=lambda x: abs(x.did_value), reverse=True)[:3]]

st.markdown(f"**분석 주차:** {selected}")
st.markdown(f"**분석 ETF 수:** {len(did_results)}개")
st.markdown(f"**스파이크 ETF:** {', '.join(spike_names) if spike_names else '없음'}")

if spike_names:
    st.info("💡 은행 채널 유입 이상 감지 — 해당 주 KB/신한/하나/우리/농협 이벤트 역추적 권고")
