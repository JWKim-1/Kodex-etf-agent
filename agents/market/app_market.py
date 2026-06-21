"""
ETF 시장 트렌드 페이지
주간 수익률 Top10 + 거래대금 Top10 — KRX pykrx 데이터 기반
"""

import os
import sys
import numpy as np
import pandas as pd
import streamlit as st
from datetime import date, datetime, timedelta

# 경로 보정 (exec로 로드될 때 루트 경로 유지)
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from krx_data_fetcher import (
    load_trend_cache, save_trend_cache,
    fetch_etf_market_summary, load_cache, _parse_week_label,
)

# ── CSS ──────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.trend-title {
    font-size:1.6rem; font-weight:800; margin-bottom:0.2rem;
    background: linear-gradient(90deg,#4d9fff,#00c6ff);
    -webkit-background-clip:text; -webkit-text-fill-color:transparent;
}
.trend-week-badge {
    display:inline-block;
    background:rgba(77,159,255,0.12); border:1px solid rgba(77,159,255,0.3);
    color:#4d9fff; border-radius:100px; padding:4px 14px;
    font-size:0.82rem; font-weight:600; margin-bottom:1.2rem;
}
.panel-title {
    font-size:1rem; font-weight:700; margin:0 0 0.8rem;
    display:flex; align-items:center; gap:8px;
}
.rank-row {
    display:flex; align-items:center; gap:10px;
    padding:9px 12px; border-radius:10px; margin:4px 0;
    background:rgba(255,255,255,0.03);
    border:1px solid rgba(255,255,255,0.06);
    transition:background .15s;
}
.rank-row:hover { background:rgba(255,255,255,0.06); }
.rank-badge {
    min-width:28px; height:28px; border-radius:8px;
    display:flex; align-items:center; justify-content:center;
    font-size:0.78rem; font-weight:800; flex-shrink:0;
}
.rank-1 { background:rgba(255,200,50,0.2); color:#f0c040; border:1px solid rgba(255,200,50,0.4); }
.rank-2 { background:rgba(180,180,180,0.15); color:#b8b8b8; border:1px solid rgba(180,180,180,0.3); }
.rank-3 { background:rgba(180,100,50,0.2); color:#cd7f32; border:1px solid rgba(180,100,50,0.35); }
.rank-n { background:rgba(255,255,255,0.05); color:#666; border:1px solid rgba(255,255,255,0.08); }
.etf-info { flex:1; min-width:0; }
.etf-name {
    font-size:0.82rem; font-weight:600; white-space:nowrap;
    overflow:hidden; text-overflow:ellipsis; color:#e8eaed;
}
.prov-chip {
    display:inline-block; font-size:0.65rem; font-weight:700;
    padding:1px 7px; border-radius:100px; margin-top:2px;
}
.metric-col { text-align:right; min-width:72px; }
.metric-val { font-size:0.95rem; font-weight:700; font-family:'JetBrains Mono','D2Coding',monospace; }
.val-pos { color:#05b169; }
.val-neg { color:#cf202f; }
.val-neu { color:#888; }
.val-vol { color:#4d9fff; }
.mini-bar-wrap {
    height:4px; border-radius:2px; background:rgba(255,255,255,0.06);
    margin-top:4px; overflow:hidden;
}
.mini-bar-fill { height:100%; border-radius:2px; }
.section-divider { height:1px; background:rgba(255,255,255,0.07); margin:1.2rem 0; }
.info-chip {
    display:inline-block; background:rgba(255,255,255,0.05);
    border:1px solid rgba(255,255,255,0.1); border-radius:6px;
    padding:3px 10px; font-size:0.75rem; color:#888; margin:2px;
}
</style>
""", unsafe_allow_html=True)

# ── 운용사 감지 ───────────────────────────────────────────────────────────────
_PROV_MAP = [
    ("KODEX",   "#4d9fff", "rgba(77,159,255,0.15)"),
    ("TIGER",   "#ff8c42", "rgba(255,140,66,0.15)"),
    ("ACE",     "#05b169", "rgba(5,177,105,0.15)"),
    ("RISE",    "#a78bfa", "rgba(167,139,250,0.15)"),
    ("HANARO",  "#00c6ff", "rgba(0,198,255,0.12)"),
    ("SOL",     "#f43f5e", "rgba(244,63,94,0.15)"),
    ("KINDEX",  "#fb923c", "rgba(251,146,60,0.12)"),
    ("ARIRANG", "#22d3ee", "rgba(34,211,238,0.12)"),
    ("KOSEF",   "#a3e635", "rgba(163,230,53,0.12)"),
    ("TIMEFOLIO","#e879f9","rgba(232,121,249,0.12)"),
]

def _detect_provider(name: str):
    for prov, color, bg in _PROV_MAP:
        if prov in str(name).upper():
            return prov, color, bg
    return None, "#888", "rgba(255,255,255,0.05)"


def _rank_badge_cls(rank: int) -> str:
    if rank == 1: return "rank-1"
    if rank == 2: return "rank-2"
    if rank == 3: return "rank-3"
    return "rank-n"


def _rank_icon(rank: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, str(rank))


def _shorten_name(name: str) -> str:
    """ETF 이름 45자 이내 truncation."""
    return name if len(name) <= 38 else name[:36] + "…"


def render_rank_row(rank: int, name: str, metric_html: str, bar_pct: float, bar_color: str) -> str:
    prov, color, bg = _detect_provider(name)
    badge_cls = _rank_badge_cls(rank)
    prov_chip = (
        f'<span class="prov-chip" style="background:{bg};color:{color};border:1px solid {color}33;">{prov}</span>'
        if prov else ""
    )
    bar_w = max(0, min(100, bar_pct))
    return f"""
    <div class="rank-row">
      <div class="rank-badge {badge_cls}">{_rank_icon(rank)}</div>
      <div class="etf-info">
        <div class="etf-name">{_shorten_name(name)}</div>
        {prov_chip}
      </div>
      <div class="metric-col">
        {metric_html}
        <div class="mini-bar-wrap">
          <div class="mini-bar-fill" style="width:{bar_w:.1f}%;background:{bar_color};"></div>
        </div>
      </div>
    </div>"""


# ── 주차 목록 구성 ────────────────────────────────────────────────────────────
krx_sheets = load_cache()
week_labels_sorted = sorted(
    krx_sheets.keys(),
    key=lambda w: _parse_week_label(w) or date.min,
    reverse=True,
)

if not week_labels_sorted:
    st.warning("캐시 데이터가 없습니다. 주차를 선택하면 자동 수집됩니다.")
    st.stop()

today = date.today()
_is_friday = (today.weekday() == 4)

def _week_label_display(w: str) -> str:
    d = _parse_week_label(w)
    if d is None:
        return w
    days_ago = (today - d).days
    if days_ago <= 7:
        return f"이번 주 ({w})"
    elif days_ago <= 14:
        return f"지난 주 ({w})"
    else:
        return w

display_labels = [_week_label_display(w) for w in week_labels_sorted]
default_idx = 1 if (not _is_friday and len(week_labels_sorted) >= 2) else 0

selected_display = st.selectbox("분석 주차", display_labels, index=default_idx)
selected_week = week_labels_sorted[display_labels.index(selected_display)]

week_start_date = _parse_week_label(selected_week)
week_end_date   = week_start_date + timedelta(days=4) if week_start_date else None

# ── 헤더 ─────────────────────────────────────────────────────────────────────
week_str = (
    f"{week_start_date.month}/{week_start_date.day} ~ {week_end_date.month}/{week_end_date.day}"
    if week_start_date else selected_week
)
st.markdown(f'<div class="trend-title">📊 ETF 시장 트렌드</div>', unsafe_allow_html=True)
st.markdown(f'<span class="trend-week-badge">📅 {week_str} 기준</span>', unsafe_allow_html=True)

# ── 데이터 로드 / 수집 ────────────────────────────────────────────────────────
trend_cache = load_trend_cache()

if selected_week in trend_cache:
    df_trend = trend_cache[selected_week]
    st.caption("📦 캐시된 데이터 사용")
else:
    from krx_data_fetcher import fetch_etf_market_summary_naver
    with st.spinner("네이버 금융에서 ETF 데이터 수집 중…"):
        try:
            df_trend = fetch_etf_market_summary_naver()
            if not df_trend.empty:
                save_trend_cache(selected_week, df_trend)
                st.success(f"✅ 수집 완료: {len(df_trend):,}개 ETF")
            else:
                st.error("네이버 금융 데이터를 불러오지 못했습니다.")
                df_trend = pd.DataFrame()
        except Exception as e:
            st.error(f"수집 실패: {e}")
            df_trend = pd.DataFrame()

if df_trend.empty:
    st.warning("데이터를 불러오지 못했습니다. KRX 계정 정보(.env)를 확인해 주세요.")
    st.stop()

# 필수 컬럼 보정
if "수익률_pct" not in df_trend.columns:
    df_trend["수익률_pct"] = np.nan
if "거래대금_억" not in df_trend.columns:
    df_trend["거래대금_억"] = 0.0
if "종목명" not in df_trend.columns:
    df_trend["종목명"] = df_trend.get("종목코드", "")

# ── Top 10 추출 ───────────────────────────────────────────────────────────────
df_valid_ret = df_trend.dropna(subset=["수익률_pct"]).copy()
df_valid_vol = df_trend[df_trend["거래대금_억"] > 0].copy()

top10_ret_pos = df_valid_ret.nlargest(10, "수익률_pct").reset_index(drop=True)
top10_ret_neg = df_valid_ret.nsmallest(5, "수익률_pct").reset_index(drop=True)
top10_vol     = df_valid_vol.nlargest(10, "거래대금_억").reset_index(drop=True)

# ── 요약 지표 ─────────────────────────────────────────────────────────────────
total_vol_조  = df_trend["거래대금_억"].sum() / 10_000
avg_ret       = df_valid_ret["수익률_pct"].mean() if len(df_valid_ret) else 0
up_cnt        = (df_valid_ret["수익률_pct"] > 0).sum()
dn_cnt        = (df_valid_ret["수익률_pct"] < 0).sum()

m1, m2, m3 = st.columns(3)
with m1:
    st.metric("전체 ETF 수", f"{len(df_trend):,}개")
with m2:
    st.metric("주간 거래대금 합계", f"{total_vol_조:.1f}조원")
with m3:
    st.metric("상승 / 하락", f"{up_cnt} / {dn_cnt}")

st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

# ── 메인 2컬럼 ───────────────────────────────────────────────────────────────
col_ret, col_vol = st.columns(2, gap="large")

# ── 왼쪽: 수익률 Top 10 ───────────────────────────────────────────────────────
with col_ret:
    st.markdown('<div class="panel-title">📈 수익률 Top 10</div>', unsafe_allow_html=True)

    if top10_ret_pos.empty:
        st.info("수익률 데이터 없음")
    else:
        max_ret = top10_ret_pos["수익률_pct"].abs().max() or 1
        rows_html = ""
        for rank, row in enumerate(top10_ret_pos.itertuples(), 1):
            ret = row.수익률_pct
            color = "#05b169" if ret >= 0 else "#cf202f"
            sign  = "+" if ret >= 0 else ""
            val_cls = "val-pos" if ret >= 0 else "val-neg"
            metric_html = f'<div class="metric-val {val_cls}">{sign}{ret:.2f}%</div>'
            bar_pct = abs(ret) / max_ret * 100
            rows_html += render_rank_row(rank, row.종목명, metric_html, bar_pct, color)
        st.markdown(rows_html, unsafe_allow_html=True)

    # 하락 Top 5 (접이식)
    with st.expander("▼ 하락 Top 5", expanded=False):
        if top10_ret_neg.empty:
            st.info("데이터 없음")
        else:
            max_neg = top10_ret_neg["수익률_pct"].abs().max() or 1
            rows_html = ""
            for rank, row in enumerate(top10_ret_neg.itertuples(), 1):
                ret = row.수익률_pct
                metric_html = f'<div class="metric-val val-neg">{ret:.2f}%</div>'
                bar_pct = abs(ret) / max_neg * 100
                rows_html += render_rank_row(rank, row.종목명, metric_html, bar_pct, "#cf202f")
            st.markdown(rows_html, unsafe_allow_html=True)

# ── 오른쪽: 거래대금 Top 10 ──────────────────────────────────────────────────
with col_vol:
    st.markdown('<div class="panel-title">💰 거래대금 Top 10</div>', unsafe_allow_html=True)

    if top10_vol.empty:
        st.info("거래대금 데이터 없음")
    else:
        max_vol = top10_vol["거래대금_억"].max() or 1
        rows_html = ""
        for rank, row in enumerate(top10_vol.itertuples(), 1):
            vol = row.거래대금_억
            if vol >= 10_000:
                vol_str = f"{vol/10_000:.2f}조"
            elif vol >= 1_000:
                vol_str = f"{vol/1_000:.1f}천억"
            else:
                vol_str = f"{vol:.0f}억"
            metric_html = f'<div class="metric-val val-vol">{vol_str}</div>'
            bar_pct = vol / max_vol * 100
            rows_html += render_rank_row(rank, row.종목명, metric_html, bar_pct, "#4d9fff")
        st.markdown(rows_html, unsafe_allow_html=True)

st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

# ── 운용사별 점유율 파이 (수익률/거래대금 동시) ─────────────────────────────
st.markdown("### 🏢 운용사별 거래대금 점유율 (Top 10 기준)")

if not top10_vol.empty:
    import plotly.graph_objects as go

    top10_vol["_prov"] = top10_vol["종목명"].apply(lambda n: _detect_provider(n)[0] or "기타")
    prov_vol = top10_vol.groupby("_prov")["거래대금_억"].sum().sort_values(ascending=False)

    prov_colors = {
        "KODEX": "#4d9fff", "TIGER": "#ff8c42", "ACE": "#05b169",
        "RISE": "#a78bfa", "HANARO": "#00c6ff", "SOL": "#f43f5e",
        "KINDEX": "#fb923c", "ARIRANG": "#22d3ee", "기타": "#666",
    }
    colors = [prov_colors.get(p, "#888") for p in prov_vol.index]

    fig = go.Figure(go.Pie(
        labels=prov_vol.index.tolist(),
        values=prov_vol.values.tolist(),
        hole=0.52,
        marker=dict(colors=colors, line=dict(color="#111", width=2)),
        textinfo="label+percent",
        textfont=dict(size=13, family="Pretendard,sans-serif"),
        hovertemplate="%{label}: %{value:.0f}억원 (%{percent})<extra></extra>",
    ))
    fig.update_layout(
        height=340,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e8eaed"),
        showlegend=True,
        legend=dict(
            orientation="v", x=1.02, y=0.5,
            font=dict(size=12),
        ),
        margin=dict(t=10, b=10, l=0, r=120),
    )
    st.plotly_chart(fig, use_container_width=True)

# ── 새로고침 안내 ─────────────────────────────────────────────────────────────
st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
if st.button("🔄 데이터 새로고침", key="trend_refresh"):
    trend_cache_local = load_trend_cache()
    if selected_week in trend_cache_local:
        del trend_cache_local[selected_week]
        # 캐시 파일 다시 쓰기
        from krx_data_fetcher import TREND_CACHE_FILE
        if trend_cache_local:
            all_dfs = []
            for wk, wdf in trend_cache_local.items():
                wdf = wdf.copy(); wdf["week"] = wk; all_dfs.append(wdf)
            pd.concat(all_dfs, ignore_index=True).to_parquet(TREND_CACHE_FILE, index=False)
        else:
            import os as _os
            if _os.path.exists(TREND_CACHE_FILE):
                _os.remove(TREND_CACHE_FILE)
    st.rerun()

st.caption(f"데이터 출처: 네이버 금융 · {week_str} 기준 · 삼성자산운용 ETF 마케팅 AI Agent")

# ── 전략 매트릭스: 수익률 vs 순매수 ─────────────────────────────────────────
st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
st.markdown("### 🎯 ETF 전략 매트릭스 — 수익률 × 순매수")
st.caption("수익률(성과)과 순매수(투자자 유입) 두 축으로 마케팅 전략 방향을 분류합니다.")

# 순매수 데이터 (KRX 캐시)
_krx_df = krx_sheets.get(selected_week, pd.DataFrame())

if _krx_df.empty:
    st.info("순매수 데이터 없음 — KRX 캐시가 비어있습니다.")
else:
    # 순매수 계산: 금융투자 + 은행 + 개인 합산
    _num_cols = [c for c in ["금융투자", "은행", "개인"] if c in _krx_df.columns]
    if not _num_cols:
        st.info("순매수 컬럼을 찾을 수 없습니다.")
    else:
        _krx_df = _krx_df.copy()
        _krx_df["순매수"] = _krx_df[_num_cols].fillna(0).sum(axis=1)
        _code_col = "종목코드" if "종목코드" in _krx_df.columns else "단축코드"
        if "종목코드" not in _krx_df.columns and "단축코드" in _krx_df.columns:
            _krx_df = _krx_df.rename(columns={"단축코드": "종목코드"})
        _krx_df["종목코드"] = _krx_df["종목코드"].astype(str).str.strip()

        # 수익률 데이터와 merge
        _ret_df = df_trend[["종목코드", "종목명", "수익률_pct"]].copy()
        _ret_df["종목코드"] = _ret_df["종목코드"].astype(str).str.strip()
        _mx_full = pd.merge(_krx_df[["종목코드", "순매수"]], _ret_df, on="종목코드", how="inner")
        _mx_full = _mx_full.dropna(subset=["수익률_pct", "순매수"])
        # 수익률Top10 + Bottom10 + 거래대금Top10 = 최대 30개만 표시
        _top_ret  = _mx_full.nlargest(10,  "수익률_pct")["종목코드"]
        _bot_ret  = _mx_full.nsmallest(10, "수익률_pct")["종목코드"]
        _top_vol  = _mx_full.nlargest(10,  "순매수")["종목코드"]
        _sel_codes = set(_top_ret) | set(_bot_ret) | set(_top_vol)
        _mx = _mx_full[_mx_full["종목코드"].isin(_sel_codes)].copy()

        if _mx.empty:
            st.info("수익률 + 순매수 교집합 데이터가 없습니다.")
        else:
            import plotly.graph_objects as go

            # 중앙값 기준 사분면
            _med_ret = _mx["수익률_pct"].median()
            _med_net = _mx["순매수"].median()

            def _quadrant(row):
                r, n = row["수익률_pct"], row["순매수"]
                if r >= _med_ret and n >= _med_net:  return "⭐ 스타 (유지·강화)"
                if r >= _med_ret and n <  _med_net:  return "📣 공격적 마케팅 필요"
                if r <  _med_ret and n >= _med_net:  return "📚 교육형 마케팅"
                return "🔄 리포지셔닝 검토"

            _mx["전략"] = _mx.apply(_quadrant, axis=1)
            _mx["is_kodex"] = _mx["종목명"].str.contains("KODEX", case=False, na=False)

            _Q_COLOR = {
                "⭐ 스타 (유지·강화)":    "#05b169",
                "📣 공격적 마케팅 필요":  "#4d9fff",
                "📚 교육형 마케팅":        "#f0c040",
                "🔄 리포지셔닝 검토":      "#f43f5e",
            }

            fig_mx = go.Figure()

            # 사분면 배경
            _x_min, _x_max = _mx["순매수"].min(), _mx["순매수"].max()
            _y_min, _y_max = _mx["수익률_pct"].min(), _mx["수익률_pct"].max()
            _xpad = (_x_max - _x_min) * 0.1 or 1e6
            _ypad = (_y_max - _y_min) * 0.1 or 0.5

            _quad_cfg = [
                (_med_net, _med_ret, _x_max + _xpad, _y_max + _ypad, "rgba(5,177,105,0.07)",  "⭐ 스타"),
                (_x_min - _xpad, _med_ret, _med_net, _y_max + _ypad, "rgba(77,159,255,0.07)", "📣 공격적 마케팅"),
                (_med_net, _y_min - _ypad, _x_max + _xpad, _med_ret, "rgba(240,192,64,0.07)", "📚 교육형"),
                (_x_min - _xpad, _y_min - _ypad, _med_net, _med_ret, "rgba(244,63,94,0.07)",  "🔄 리포지셔닝"),
            ]
            for x0, y0, x1, y1, color, _ in _quad_cfg:
                fig_mx.add_shape(type="rect", x0=x0, y0=y0, x1=x1, y1=y1,
                                 fillcolor=color, line_width=0, layer="below")

            # 중앙선
            fig_mx.add_shape(type="line", x0=_med_net, x1=_med_net,
                             y0=_y_min - _ypad, y1=_y_max + _ypad,
                             line=dict(color="rgba(255,255,255,0.2)", width=1, dash="dot"))
            fig_mx.add_shape(type="line", y0=_med_ret, y1=_med_ret,
                             x0=_x_min - _xpad, x1=_x_max + _xpad,
                             line=dict(color="rgba(255,255,255,0.2)", width=1, dash="dot"))

            # 사분면 라벨
            _lbl_pos = [
                (_x_max,           _y_max,           "⭐ 스타",         "#05b169", "right", "top"),
                (_x_min,           _y_max,           "📣 공격적 마케팅", "#4d9fff", "left",  "top"),
                (_x_max,           _y_min,           "📚 교육형",        "#f0c040", "right", "bottom"),
                (_x_min,           _y_min,           "🔄 리포지셔닝",    "#f43f5e", "left",  "bottom"),
            ]
            for lx, ly, ltxt, lc, xanc, yanc in _lbl_pos:
                fig_mx.add_annotation(x=lx, y=ly, text=f"<b>{ltxt}</b>",
                                      showarrow=False, font=dict(size=11, color=lc),
                                      xanchor=xanc, yanchor=yanc, opacity=0.7)

            # 비KODEX 점 (배경)
            _bg = _mx[~_mx["is_kodex"]]
            fig_mx.add_trace(go.Scatter(
                x=_bg["순매수"], y=_bg["수익률_pct"],
                mode="markers",
                marker=dict(size=5, color="rgba(150,150,150,0.25)", line=dict(width=0)),
                hovertemplate="<b>%{customdata[0]}</b><br>수익률: %{y:.2f}%<br>순매수: %{x:,.0f}만원<extra></extra>",
                customdata=_bg[["종목명"]].values,
                name="기타 ETF",
                showlegend=True,
            ))

            # KODEX 점 (전략별 색상)
            _kx = _mx[_mx["is_kodex"]]
            for strat, color in _Q_COLOR.items():
                _sub = _kx[_kx["전략"] == strat]
                if _sub.empty:
                    continue
                fig_mx.add_trace(go.Scatter(
                    x=_sub["순매수"], y=_sub["수익률_pct"],
                    mode="markers+text",
                    marker=dict(size=11, color=color,
                                line=dict(color="rgba(255,255,255,0.5)", width=1.2),
                                symbol="circle"),
                    text=_sub["종목명"].str.replace("KODEX ", "", regex=False).str[:10],
                    textposition="top center",
                    textfont=dict(size=9, color=color),
                    hovertemplate=(
                        "<b>%{customdata[0]}</b><br>"
                        "수익률: %{y:.2f}%<br>"
                        "순매수: %{x:,.0f}만원<br>"
                        f"전략: {strat}<extra></extra>"
                    ),
                    customdata=_sub[["종목명"]].values,
                    name=strat,
                ))

            fig_mx.update_layout(
                height=500,
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(20,20,30,0.6)",
                font=dict(color="#e8eaed", family="Pretendard,sans-serif"),
                xaxis=dict(
                    title="순매수 (금융투자+은행+개인, 천원)",
                    gridcolor="rgba(255,255,255,0.05)",
                    zerolinecolor="rgba(255,255,255,0.15)",
                    tickformat=",",
                ),
                yaxis=dict(
                    title="주간 수익률 (%)",
                    gridcolor="rgba(255,255,255,0.05)",
                    zerolinecolor="rgba(255,255,255,0.15)",
                    ticksuffix="%",
                ),
                legend=dict(
                    orientation="h", x=0.5, y=-0.18, xanchor="center",
                    font=dict(size=11),
                    bgcolor="rgba(0,0,0,0)",
                ),
                margin=dict(t=20, b=60, l=60, r=20),
                hoverlabel=dict(
                    bgcolor="rgba(20,20,30,0.92)",
                    bordercolor="rgba(255,255,255,0.15)",
                    font=dict(color="#e8eaed"),
                ),
            )
            st.plotly_chart(fig_mx, use_container_width=True)

            # 전략별 요약 카드
            st.markdown("""
<style>
.mx-card { border-radius:12px; padding:12px 16px; margin:6px 0;
           border:1px solid; font-size:.82rem; }
.mx-card-title { font-weight:700; font-size:.88rem; margin-bottom:4px; }
.mx-card-desc  { color:#aaa; line-height:1.5; }
</style>""", unsafe_allow_html=True)

            _strat_desc = {
                "⭐ 스타 (유지·강화)":    ("수익률↑ + 순매수↑", "#05b169", "성과도 좋고 투자자 유입도 활발. 현 마케팅 기조 유지 + 브랜드 강화"),
                "📣 공격적 마케팅 필요":  ("수익률↑ + 순매수↓", "#4d9fff", "성과는 좋은데 투자자가 모름. 인지도 공략 — 광고·채널 확대"),
                "📚 교육형 마케팅":        ("수익률↓ + 순매수↑", "#f0c040", "사람은 오는데 성과가 아쉬움. 장기투자 가치·전략 교육 콘텐츠"),
                "🔄 리포지셔닝 검토":      ("수익률↓ + 순매수↓", "#f43f5e", "성과·유입 모두 부진. 전략 재검토 또는 마케팅 축소 고려"),
            }
            _cnt_map = _kx["전략"].value_counts()
            c1, c2, c3, c4 = st.columns(4)
            for col, (strat, (axis_desc, color, desc)) in zip([c1,c2,c3,c4], _strat_desc.items()):
                cnt = _cnt_map.get(strat, 0)
                with col:
                    st.markdown(
                        f"""<div class="mx-card" style="border-color:{color}44;background:{color}0d;">
                        <div class="mx-card-title" style="color:{color};">{strat}</div>
                        <div style="font-size:.72rem;color:{color}88;margin-bottom:4px;">{axis_desc} · KODEX {cnt}개</div>
                        <div class="mx-card-desc">{desc}</div>
                        </div>""",
                        unsafe_allow_html=True,
                    )
