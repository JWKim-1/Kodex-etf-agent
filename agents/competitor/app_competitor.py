"""
경쟁사 ETF 채널 모니터링
TIGER·ACE·RISE·HANARO·SOL 유튜브·블로그 마케팅 감지
→ 감지 이벤트 보드 (이벤트명 / 기간 / 내용) 표시
"""

import os, sys, json, re, logging
from datetime import datetime, timedelta, date
from typing import Dict

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd
import streamlit as st
import anthropic as ant

from collector import DataCollector, CHANNEL_LABELS
from krx_data_fetcher import load_cache, _parse_week_label
from channel_archive import has_archive, save_channel_results, load_channel_results, get_archived_at

logger = logging.getLogger(__name__)

# ── CSS 경쟁사 이벤트 보드 ────────────────────────────────────────────────────
st.markdown("""
<style>
.comp-header {
    font-size:1.6rem; font-weight:800; margin-bottom:.3rem;
    background:linear-gradient(90deg,#f43f5e,#a78bfa);
    -webkit-background-clip:text; -webkit-text-fill-color:transparent;
}
.comp-week-badge {
    display:inline-block;
    background:rgba(244,63,94,0.1); border:1px solid rgba(244,63,94,0.3);
    color:#f43f5e; border-radius:100px; padding:4px 14px;
    font-size:.82rem; font-weight:600; margin-bottom:1.2rem;
}
.ev-board { display:flex; gap:12px; flex-wrap:wrap; margin:16px 0; }
.ev-card {
    flex:1; min-width:240px; max-width:340px;
    border:1px solid rgba(244,63,94,0.2); border-radius:14px;
    padding:14px 16px; background:rgba(244,63,94,0.04);
    transition:background .15s;
}
.ev-card:hover { background:rgba(244,63,94,0.08); }
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
.ev-period { font-size:.75rem; color:#f43f5e; margin:4px 0; }
.ev-summary { font-size:.78rem; color:#aaa; line-height:1.5; margin:6px 0 0; }
.ev-channel { font-size:.68rem; color:#666; margin-top:6px; }
.prov-badge-comp {
    display:inline-block; font-size:.72rem; font-weight:700;
    padding:3px 10px; border-radius:100px; margin:2px;
}
.comp-divider { height:1px; background:rgba(255,255,255,0.07); margin:1.2rem 0; }
.ch-status-ok   { color:#05b169; }
.ch-status-fail { color:#cf202f; }
</style>
""", unsafe_allow_html=True)

# ── 경쟁사 프로바이더 정의 ────────────────────────────────────────────────────
COMP_PROVIDERS = {
    "TIGER": {"color": "#ff8c42", "bg": "rgba(255,140,66,0.15)", "channels": ["tiger_youtube", "tiger_blog"]},
    "ACE":   {"color": "#05b169", "bg": "rgba(5,177,105,0.15)",  "channels": ["ace_youtube"]},
    "RISE":  {"color": "#a78bfa", "bg": "rgba(167,139,250,0.15)","channels": ["rise_youtube"]},
    "HANARO":{"color": "#00c6ff", "bg": "rgba(0,198,255,0.12)",  "channels": ["hanaro_youtube"]},
    "SOL":   {"color": "#f43f5e", "bg": "rgba(244,63,94,0.15)",  "channels": ["sol_youtube"]},
}

# ── API 키 ────────────────────────────────────────────────────────────────────
anthropic_key = st.session_state.get("_anthropic_key", "")
if not anthropic_key:
    with st.sidebar:
        st.header("⚙️ 설정")
        anthropic_key = st.text_input(
            "Anthropic API Key", value=os.getenv("ANTHROPIC_API_KEY", ""),
            type="password", help="경쟁사 마케팅 감지용"
        )

# ── 주차 선택 ─────────────────────────────────────────────────────────────────
today_date = datetime.now().date()
monday = today_date - timedelta(days=today_date.weekday())
week_opts = {}
for i in range(8):
    ws = monday - timedelta(weeks=i)
    we = ws + timedelta(days=4)
    lbl = f"{ws.month}.{ws.day}-{we.month}.{we.day}"
    week_opts[lbl] = (ws, we)

