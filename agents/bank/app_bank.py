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

# pickle이 동적 로드 모듈을 직렬화할 수 있도록 sys.modules에 등록
import sys as _sys_mod
_sys_mod.modules.setdefault("bank_collector", _bank_collector)
_sys_mod.modules.setdefault("bank_analyzer",  _bank_analyzer)

BankChannelCollector = _bank_collector.BankChannelCollector
CHANNEL_LABELS       = _bank_collector.CHANNEL_LABELS
BankAnalyzer         = _bank_analyzer.MarketingAnalyzer

# ── 페이지 설정 ───────────────────────────────────────────────────────────────
st.title("🏦 은행 채널 KODEX ETF 마케팅 효과 측정 Agent")
st.caption("은행 순매수 이상 감지 → ETF 특정 → DiD 분석 → 역추적")

with st.sidebar:
    st.header("⚙️ LLM 설정")
    _bank_ant = st.text_input("Anthropic API Key", value=os.getenv("ANTHROPIC_API_KEY",""), type="password", key="bank_ant_key", help="Anthropic Claude 사용 시")
    _bank_gem = st.text_input("Gemini API Key",    value=os.getenv("GEMINI_API_KEY",""),    type="password", key="bank_gem_key", help="Google Gemini 무료 사용 시")
    if _bank_gem: os.environ["GEMINI_API_KEY"] = _bank_gem

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
.formula-box {
    border:1px solid rgba(0,82,255,0.2); border-radius:12px;
    padding:16px 20px; font-family:'Pretendard','JetBrains Mono','D2Coding','Courier New',monospace;
    font-size:0.85rem; white-space:pre-wrap; margin:10px 0;
    background:#16181c; color:#e8eaed;
}
.comp-grid { display:flex; gap:12px; margin:12px 0; flex-wrap:wrap; }
.did-result { font-size:1.4rem; font-weight:700; padding:6px 0;
              font-family:'Pretendard','JetBrains Mono','D2Coding','Courier New',monospace; }
.badge-ok  { background:rgba(40,167,69,0.15); color:#28a745; padding:3px 10px;
             border-radius:100px; font-size:0.72rem; font-weight:600; border:1px solid rgba(40,167,69,0.3); }
