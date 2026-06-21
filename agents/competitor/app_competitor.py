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
from channel_archive import has_archive, save_channel_results, load_channel_results, get_archived_at, save_raw_data, load_raw_data

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
.ev-board { display:flex; gap:14px; flex-wrap:wrap; margin:16px 0; }
.ev-card {
    flex:1; min-width:240px; max-width:340px;
    border:1px solid rgba(244,63,94,0.2); border-radius:14px;
    padding:0; overflow:hidden;
    background:rgba(244,63,94,0.04);
    transition:transform .15s, background .15s;
}
.ev-card:hover { transform:translateY(-2px); background:rgba(244,63,94,0.08); }
.ev-card-img { width:100%; height:120px; object-fit:cover; display:block; }
.ev-card-img-placeholder {
    width:100%; height:72px;
    display:flex; align-items:center; justify-content:center; font-size:2rem;
}
.ev-card-body { padding:12px 14px; }
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
    "TIGER": {"color": "#ff8c42", "bg": "rgba(255,140,66,0.15)",  "channels": ["tiger_youtube", "tiger_event"]},
    "ACE":   {"color": "#05b169", "bg": "rgba(5,177,105,0.15)",   "channels": ["ace_youtube", "ace_event"]},
    "RISE":  {"color": "#a78bfa", "bg": "rgba(167,139,250,0.15)", "channels": ["rise_youtube", "rise_event"]},
    "HANARO":{"color": "#00c6ff", "bg": "rgba(0,198,255,0.12)",   "channels": ["hanaro_youtube", "hanaro_event"]},
    "SOL":   {"color": "#f43f5e", "bg": "rgba(244,63,94,0.15)",   "channels": ["sol_youtube", "sol_event", "sol_blog"]},
    "KODEX": {"color": "#3B82F6", "bg": "rgba(59,130,246,0.12)",  "channels": ["kodex_youtube", "samsung_fund_event"]},
}

# ── API 키 ────────────────────────────────────────────────────────────────────
anthropic_key = st.session_state.get("_anthropic_key", "")
if not anthropic_key:
    with st.sidebar:
        st.header("⚙️ 설정")
        anthropic_key = st.text_input(
            "Anthropic API Key", value=os.getenv("ANTHROPIC_API_KEY", ""),
            type="password", key="comp_ant_key", help="Anthropic Claude 사용 시"
        )
        gemini_key = st.text_input(
            "Gemini API Key", value=os.getenv("GEMINI_API_KEY", ""),
            type="password", key="comp_gem_key", help="Google Gemini 무료 사용 시 (둘 중 하나만)"
        )
        if gemini_key: os.environ["GEMINI_API_KEY"] = gemini_key
        api_key = anthropic_key or gemini_key

# ── 메인 탭 ──────────────────────────────────────────────────────────────────
_main_tab1, _main_tab2 = st.tabs(["📡 이번 주 경쟁사 모니터링", "📁 채널 수집 히스토리"])