week_labels_list = list(week_opts.keys())
_is_friday = today_date.weekday() == 4
_default = 0 if _is_friday else 1

selected_week_lbl = st.selectbox("분석 주차", week_labels_list, index=min(_default, len(week_labels_list)-1), key="comp_week")
week_start_date, week_end_date = week_opts[selected_week_lbl]
week_start_dt = datetime(week_start_date.year, week_start_date.month, week_start_date.day)
week_end_dt   = datetime(week_end_date.year, week_end_date.month, week_end_date.day, 23, 59)
week_str = f"{week_start_date.month}/{week_start_date.day} ~ {week_end_date.month}/{week_end_date.day}"

st.markdown(f'<div class="comp-header">🏢 경쟁사 채널 모니터링</div>', unsafe_allow_html=True)
st.markdown(f'<span class="comp-week-badge">📅 {week_str} 기준</span>', unsafe_allow_html=True)

if st.button("🔍 경쟁사 채널 수집 및 분석", type="primary", use_container_width=True, key="comp_run"):
    st.session_state["comp_analysis_run"] = True

if not st.session_state.get("comp_analysis_run", False):
    st.info("위 버튼을 눌러 경쟁사 ETF 운용사 채널 수집을 시작하세요.")
    st.markdown("""
    **수집 채널:**
    - 🟠 **TIGER ETF** — 미래에셋자산운용 유튜브·블로그
    - 🟢 **ACE ETF** — 한국투자신탁운용 유튜브
    - 🟣 **RISE ETF** — KB자산운용 유튜브
    - 🔵 **HANARO ETF** — NH-Amundi자산운용 유튜브
    - 🔴 **SOL ETF** — 신한자산운용 유튜브
    - 📰 뉴스 (네이버/구글)
    """)
    st.stop()

# ── STEP 1: 채널 수집 ─────────────────────────────────────────────────────────
st.markdown('<div class="step-header">Step 1 · 경쟁사 채널 수집</div>', unsafe_allow_html=True)

_archive_key = f"competitor_{selected_week_lbl}"
_days_old = (today_date - week_start_date).days

if has_archive(_archive_key):
    collection_results = load_channel_results(_archive_key)
    _archived_at = get_archived_at(_archive_key)
    st.caption(f"📦 보존된 결과 (최초 수집: {_archived_at})")
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

    with st.spinner("경쟁사 채널 수집 중..."):
        collection_results = collector.collect_all_competitor(progress_callback=on_prog)
    prog_ph.empty()

    if _days_old <= 14:
        save_channel_results(_archive_key, collection_results)

ok_cnt   = sum(1 for r in collection_results.values() if r.success)
fail_cnt = len(collection_results) - ok_cnt

# 채널 상태 pills
ch_pills = ""
for r in collection_results.values():
    icon = "✅" if r.success else "❌"
    ch_pills += f'<span class="ch-pill {"ch-ok" if r.success else "ch-fail"}">{icon} {r.channel_name}</span>'
st.markdown(f'<div style="margin:8px 0;">{ch_pills}</div>', unsafe_allow_html=True)
st.caption(f"수집 결과: 성공 {ok_cnt}개 / 실패 {fail_cnt}개")

with st.expander("📡 채널별 상세", expanded=False):
    for r in collection_results.values():
        if not r.success:
            st.markdown(f"❌ **{r.channel_name}** — {r.error_label or r.error}")
        else:
            d = r.data or {}
            items = []
            if "videos" in d:  items = [v.get("title","") for v in d["videos"][:5]]
            elif "posts" in d: items = [p.get("title","") for p in d["posts"][:5]]
            if items:
                st.markdown(f"**{r.channel_name}**")
                for it in items:
                    st.markdown(f"- {it}")

# ── STEP 2: LLM 경쟁사 마케팅 감지 + 이벤트 보드 ─────────────────────────────
st.markdown('<div class="comp-divider"></div>', unsafe_allow_html=True)
st.markdown('<div class="step-header">Step 2 · 마케팅 이벤트 감지 및 분석</div>', unsafe_allow_html=True)

