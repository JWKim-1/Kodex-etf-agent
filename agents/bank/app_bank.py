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
import importlib.util as _ilu

def _load_bank_mod(filename, mod_name):
    path = os.path.join(ROOT, "agents", "bank", filename)
    spec = _ilu.spec_from_file_location(mod_name, path)
    mod = _ilu.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod

_bank_collector_mod = _load_bank_mod("collector.py", "bank_collector")
_bank_analyzer_mod  = _load_bank_mod("analyzer.py",  "analyzer")

BankChannelCollector = _bank_collector_mod.BankChannelCollector
CHANNEL_LABELS       = _bank_collector_mod.CHANNEL_LABELS
BankAnalyzer         = _bank_analyzer_mod.MarketingAnalyzer

# ── 페이지 설정 ───────────────────────────────────────────────────────────────
st.title("🏦 은행 채널 KODEX ETF 마케팅 효과 측정 Agent")

with st.expander("📐 마케팅 점수(0~100) 산정 방식", expanded=False):
    st.markdown("""
**은행 채널은 사전 마케팅 감지가 불가능**하므로(앱 내부 운영), 역방향으로 순매수 이상을 감지합니다.

| 단계 | 내용 |
|------|------|
| ① 1단계 DiD | `KODEX 은행변화율 − 경쟁사 은행변화율` |
| ② 2단계 Z-score | `(이번주 DiD − 16주 DiD평균) ÷ 16주 DiD표준편차` |
| ③ sigmoid 점수 | `100 ÷ (1 + exp(−Z × 1.5))` → 0~100점 |

**점수의 통계적 의미:**
경쟁사 대비 초과 은행 순매수 변화가 이번 주만큼 크게 발생할 확률이 과거 이력(16주) 분포에서 얼마나 드문가를 나타냅니다.

| 점수 | Z-score | 통계적 의미 |
|------|---------|------------|
| 88점 | +2.0 | 과거 이력 중 상위 2% — 매우 드문 초과 변화 |
| 81점 | +1.5 | 과거 이력 중 상위 7% — 통계적으로 유의미 |
| 73점 | +1.0 | 과거 이력 중 상위 16% — 다소 두드러짐 |
| 50점 | 0.0 | 경쟁사 대비 평균 수준 (중립) |
| 27점 | −1.0 | 과거 이력 중 하위 16% — 경쟁사 상대 열위 |

**베이스라인 부족 시 (신규상장 등 8주 미만) — AUM 상대강도 방식:**

이력이 부족해 Z-score 기반 점수를 산출할 수 없을 때, 이번 주 순매수를 시가총액(AUM)으로 나눈 비율로 비교합니다.

```
KODEX 비율 = 이번 주 은행순매수 / KODEX AUM(시총)
경쟁사 비율 = 이번 주 경쟁사 은행순매수 / 경쟁사 AUM
AUM DiD = KODEX비율 − 경쟁사평균비율
```

AUM으로 나누는 이유: ETF 크기가 달라도 동등하게 비교 가능 (체급 차이 제거)

- **AUM DiD > 0** → KODEX에 경쟁사 대비 상대적으로 더 많은 자금 유입
- **AUM DiD < 0** → 경쟁사에 상대적으로 더 많은 자금 유입
- sigmoid 변환 후 0~100점으로 표현 (50점 = 경쟁사와 동일 수준)

**판정 기준:** 🟢 Z≥2.0(95점↑) 강한 이상 감지 / 🟡 Z≥1.5(90점↑) 이상 감지 / ⚪ 정상 변동 / 🔴 Z≤-1.5 경쟁사 우위 · ⚠️ 비교군없음 = 절대변화율(참고용)
    """)
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
    st.info("📊 KRX 데이터 없음 — 랜딩 페이지에서 **'📊 KRX 수집'** 을 먼저 실행하세요. (VPN 필수)")
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
_saved_bank = st.session_state.get("bank_sheet_key")
if _saved_bank and _saved_bank in sheet_names:
    _bk_label = _krx_label(_saved_bank)
    if _bk_label in label_list:
        default_idx = label_list.index(_bk_label)
selected_label = st.selectbox("분석할 주차", label_list, index=default_idx, key="bank_week")
selected = labeled_weeks[selected_label]
st.session_state["bank_sheet_key"] = selected  # 실제 캐시 키

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