.ev-board { display:flex; gap:12px; flex-wrap:wrap; margin:12px 0; }
.ev-card {
    flex:1; min-width:220px; max-width:320px;
    border:1px solid rgba(0,82,255,0.25); border-radius:14px;
    padding:14px 16px; background:rgba(0,82,255,0.05);
}
.ev-card-type {
    font-size:.68rem; font-weight:700; padding:2px 8px; border-radius:100px;
    display:inline-block; margin-bottom:6px;
}
.ev-type-event   { background:rgba(0,198,255,0.15);color:#00c6ff;border:1px solid rgba(0,198,255,0.3); }
.ev-type-promo   { background:rgba(5,177,105,0.15);color:#05b169;border:1px solid rgba(5,177,105,0.3); }
.ev-type-content { background:rgba(255,200,50,0.15);color:#f0c040;border:1px solid rgba(255,200,50,0.3); }
.ev-type-fee     { background:rgba(167,139,250,0.15);color:#a78bfa;border:1px solid rgba(167,139,250,0.3); }
.ev-type-etc     { background:rgba(255,255,255,0.08);color:#aaa;border:1px solid rgba(255,255,255,0.15); }
.ev-title { font-size:.88rem; font-weight:700; color:#e8eaed; margin-bottom:4px; line-height:1.4; }
.ev-period { font-size:.75rem; color:#4d9fff; margin:4px 0; }
.ev-summary { font-size:.78rem; color:#aaa; line-height:1.5; margin:6px 0 0; }
.ev-channel { font-size:.68rem; color:#666; margin-top:6px; }
</style>
""", unsafe_allow_html=True)

def _did_pct(v: float) -> str:
    p = int(round(v * 100))
    return f"평소 대비 {p:+d}%"

# ══════════════════════════════════════════════════════════════════
# Step 1 · 데이터 로드
# ══════════════════════════════════════════════════════════════════
st.markdown('<div class="step-header">Step 1 · 데이터 로드</div>', unsafe_allow_html=True)

all_sheets = {}
base_loaded = False

# KRX 공유 캐시 로드 — 은행은 2단계 Z-score 16주 윈도우 필요
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
    st.warning("데이터 없음 — KRX 수집 후 다시 시도하세요")
    st.stop()

# ── KRX 신규 주차 수집 (매주 금요일 장 마감 후 1회) ──
with st.expander("🔄 신규 주차 데이터 수집 (매주 1회)", expanded=False):
    st.caption("매주 금요일 장 마감 후 이번 주 데이터 추가. 수집 후 다음부터는 분석 시작만 누르면 됩니다.")
    from datetime import date as _date
    _today = _date.today()
    _monday = _today - timedelta(days=_today.weekday())
    _friday = _monday + timedelta(days=4)
    col_d1, col_d2, col_btn = st.columns([2, 2, 2])
    krx_start = col_d1.date_input("시작일", value=_monday, key="bank_krx_start")
    krx_end   = col_d2.date_input("종료일", value=_friday, key="bank_krx_end")
    if col_btn.button("🔄 KRX 수집", type="primary", use_container_width=True, key="bank_krx_btn"):
        try:
            from krx_data_fetcher import fetch_weekly_etf_data, load_cache, save_cache
            with st.spinner("KRX 수집 중... (수분 소요)"):
                new_df = fetch_weekly_etf_data(krx_start, krx_end)
            if not new_df.empty:
                label = f"{krx_start.month}.{krx_start.day}-{krx_end.month}.{krx_end.day}"
                existing = load_cache()
                existing[label] = new_df
                save_cache(existing)
                st.success(f"✅ {label} 수집 완료 — {len(new_df)}개 ETF")
                st.rerun()
            else:
                st.warning("수집된 데이터 없음 — 날짜 확인 또는 KRX 세션 재시도")
        except Exception as e:
            st.error(f"수집 실패: {e}")

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

def _krx_label(w: str) -> str:
    """KRX 캐시 주차 라벨 표시용 변환.
    멘토님 엑셀은 목요일까지만 있어 '5.25-5.28',
    KRX 직접 수집분은 월~금 5일치라 끝날을 금요일로 표시."""
    import re as _re
    m = _re.match(r"(\d{1,2})\.(\d{1,2})-(\d{1,2})\.(\d{1,2})", w)
    if m:
        sm, sd, em, ed = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        # 끝날이 목요일(28일처럼 짝수-1)이면 +1해서 금요일로 보정
        try:
            from datetime import date as _d
            end_date = _d(2026, em, ed)
            if end_date.weekday() == 3:  # 목요일이면
                end_date = _d(2026, em, ed + 1)
                return f"{sm}.{sd}-{end_date.month}.{end_date.day}"
        except Exception:
            pass
    return w

labeled_weeks = {_krx_label(s): s for s in sheet_names}  # 표시라벨 → 실제키
label_list = list(labeled_weeks.keys())
selected_label = st.selectbox("분석할 주차", label_list,
                               index=default_idx, key="bank_week")
selected = labeled_weeks[selected_label]  # 실제 캐시 키

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

# 과거 주차면 채널 수집 의미 없음 (RSS 보관 기간 초과 가능)
from datetime import date as _today_d
import re as _re2
_bm = _re2.match(r"(\d{1,2})\.(\d{1,2})", selected or "")
if _bm:
    try:
        _sel_start_d = _today_d(_today_d.today().year, int(_bm.group(1)), int(_bm.group(2)))
    except Exception:
        _sel_start_d = None
else:
    _sel_start_d = None
_days_ago_bank = (_today_d.today() - _sel_start_d).days if _sel_start_d else 0
if _days_ago_bank > 14:
    st.info(f"📼 {selected}은 {_days_ago_bank}일 전 주차 — RSS 보관 기간 초과로 채널 수집이 부정확할 수 있습니다. DiD 분석은 정상 수행됩니다.")

# 아카이브에서 자동 로드 (버튼 없이) — importlib로 강제 재로드해 캐시 문제 방지
_bank_arch_key = f"bank_{selected}"
_bank_llm_arch_key = f"bank_llm_{selected}"
import importlib as _ilib, channel_archive as _ch_arch_mod
_ilib.reload(_ch_arch_mod)
_bank_has_arch   = _ch_arch_mod.has_archive
_bank_load_ch    = _ch_arch_mod.load_channel_results
_bank_load_raw   = _ch_arch_mod.load_raw_data
_bank_arch_at    = _ch_arch_mod.get_archived_at

if "bank_collect_results" not in st.session_state and _bank_has_arch(_bank_arch_key):
    st.session_state["bank_collect_results"] = _bank_load_ch(_bank_arch_key)
    _cached_llm = _bank_load_raw(_bank_llm_arch_key)
    if _cached_llm:
        st.session_state["bank_llm_result"] = _cached_llm
    _bat = _bank_arch_at(_bank_arch_key)
    st.caption(f"📦 보존된 수집 결과 자동 로드 (최초 수집: {_bat})")

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
        collector = BankChannelCollector(week_start=_ws, week_end=_we,
                                         youtube_api_key=os.getenv("YOUTUBE_API_KEY", ""))
        results = collector.collect_all(progress_callback=on_prog)
    st.session_state["bank_collect_results"] = results

    # LLM으로 마케팅 활동 판단
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "") or st.session_state.get("bank_ant_key","")
    gemini_key    = os.getenv("GEMINI_API_KEY", "")    or st.session_state.get("bank_gem_key","")
    if anthropic_key or gemini_key:
        os.environ["GEMINI_API_KEY"] = gemini_key
        with st.spinner("LLM이 채널 내용 분석 중..."):
            from agents.bank.analyzer import extract_target_etfs_with_llm
            llm_result = extract_target_etfs_with_llm(results, anthropic_key)
    else:
        llm_result = {"marketing_detected": False, "etf_codes": [], "evidence": []}
    st.session_state["bank_llm_result"] = llm_result

    # 아카이브 저장
    if _days_ago_bank <= 14:
        from channel_archive import save_channel_results as _bank_save, save_raw_data as _bank_save_raw
        _bank_save(_bank_arch_key, results)
        _bank_save_raw(_bank_llm_arch_key, llm_result)

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
        evidence = llm_result.get("evidence", [])
        st.success(f"📣 마케팅 활동 감지 — {len(evidence)}건")
        if llm_result.get("summary"):
            st.caption(llm_result["summary"])

        # 이벤트 카드 보드
        _type_cls  = {"이벤트":"ev-type-event","프로모션":"ev-type-promo","추천콘텐츠":"ev-type-content","수수료혜택":"ev-type-fee"}
        _type_icon = {"이벤트":"🎁","프로모션":"💰","추천콘텐츠":"📺","수수료혜택":"🎯"}
        if evidence:
            cards_html = '<div class="ev-board">'
            for ev in evidence[:8]:
                mtype      = ev.get("marketing_type", "기타")
                cls        = _type_cls.get(mtype, "ev-type-etc")
                icon       = _type_icon.get(mtype, "📋")
                title      = (ev.get("title") or "")[:60]
                period     = ev.get("event_period") or ""
                summary    = ev.get("event_summary") or ev.get("marketing_reason") or ev.get("reason") or ""
                channel    = ev.get("channel", "")
                url        = ev.get("url", "")
                target_etf = ev.get("target_etf") or ""
                title_html  = f'<a href="{url}" target="_blank" style="color:#e8eaed;text-decoration:none;">{title}</a>' if url and url.startswith("http") else title
                period_html = f'<div class="ev-period">📅 {period}</div>' if period and period not in ("","null") else ""
                etf_html    = f'<div class="ev-etf" style="color:#05b169;">🎯 {target_etf}</div>' if target_etf and target_etf != "null" else ""
                img_html    = f'<div class="ev-card-img-placeholder" style="background:rgba(5,177,105,0.08);">🏦</div>'
                cards_html += (
                    f'<div class="ev-card" style="border-color:#05b16933;">'
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
                    # 링크 있으면 링크, 없으면 제목만 (글 내용 전체 표시 방지)
                    if link and link.startswith("http"):
                        link_md = f"[{title[:60]}]({link})"
                    else:
                        link_md = title[:60]  # 최대 60자로 제한
                    st.caption(f"  · {link_md}")

analyzer = BankAnalyzer()

# ══════════════════════════════════════════════════════════════════
# Step 3 · ETF별 은행 순매수 DiD
# ══════════════════════════════════════════════════════════════════
st.markdown('<div class="step-header">Step 3 · KODEX ETF 은행 순매수 DiD</div>', unsafe_allow_html=True)

current_df_bank = all_sheets[selected]
_code_col = "종목코드" if "종목코드" in current_df_bank.columns else "단축코드"

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
all_kodex = current_df_bank[current_df_bank["종목명"].str.contains("KODEX", na=False)]
bank_active_cnt = all_kodex[all_kodex["은행"].notna() & (all_kodex["은행"] != 0.0)].shape[0]
st.caption(f"KODEX {len(all_kodex)}개 중 은행 거래 있는 ETF: {bank_active_cnt}개")

llm_etf_codes = st.session_state.get("bank_llm_result", {}).get("etf_codes", [])
if llm_etf_codes:
    st.info(f"📡 채널 감지 ETF {len(llm_etf_codes)}개 기준 분석")
    bank_target_codes = llm_etf_codes
else:
    st.caption("채널 감지 없음 — 전체 KODEX ETF 기준 DiD")
    bank_target_codes = all_kodex[_code_col].tolist()

# 분석 결과 캐시 — pickle로 객체 전체 저장, 리로드 시 즉시 복원
import pickle as _pickle
import sys as _sys, os as _os
_sys.path.insert(0, ROOT)
from did_history import save_results as _save_did

_cache_key = f"bank_did_{selected}_{len(bank_target_codes)}"
_pkl_dir  = _os.path.join(ROOT, ".did_cache")
_os.makedirs(_pkl_dir, exist_ok=True)
_pkl_path = _os.path.join(_pkl_dir, f"bank_{selected.replace('.','_').replace('-','_')}.pkl")

if st.session_state.get("bank_did_key") == _cache_key:
    # 같은 세션 내 — session_state 사용
    summary = st.session_state.get("bank_did_result", {})
elif _os.path.exists(_pkl_path):
    # 이전 실행 결과 pickle 복원 — 재계산 없음
    with open(_pkl_path, "rb") as _f:
        summary = _pickle.load(_f)
    st.session_state["bank_did_result"] = summary
    st.session_state["bank_did_key"] = _cache_key
    st.caption("💾 이전 분석 결과 복원 (캐시)")
else:
    with st.spinner(f"KODEX ETF {len(bank_target_codes)}개 은행 순매수 DiD 분석 중… (첫 실행만 소요)"):
        summary = analyzer.analyze(all_sheets, bank_target_codes, selected)
    st.session_state["bank_did_result"] = summary
    st.session_state["bank_did_key"] = _cache_key
    # pickle 저장 (다음 리로드 시 즉시 복원용)
    with open(_pkl_path, "wb") as _f:
        _pickle.dump(summary, _f)
    # parquet 요약 저장
    _save_did(selected, [
        {"code": c, "name": r.kodex_name, "did": r.did_value,
         "judgement": r.judgement, "marketing_detected": True,
         "no_competitors": r.no_competitors}
        for c, r in summary.items()
    ], channel_type="bank")

did_results = list(summary.values()) if summary else []

if not did_results:
    st.info("비교군 없음 — 경쟁사 동일 유형 ETF가 데이터에 없습니다.")
else:
    c_map = {"🟢":"#28a745","🟡":"#ffc107","⚪":"#6c757d","🔴":"#dc3545","⚫":"#343a40"}
    provider_colors = {"TIGER":"#f4a261","ACE":"#e76f51","PLUS":"#2a9d8f","SOL":"#e9c46a","RISE":"#6b9fff","HANARO":"#a78bfa"}

    sorted_results = sorted(did_results, key=lambda x: abs(x.did_value), reverse=True)
    spikes    = [r for r in sorted_results if abs(r.did_value) >= 1.0]
    normals   = [r for r in sorted_results if abs(r.did_value) < 1.0]

    def _z_label(z: float) -> str:
        """Z-score → 직관적 설명"""
        if z >= 2.0:    return f"Z={z:+.2f} — 평소 변동성의 {z:.1f}배 🔺"
        elif z >= 1.0:  return f"Z={z:+.2f} — 이례적 상승 ⚠️"
        elif z <= -2.0: return f"Z={z:+.2f} — 경쟁사 대비 {abs(z):.1f}배 부진 🔻"
        elif z <= -1.0: return f"Z={z:+.2f} — 경쟁사 우위"
        else:           return f"Z={z:+.2f} — 정상 범위"

    # ── 이상 감지 ETF 카드 ──
    if spikes:
        st.markdown(f"**⚡ 이상 감지: {len(spikes)}개** (평소 변동의 1배 이상)")
        cols = st.columns(min(len(spikes), 4))
        for col, r in zip(cols, spikes):
            c = c_map.get(r.judgement_emoji, "#6c757d")
            with col:
                st.markdown(
                    f"<div style='border:2px solid {c};border-radius:8px;padding:14px;text-align:center;'>"
                    f"<div style='font-size:2rem;'>{r.judgement_emoji}</div>"
                    f"<div style='font-weight:700;font-size:0.85rem;'>{r.kodex_name}</div>"
                    f"<div class='did-result' style='color:{c};font-size:1rem;'>{_z_label(r.did_value)}</div>"
                    f"</div>", unsafe_allow_html=True)
    else:
        st.info("이번 주 이상 변동 없음 — 모든 KODEX ETF 정상 범위")

    st.divider()

    # ── 이상 ETF 상세 expander ──
    for r in spikes:
        border_c = c_map.get(r.judgement_emoji, "#6c757d")
        with st.expander(
            f"{r.judgement_emoji} {r.kodex_name}  |  {_z_label(r.did_value)}  —  {r.judgement}",
            expanded=False
        ):
            c1, c2, c3 = st.columns(3)
            c1.metric("KODEX 은행 변화율", f"{int(r.kodex_change_pct*100):+d}%")
            c2.metric("비교군 평균", f"{int(r.control_avg_pct*100):+d}%" if not r.no_competitors else "N/A")
            c3.metric("평소 대비 이상 정도", _z_label(r.did_value),
                      delta_color="normal" if r.did_value >= 1.0 else ("off" if r.did_value >= -1.0 else "inverse"))

            bw = r.baseline.weeks_used
            if bw < 4:
                st.warning(f"⚠️ 베이스라인 {bw}주만 확보 — 신규 상장 ETF. {4-bw}주 더 쌓이면 정상화됩니다.")

            st.divider()

            # 비교군 카드
            if r.competitors:
                cards = ""
                for comp in r.competitors:
                    c = provider_colors.get(comp.provider, "#adb5bd")
                    short2 = comp.name
                    for pfx in ["TIGER ","PLUS ","ACE ","SOL ","RISE ","HANARO "]: short2 = short2.replace(pfx,"")
                    cards += (
                        f'<div style="flex:1;min-width:110px;border:2px solid {c};border-radius:24px;'
                        f'padding:14px 10px;text-align:center;background:#16181c;">'
                        f'<div style="font-size:.68rem;color:{c};font-weight:700;">{comp.provider}</div>'
                        f'<div style="font-size:.95rem;font-weight:700;color:#e8eaed;">{short2}</div>'
                        f'<div style="font-size:1.1rem;font-weight:700;color:{c};font-family:monospace;">'
                        f'{int(comp.change_pct*100):+d}%</div></div>'
                    )
                st.markdown(f'<div class="comp-grid">{cards}</div>', unsafe_allow_html=True)

            # 계산 결과 ①②③④
            if not r.no_competitors:
                ctrl_str = " + ".join(f"{c.change_pct:+.4f}" for c in r.competitors)
                ctrl_pct_str = " + ".join(f"{int(c.change_pct*100):+d}%" for c in r.competitors)
                n = len(r.competitors)
                raw_did = getattr(r, "raw_did_value", r.kodex_change_pct - r.control_avg_pct)
                z = r.did_value  # 2단계 이후 Z-score
                z_label = _z_label(z)
                formula = (
                    f"[ 은행 컬럼 · {r.mapping_source} ]\n\n"
                    f"  ① KODEX 은행변화율   = {r.kodex_change_pct:+.4f}  (≈ {int(r.kodex_change_pct*100):+d}%p)\n"
                    f"  ② 비교군 {n}개 평균  = ({ctrl_str}) ÷ {n} = {r.control_avg_pct:+.4f}  (≈ {int(r.control_avg_pct*100):+d}%p)\n\n"
                    f"  ③ 1단계 DiD          = {r.kodex_change_pct:+.4f} − {r.control_avg_pct:+.4f} = {raw_did:+.4f}  (≈ {int(raw_did*100):+d}%p)\n\n"
                    f"  ④ 2단계 Z-score      = {z:+.4f}  ({z_label})\n"
                    f"     (이번주 DiD를 16주 평균·표준편차로 표준화)\n\n"
                    f"  판정  {r.judgement_emoji} {r.judgement}"
                )
                st.markdown(f"<div class='formula-box'>{formula}</div>", unsafe_allow_html=True)

            # 단계별 계산 로그 (LP 관련 제외)
            with st.expander("📋 단계별 계산 로그", expanded=False):
                log_html = ""
                icons = {
                    "[KODEX":"🟦","[베이스라인":"📊","[비교군":"🆚",
                    "[DiD":"🧮","[2단계":"📐","[판정":"🏁","[최종판정":"🏁",
                }
                for line in r.calculation_log:
                    if "[LP" in line or "LP 감지" in line: continue
                    icon = "▸"
                    for k, v in icons.items():
                        if line.startswith(k): icon = v; break
                    color = "#4d9fff" if "KODEX" in line[:15] else \
                            "#f4a261" if "비교군" in line[:10] else \
                            "#4ec880" if "판정" in line else \
                            "#a78bfa" if "2단계" in line[:8] else "inherit"
                    log_html += (f"<div style='padding:3px 0;border-bottom:1px solid rgba(255,255,255,0.04);'>"
                                 f"<span style='opacity:.5;margin-right:6px;'>{icon}</span>"
                                 f"<span style='color:{color};font-size:0.82rem;font-family:monospace;'>{line}</span></div>")
                st.markdown(f"<div style='padding:8px;'>{log_html}</div>", unsafe_allow_html=True)

            if r.notes:
                st.warning("  |  ".join(r.notes))

    # ── 정상 범위 ETF 한곳에 테이블로 ──
    if normals:
        with st.expander(f"📋 정상 범위 ETF {len(normals)}개 (이상 없음)", expanded=False):
            rows = []
            for r in normals:
                comp_names = " / ".join(c.name for c in r.competitors)
                rows.append({
                    "판정": f"{r.judgement_emoji} {r.judgement}",
                    "KODEX ETF": r.kodex_name,
                    "KODEX 변화율": f"{int(r.kodex_change_pct*100):+d}%",
                    "비교군": comp_names if comp_names else "없음",
                    "비교군 변화율": f"{int(r.control_avg_pct*100):+d}%",
                    "이상 정도": _z_label(r.did_value),
                    "매핑": r.mapping_source,
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True)

# ══════════════════════════════════════════════════════════════════
# Step 5 · 요약
# ══════════════════════════════════════════════════════════════════
st.markdown('<div class="step-header">Step 5 · 주간 요약</div>', unsafe_allow_html=True)

spikes = [r for r in did_results if abs(r.did_value) >= 1.0]  # 카드/테이블과 동일 기준
spike_names = [r.kodex_name for r in sorted(spikes, key=lambda x: abs(x.did_value), reverse=True)[:3]]

st.markdown(f"**분석 주차:** {selected}")
st.markdown(f"**분석 ETF 수:** {len(did_results)}개")
st.markdown(f"**이상 감지 ETF (Z≥1.0):** {len(spikes)}개")
st.markdown(f"**상위 3개:** {', '.join(spike_names) if spike_names else '없음'}")

if spike_names:
    st.info("💡 은행 채널 유입 이상 감지 — 해당 주 KB/신한/하나/우리/농협 이벤트 역추적 권고")