def extract_competitor_events(collection_results: dict, api_key: str) -> dict:
    """
    경쟁사 채널에서 LLM으로 마케팅 이벤트 추출.
    이벤트명·기간·내용·대상 ETF 구조화.
    """
    marketing_texts = []
    for r in collection_results.values():
        if not r.success or not r.data: continue
        d = r.data
        label = f"[{r.channel_name}]"
        if "raw_text" in d:
            marketing_texts.append(f"{label}\n{d['raw_text'][:600]}")
        elif "videos" in d:
            lines = [f"- {v['title']} {v.get('url','')}" for v in d["videos"][:5]]
            if lines: marketing_texts.append(f"{label}\n" + "\n".join(lines))
        elif "posts" in d:
            lines = [f"- {p['title']} {p.get('link','')}" for p in d["posts"][:5]]
            if lines: marketing_texts.append(f"{label}\n" + "\n".join(lines))
        elif "articles" in d:
            lines = [f"- {a['title']} {a.get('link','')}" for a in d["articles"][:5]]
            if lines: marketing_texts.append(f"{label}\n" + "\n".join(lines))

    if not marketing_texts:
        return {"marketing_detected": False, "events": [], "summary": "수집된 텍스트 없음"}

    prompt = f"""다음은 경쟁사 ETF 운용사 채널(TIGER/ACE/RISE/HANARO/SOL)에서 수집된 텍스트입니다.

{chr(10).join(marketing_texts)}

[분석 기준]
- 각 운용사가 자사 ETF를 대상으로 진행하는 마케팅 활동을 감지하세요
- 이벤트, 프로모션, 수수료 혜택, 매수 유도 CTA, 특정 ETF 직접 추천 등이 대상
- 시황 분석, ETF 교육, 단순 ETF 언급 등은 제외
- 텍스트에서 이벤트 기간을 추출하고, 없으면 null로 표시

JSON만 출력:
{{
  "marketing_detected": true,
  "summary": "감지된 경쟁사 마케팅 활동 전체 요약 (2-3문장)",
  "events": [
    {{
      "channel": "채널명 (예: TIGER ETF 유튜브)",
      "provider": "TIGER|ACE|RISE|HANARO|SOL|기타",
      "title": "이벤트·콘텐츠 제목",
      "url": "링크 (있으면)",
      "marketing_type": "이벤트|프로모션|추천콘텐츠|수수료혜택|기타",
      "event_period": "YYYY-MM-DD ~ YYYY-MM-DD 또는 기간 설명 (없으면 null)",
      "event_summary": "이벤트 핵심 내용: 어떤 혜택, 조건, 대상 ETF 등 1-2문장",
      "target_etf": "대상 ETF 이름 또는 카테고리 (예: TIGER 미국S&P500, null 가능)"
    }}
  ]
}}"""

    try:
        client = ant.Anthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY",""))
        msg = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        logger.warning(f"경쟁사 LLM 분석 실패: {e}")

    return {"marketing_detected": False, "events": [], "summary": f"LLM 분석 실패"}


with st.spinner("LLM으로 경쟁사 마케팅 이벤트 분석 중..."):
    comp_result = extract_competitor_events(collection_results, anthropic_key)

st.markdown('<div class="comp-divider"></div>', unsafe_allow_html=True)

if not comp_result.get("marketing_detected"):
    st.info("이번 주 경쟁사 마케팅 활동 감지 없음")
    if comp_result.get("summary"):
        st.caption(comp_result["summary"])
    st.stop()

# ── 이벤트 보드 메인 ─────────────────────────────────────────────────────────
events = comp_result.get("events", [])
st.success(f"📣 경쟁사 마케팅 이벤트 {len(events)}건 감지")
if comp_result.get("summary"):
    st.caption(comp_result["summary"])

st.markdown("### 📋 감지된 이벤트")

if not events:
    st.info("이벤트 상세 데이터 없음")
    st.stop()