with _main_tab1:

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

    _archive_key = f"competitor_{selected_week_lbl}"
    _days_old = (today_date - week_start_date).days

    # 아카이브 있으면 버튼 없이 자동 진행
    if not st.session_state.get("comp_analysis_run", False):
        if has_archive(_archive_key):
            st.session_state["comp_analysis_run"] = True

    if not st.session_state.get("comp_analysis_run", False):
        st.info("📦 이번 주 수집 데이터 없음 — 랜딩 페이지에서 **'🔄 전체 수집 시작'** 을 먼저 실행하세요.")
        st.stop()

    # ── STEP 1: 채널 수집 ─────────────────────────────────────────────────────────
    st.markdown('<div class="step-header">Step 1 · 경쟁사 채널 수집</div>', unsafe_allow_html=True)

    if has_archive(_archive_key):
        collection_results = load_channel_results(_archive_key)
        _archived_at = get_archived_at(_archive_key)
        st.caption(f"📦 보존된 결과 (최초 수집: {_archived_at})")
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
    if fail_cnt > 0:
        st.caption(f"수집 결과: 성공 {ok_cnt}개 / 미수집 {fail_cnt}개 (YouTube 쿼터 초과 시 RSS 폴백, 이번 주 게시물 없는 채널 포함)")
    else:
        st.caption(f"수집 결과: 전체 {ok_cnt}개 채널 수집 완료")

    with st.expander("📡 채널별 상세", expanded=False):
        for r in collection_results.values():
            if not r.success:
                st.markdown(f"❌ **{r.channel_name}** — {r.error_label or r.error}")
            else:
                d = r.data or {}
                items = []
                if "videos" in d:         items = [v.get("title","") for v in d["videos"][:5]]
                elif "event_details" in d: items = [e.get("title","") for e in d["event_details"][:5]]
                elif "posts" in d:         items = [p.get("title","") for p in d["posts"][:5]]
                elif "events" in d:        items = d["events"][:5]
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
        event_details에 image_url이 있으면 vision LLM으로 배너 이미지도 분석.
        """
        marketing_texts = []
        collected_image_urls = []  # 배너 이미지 URL 수집
        for r in collection_results.values():
            if not r.success or not r.data: continue
            d = r.data
            label = f"[{r.channel_name}]"
            ch_lines = []
            if "raw_text" in d:
                ch_lines.append(d['raw_text'][:300])
            elif "videos" in d:
                ch_lines += [f"- {v['title']}" for v in d["videos"][:3]]
            elif "posts" in d:
                ch_lines += [f"- {p['title']}" for p in d["posts"][:3]]
            elif "articles" in d:
                ch_lines += [f"- {a['title']}" for a in d["articles"][:3]]
            for ev in d.get("event_details", []):
                title = ev.get("title", "")
                url   = ev.get("url", "")
                img   = ev.get("image_url", "")
                if title:
                    ch_lines.append(f"- {title}" + (f" {url}" if url else ""))
                if img and img.startswith("http"):
                    collected_image_urls.append(img)
            if ch_lines:
                # 채널명에서 provider 감지해서 태그 추가
                _prov_tag = next((p for p in ["TIGER","ACE","RISE","HANARO","SOL","PLUS","KODEX"]
                                  if p in r.channel_name.upper()), "")
                _prov_str = f" [운용사: {_prov_tag}]" if _prov_tag else ""
                marketing_texts.append(f"{label}{_prov_str}\n" + "\n".join(ch_lines[:10]))

        if not marketing_texts:
            return {"marketing_detected": False, "events": [], "summary": "수집된 텍스트 없음"}

        # 채널당 300자로 제한 (전체 품질 균등 배분)
        combined = "\n\n".join(marketing_texts)
        if len(combined) > 6000:
            combined = combined[:6000] + "\n...(이하 생략)"

        prompt = f"""다음은 ETF 운용사 채널(KODEX/TIGER/ACE/RISE/HANARO/SOL)에서 수집된 텍스트입니다.

    {combined}

    [ETF 마케팅 활동 판단 기준]
    포함 (is_etf_marketing=true):
    - 특정 ETF 매수/투자 유도 이벤트, 프로모션, 경품 증정
    - 수수료 면제/할인 혜택 (특정 ETF 대상)
    - ETF 신규 출시·상장 홍보
    - ETF 매수 조건부 리워드/캐시백

    제외 (is_etf_marketing=false):
    - 일반 시황·경제 분석 콘텐츠 (ETF 언급만 있음)
    - 투자 교육 콘텐츠 (ETF 개념 설명, 재테크 팁 등)
    - 채용 공고, 사회공헌, 기업 IR, 스포츠 후원
    - 부동산·주식·채권 등 ETF 외 상품 안내
    - 단순 뉴스 보도 (운용사가 마케팅 주체가 아닌 경우)

    모든 항목에 is_etf_marketing을 판단해 포함하세요.

    JSON만 출력:
    {{
      "marketing_detected": true,
      "summary": "감지된 경쟁사 ETF 마케팅 활동 전체 요약 (2-3문장)",
      "events": [
        {{
          "channel": "채널명 (예: TIGER ETF 유튜브)",
          "provider": "KODEX|TIGER|ACE|RISE|HANARO|SOL|기타",
          "title": "이벤트·콘텐츠 제목",
          "url": "링크 (있으면)",
          "marketing_type": "이벤트|프로모션|추천콘텐츠|수수료혜택|기타",
          "event_period": "YYYY-MM-DD ~ YYYY-MM-DD 또는 기간 설명 (없으면 null)",
          "event_summary": "이벤트 핵심 내용: 어떤 혜택, 조건, 대상 ETF 등 1-2문장",
          "target_etf": "대상 ETF 이름 또는 카테고리 (예: TIGER 미국S&P500, null 가능)",
          "is_etf_marketing": true
        }}
      ]
    }}"""

        try:
            from llm_client import call_llm, call_llm_with_images
            gem_key = os.getenv("GEMINI_API_KEY", "")
            text = None
            if collected_image_urls:
                try:
                    img_note = f"\n\n[첨부 이미지 {len(collected_image_urls)}개: 이벤트 배너 이미지입니다.]"
                    text = call_llm_with_images(
                        prompt + img_note,
                        collected_image_urls,
                        anthropic_key=api_key,
                        gemini_key=gem_key,
                        max_tokens=1500,
                    )
                except Exception as img_e:
                    logger.warning(f"이미지 LLM 실패, 텍스트 전용으로 전환: {img_e}")
                    text = None
            if not text:
                text = call_llm(prompt, anthropic_key=api_key, gemini_key=gem_key, max_tokens=3000)
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                raw = m.group()
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    try:
                        from json_repair import repair_json
                        return json.loads(repair_json(raw))
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"경쟁사 LLM 분석 실패: {e}")

        return {"marketing_detected": False, "events": [], "summary": "LLM 분석 실패"}


    def _inject_images(comp_result: dict, collection_results: dict) -> dict:
        """LLM 결과 events에 image_url을 collection_results event_details에서 URL 매칭으로 주입."""
        if not comp_result.get("events"):
            return comp_result
        # URL → image_url 역색인 만들기
        url_to_img: dict = {}
        for r in collection_results.values():
            if not r.success or not r.data: continue
            for ev in r.data.get("event_details", []):
                u = ev.get("url","")
                img = ev.get("image_url","")
                if u and img:
                    url_to_img[u] = img
        # title 매칭용 dict도 (URL이 약간 달라질 수 있어서)
        title_to_img: dict = {}
        for r in collection_results.values():
            if not r.success or not r.data: continue
            for ev in r.data.get("event_details", []):
                t = ev.get("title","")[:40]
                img = ev.get("image_url","")
                if t and img:
                    title_to_img[t] = img
        for ev in comp_result["events"]:
            if ev.get("image_url"): continue
            url = ev.get("url","")
            title = (ev.get("title") or "")[:40]
            ev["image_url"] = url_to_img.get(url) or title_to_img.get(title) or ""
        return comp_result


    def keyword_fallback_competitor(collection_results: dict) -> dict:
        """API 키 없을 때 제목 키워드 기반으로 경쟁사 이벤트 감지."""
        events = []
        _prov_keys = ["TIGER","ACE","RISE","HANARO","SOL","KODEX"]
        for r in collection_results.values():
            if not r.success or not r.data: continue
            d = r.data
            items = []
            for v in d.get("videos",[]):       items.append({"title":v.get("title",""),  "url":v.get("url",""),  "image_url":""})
            for p in d.get("posts",[]):        items.append({"title":p.get("title",""),  "url":p.get("link",""), "image_url":""})
            for a in d.get("articles",[]):     items.append({"title":a.get("title",""),  "url":a.get("link",""), "image_url":""})
            for ev in d.get("event_details",[]): items.append({"title":ev.get("title",""),"url":ev.get("url",""),"image_url":ev.get("image_url","")})
            for item in items:
                t = item["title"]
                if not any(kw in t for kw in ["이벤트","프로모션","혜택","ETF","투자","매수","출시","신규"]): continue
                # 제목에서 provider 우선 추출, 없으면 채널명에서
                prov = next((p for p in _prov_keys if p in t.upper()), None) or \
                       next((p for p in _prov_keys if p in r.channel_name.upper()), "기타")
                ev_type = "이벤트" if "이벤트" in t else "프로모션" if ("혜택" in t or "프로모션" in t) else "추천콘텐츠"
                events.append({
                    "provider": prov,
                    "channel": r.channel_name,
                    "title": t[:80],
                    "url": item.get("url",""),
                    "image_url": item.get("image_url",""),
                    "marketing_type": ev_type,
                    "event_summary": f"{r.channel_name} 마케팅 콘텐츠 감지",
                    "event_period": None,
                    "target_etf": None,
                })
        if events:
            return {"marketing_detected": True, "events": events,
                    "summary": f"키워드 기반 감지 (API 키 없음) — {len(events)}건"}
        return {"marketing_detected": False, "events": [], "summary": "감지 없음 (키워드 방식)"}


    _use_api = anthropic_key or os.getenv("GEMINI_API_KEY","")
    _llm_cache_key = f"comp_llm_{selected_week_lbl}"

    # LLM 분석 결과 캐시 자동 로드 (실패 결과는 무시하고 재실행)
    _cached = load_raw_data(_llm_cache_key) if has_archive(_llm_cache_key) else None
    _cache_valid = bool(
        _cached and not (
            _cached.get("marketing_detected") is False
            and "실패" in _cached.get("summary", "")
        )
    )

    if _cached and _cache_valid:
        comp_result = _cached
        st.caption(f"📦 LLM 분석 결과 캐시 사용 ({_llm_cache_key})")
    elif _use_api:
        with st.spinner("LLM으로 경쟁사 마케팅 이벤트 분석 중..."):
            comp_result = extract_competitor_events(collection_results, anthropic_key or "")
        comp_result = _inject_images(comp_result, collection_results)
        _llm_failed = "실패" in comp_result.get("summary", "")
        if not _llm_failed and comp_result.get("marketing_detected") is not None:
            save_raw_data(_llm_cache_key, comp_result)
        elif _llm_failed:
            st.warning("LLM 호출 실패 — 키워드 기반으로 전환합니다.")
            comp_result = keyword_fallback_competitor(collection_results)
    else:
        st.info("💡 API 키 미입력 — 키워드 기반으로 경쟁사 이벤트를 감지합니다. (LLM 보다 정밀도 낮음)")
        comp_result = keyword_fallback_competitor(collection_results)
        # 키워드 폴백은 이미 event_details에서 image_url 직접 넣었으므로 별도 주입 불필요

    st.markdown('<div class="comp-divider"></div>', unsafe_allow_html=True)

    if not comp_result.get("marketing_detected"):
        summary_txt = comp_result.get("summary", "")
        if "실패" in summary_txt or not summary_txt:
            st.error("LLM 분석 실패 — API 키를 확인하거나 다시 시도하세요.")
        else:
            st.info("이번 주 경쟁사 마케팅 활동 감지 없음")
        if summary_txt:
            st.caption(summary_txt)
        st.stop()

    # ── 이벤트 보드 메인 ─────────────────────────────────────────────────────────
    all_events = comp_result.get("events", [])

    # ETF 마케팅 필터링 (LLM이 is_etf_marketing 판단한 경우에만 적용)
    _has_filter_field = any("is_etf_marketing" in ev for ev in all_events)
    if _has_filter_field:
        events = [ev for ev in all_events if ev.get("is_etf_marketing", True)]
        filtered_out = len(all_events) - len(events)
    else:
        events = all_events
        filtered_out = 0

    _filter_note = f" (ETF 마케팅 외 {filtered_out}건 제외)" if filtered_out > 0 else ""
    st.success(f"📣 경쟁사 ETF 마케팅 이벤트 {len(events)}건 감지{_filter_note}")
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
    _prov_icon = {"KODEX":"🔵","TIGER":"🟠","ACE":"🟢","RISE":"🟣","HANARO":"🔵","SOL":"🔴"}

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
            mtype      = ev.get("marketing_type", "기타")
            cls        = _type_cls.get(mtype, "ev-type-etc")
            ev_icon    = _type_icon.get(mtype, "📋")
            title      = (ev.get("title") or "")[:60]
            period     = ev.get("event_period") or ""
            summary    = ev.get("event_summary") or ""
            channel    = ev.get("channel", "")
            url        = ev.get("url", "")
            target_etf = ev.get("target_etf") or ""
            img_url    = ev.get("image_url","")

            title_html  = (f'<a href="{url}" target="_blank" style="color:#e8eaed;text-decoration:none;">{title}</a>'
                           if url and url.startswith("http") else title)
            period_html = f'<div class="ev-period">📅 {period}</div>' if period and period not in ("","null") else ""
            etf_html    = (f'<div style="font-size:.7rem;color:{pinfo["color"]};margin-top:4px;">🎯 {target_etf}</div>'
                           if target_etf and target_etf != "null" else "")
            img_html    = (f'<img class="ev-card-img" src="{img_url}" onerror="this.style.display=\'none\'">'
                           if img_url else f'<div class="ev-card-img-placeholder" style="background:{pinfo["bg"]};">{ev_icon}</div>')

            cards_html += (
                f'<div class="ev-card" style="border-color:{pinfo["color"]}33;">'
                f'{img_html}'
                f'<div class="ev-card-body">'
                f'<span class="ev-card-type {cls}">{ev_icon} {mtype}</span>'
                f'<div class="ev-title">{title_html}</div>'
                f'{period_html}'
                f'<div class="ev-summary">{summary[:140]}</div>'
                f'{etf_html}'
                f'<div class="ev-channel">📡 {channel}</div>'
                f'</div></div>'
            )
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


with _main_tab2:
    _hist_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agents", "history", "app_history.py")
    exec(open(_hist_file, encoding="utf-8").read())