if "bank_collect_results" not in st.session_state:
    st.info("📦 이번 주 수집 데이터 없음 — 랜딩 페이지에서 **'🔄 전체 수집 시작'** 을 먼저 실행하세요.")
    st.stop()

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
    try:
        with open(_pkl_path, "rb") as _f:
            summary = _pickle.load(_f)
        st.session_state["bank_did_result"] = summary
        st.session_state["bank_did_key"] = _cache_key
        st.caption("💾 이전 분석 결과 복원 (캐시)")
    except Exception:
        # 손상된 pkl 삭제 후 재계산
        _os.remove(_pkl_path)
        summary = None
    if summary is None:
        with st.spinner(f"KODEX ETF {len(bank_target_codes)}개 은행 순매수 DiD 분석 중… (캐시 손상, 재계산)"):
            summary = analyzer.analyze(all_sheets, bank_target_codes, selected)
        st.session_state["bank_did_result"] = summary
        st.session_state["bank_did_key"] = _cache_key
        with open(_pkl_path, "wb") as _f:
            _pickle.dump(summary, _f)
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

    def _score_label(r) -> str:
        score = getattr(r, 'marketing_score', 50.0)
        z = getattr(r, 'zscore', r.did_value)
        return f"{score:.0f}점 (Z={z:+.2f})"

    sorted_results = sorted(did_results, key=lambda x: getattr(x, 'marketing_score', 50.0), reverse=True)
    spikes    = [r for r in sorted_results if getattr(r, 'marketing_score', 50.0) >= 90 or getattr(r, 'marketing_score', 50.0) <= 10]
    normals   = [r for r in sorted_results if r not in spikes]

    # ── 이상 감지 ETF 카드 ──
    if spikes:
        st.markdown(f"**⚡ 이상 감지: {len(spikes)}개** (Z≥1.5 또는 Z≤-1.5 기준)")
        cols = st.columns(min(len(spikes), 4))
        for col, r in zip(cols, spikes):
            c = c_map.get(r.judgement_emoji, "#6c757d")
            with col:
                _no_comp_badge = "<div style='font-size:0.65rem;color:#aaa;margin-top:2px;'>⚠️ 비교군없음</div>" if r.no_competitors else ""
                st.markdown(
                    f"<div style='border:2px solid {c};border-radius:8px;padding:14px;text-align:center;'>"
                    f"<div style='font-size:2rem;'>{r.judgement_emoji}</div>"
                    f"<div style='font-weight:700;font-size:0.85rem;'>{r.kodex_name}</div>"
                    f"<div class='did-result' style='color:{c};font-size:1rem;'>{_score_label(r)}</div>"
                    f"{_no_comp_badge}"
                    f"</div>", unsafe_allow_html=True)
    else:
        st.info("이번 주 이상 변동 없음 — 모든 KODEX ETF 정상 범위")

    st.divider()

    # ── 이상 ETF 상세 expander ──
    for r in spikes:
        border_c = c_map.get(r.judgement_emoji, "#6c757d")
        with st.expander(
            f"{r.judgement_emoji} {r.kodex_name}  |  {_score_label(r)}  —  {r.judgement}",
            expanded=False
        ):
            c1, c2, c3 = st.columns(3)
            c1.metric("KODEX 은행 변화율", f"{int(r.kodex_change_pct*100):+d}%")
            c2.metric("비교군 평균", f"{int(r.control_avg_pct*100):+d}%" if not r.no_competitors else "N/A")
            _score_b = getattr(r, 'marketing_score', 50.0)
            c3.metric("마케팅 점수 (0~100)", f"{_score_b:.0f}점",
                      delta=r.judgement,
                      delta_color="normal" if _score_b >= 60 else ("off" if _score_b >= 40 else "inverse"))

            bw = r.baseline.weeks_used
            if bw < 8:
                st.warning(f"⚠️ 베이스라인 {bw}주만 확보 — 신규 상장 ETF. {8-bw}주 더 쌓이면 정상화됩니다.")

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
                        f'{int(comp.change_pct*100):+d}%</div>'
                        + (f'<div style="font-size:.65rem;color:#888;margin-top:4px;">r={comp.corr:.3f}</div>' if comp.corr is not None else '')
                        + '</div>'
                    )
                st.markdown(f'<div class="comp-grid">{cards}</div>', unsafe_allow_html=True)

            # 계산 전 과정 표시
            if not r.no_competitors:
                n = len(r.competitors)
                raw_did = getattr(r, "raw_did_value", r.kodex_change_pct - r.control_avg_pct)
                z = getattr(r, 'zscore', r.did_value)
                score_b = getattr(r, 'marketing_score', 50.0)
                z_label = f"{score_b:.0f}점 (Z={z:+.2f})"

                # calculation_log에서 핵심 라인 추출
                _log_kodex, _log_base, _log_comp, _log_did, _log_z1, _log_z2, _log_z3 = "", "", "", "", "", "", ""
                for line in r.calculation_log:
                    if "[LP" in line or "LP 감지" in line: continue
                    if "[KODEX 순매수]" in line:    _log_kodex = line
                    elif "[베이스라인]" in line:     _log_base  = line
                    elif "[비교군 지표]" in line:   _log_comp  = line
                    elif "[DiD 계산]" in line:      _log_did   = line
                    elif "DiD 이력:" in line:       _log_z1    = line
                    elif "16주평균=" in line:        _log_z2    = line
                    elif "Z = (" in line:           _log_z3    = line

                comp_lines = "\n".join(
                    f"     {c.provider} {c.name[:20]}: {c.change_pct:+.4f}"
                    for c in r.competitors
                )
                ctrl_str = " + ".join(f"{c.change_pct:+.4f}" for c in r.competitors)

                formula = (
                    f"[ 은행 컬럼 · {r.mapping_source} ]\n\n"
                    f"━━ STEP 1 · KODEX 변화율 ━━\n"
                    + (f"  {_log_kodex}\n" if _log_kodex else "")
                    + (f"  {_log_base}\n" if _log_base else "")
                    + f"  ▶ KODEX 변화율 = {r.kodex_change_pct:+.4f}  (≈ {int(r.kodex_change_pct*100):+d}%p)\n\n"
                    f"━━ STEP 2 · 비교군 변화율 ━━\n"
                    f"{comp_lines}\n"
                    + (f"  {_log_comp}\n" if _log_comp else "")
                    + f"  ▶ 비교군 {n}개 평균 = ({ctrl_str}) ÷ {n} = {r.control_avg_pct:+.4f}  (≈ {int(r.control_avg_pct*100):+d}%p)\n\n"
                    f"━━ STEP 3 · 1단계 DiD ━━\n"
                    + (f"  {_log_did}\n" if _log_did else "")
                    + f"  ▶ DiD = {r.kodex_change_pct:+.4f} − {r.control_avg_pct:+.4f} = {raw_did:+.4f}  (≈ {int(raw_did*100):+d}%p)\n\n"
                    f"━━ STEP 4 · 2단계 Z-score (16주 이력 표준화) ━━\n"
                    + (f"  {_log_z1}\n" if _log_z1 else "")
                    + (f"  {_log_z2}\n" if _log_z2 else "")
                    + (f"  {_log_z3}\n" if _log_z3 else "")
                    + f"  ▶ Z-score = {z:+.4f}  ({z_label})\n\n"
                    f"━━ 판정 ━━\n"
                    f"  {r.judgement_emoji} {r.judgement}"
                )
                st.markdown(f"<div class='formula-box'>{formula}</div>", unsafe_allow_html=True)

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
                    "점수": f"{float(getattr(r, 'marketing_score', None) or 50.0):.0f}점 (Z={float(getattr(r, 'zscore', None) or 0.0):+.2f})",
                    "매핑": r.mapping_source,
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True)

# ══════════════════════════════════════════════════════════════════
# Step 5 · 요약
# ══════════════════════════════════════════════════════════════════
st.markdown('<div class="step-header">Step 5 · 주간 요약</div>', unsafe_allow_html=True)

spikes = [r for r in did_results if getattr(r, 'marketing_score', 50.0) >= 90 or getattr(r, 'marketing_score', 50.0) <= 10]
spike_names = [r.kodex_name for r in sorted(spikes, key=lambda x: abs(getattr(x, 'marketing_score', 50.0) - 50), reverse=True)[:3]]

st.markdown(f"**분석 주차:** {selected}")
st.markdown(f"**분석 ETF 수:** {len(did_results)}개")
st.markdown(f"**이상 감지 ETF (Z≥1.0):** {len(spikes)}개")
st.markdown(f"**상위 3개:** {', '.join(spike_names) if spike_names else '없음'}")

if spike_names:
    st.info("💡 은행 채널 유입 이상 감지 — 해당 주 KB/신한/하나/우리/농협 이벤트 역추적 권고")