# 운용사별 그룹핑
from collections import defaultdict
by_provider = defaultdict(list)
for ev in events:
    prov = ev.get("provider", "기타")
    by_provider[prov].append(ev)

_type_cls  = {"이벤트":"ev-type-event","프로모션":"ev-type-promo","추천콘텐츠":"ev-type-content","수수료혜택":"ev-type-fee"}
_type_icon = {"이벤트":"🎁","프로모션":"💰","추천콘텐츠":"📺","수수료혜택":"🎯"}
_prov_icon = {"TIGER":"🟠","ACE":"🟢","RISE":"🟣","HANARO":"🔵","SOL":"🔴"}

for prov, prov_events in by_provider.items():
    pinfo = COMP_PROVIDERS.get(prov, {"color":"#aaa","bg":"rgba(255,255,255,0.05)"})
    icon  = _prov_icon.get(prov, "⬜")
    st.markdown(
        f'<div style="font-size:1rem;font-weight:700;margin:16px 0 8px;color:{pinfo["color"]};">'
        f'{icon} {prov} ETF ({len(prov_events)}건)</div>',
        unsafe_allow_html=True
    )

    cards_html = '<div class="ev-board">'
    for ev in prov_events:
        mtype    = ev.get("marketing_type", "기타")
        cls      = _type_cls.get(mtype, "ev-type-etc")
        ev_icon  = _type_icon.get(mtype, "📋")
        title    = (ev.get("title") or "")[:60]
        period   = ev.get("event_period") or ""
        summary  = ev.get("event_summary") or ""
        channel  = ev.get("channel", "")
        url      = ev.get("url", "")
        target_etf = ev.get("target_etf") or ""

        title_html = (
            f'<a href="{url}" target="_blank" style="color:#e8eaed;text-decoration:none;">{title}</a>'
            if url and url.startswith("http") else title
        )
        period_html = f'<div class="ev-period">📅 {period}</div>' if period and period != "null" else ""
        etf_html    = (
            f'<div style="font-size:.7rem;color:{pinfo["color"]};margin-top:4px;">🎯 {target_etf}</div>'
            if target_etf and target_etf != "null" else ""
        )

        cards_html += f"""
        <div class="ev-card" style="border-color:{pinfo["color"]}33;background:{pinfo["bg"]};">
          <span class="ev-card-type {cls}">{ev_icon} {mtype}</span>
          <div class="ev-title">{title_html}</div>
          {period_html}
          <div class="ev-summary">{summary[:140]}</div>
          {etf_html}
          <div class="ev-channel">📡 {channel}</div>
        </div>"""
    cards_html += "</div>"
    st.markdown(cards_html, unsafe_allow_html=True)

# ── 운용사별 이벤트 건수 요약 바 ─────────────────────────────────────────────
st.markdown('<div class="comp-divider"></div>', unsafe_allow_html=True)
st.markdown("### 📊 운용사별 이벤트 현황")

import plotly.graph_objects as go

prov_names = [p for p in by_provider]
prov_cnts  = [len(by_provider[p]) for p in prov_names]
prov_colors = [COMP_PROVIDERS.get(p, {}).get("color", "#888") for p in prov_names]

fig = go.Figure(go.Bar(
    x=prov_cnts, y=prov_names, orientation="h",
    marker=dict(color=prov_colors),
    text=prov_cnts, textposition="outside",
))
fig.update_layout(
    height=max(150, len(prov_names) * 50 + 60),
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#e8eaed", size=13),
    xaxis=dict(showgrid=False, zeroline=False, visible=False),
    yaxis=dict(autorange="reversed"),
    margin=dict(l=10, r=60, t=10, b=10),
)
st.plotly_chart(fig, use_container_width=True)

# 새로고침
if st.button("🔄 데이터 새로고침 (재수집)", key="comp_refresh"):
    from channel_archive import _load_all, _save_all
    arch = _load_all()
    if _archive_key in arch:
        del arch[_archive_key]
        _save_all(arch)
    st.session_state["comp_analysis_run"] = False
    st.rerun()

st.caption(f"경쟁사 채널 모니터링 · {week_str} · 삼성자산운용 ETF AI Agent")
