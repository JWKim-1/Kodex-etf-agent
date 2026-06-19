"""
마케팅 백테스트 분석
marketing_history.json의 마케팅 이벤트와 did_history.parquet/bank_zscore_history.parquet를 대조해
채널별로 마케팅 활동이 DiD/Z-score에 유의미한 영향을 줬는지 분석하고 결과를 저장한다.

데이터 범위 현황 (2026-06):
- marketing_history: 6.8-6.12, 6.15-6.19 (2주)
- did_history(securities): 3.2-3.6 ~ 3.16-3.20 (3월, 마케팅 이력 전)
- did_history(mass): 5.25-5.28, 6.1-6.5, 6.9-6.13
- bank_zscore: 6.1-6.5, 6.9-6.13
"""

import json
import os
import sys
from datetime import date, timedelta
from datetime import datetime

# Windows 콘솔 UTF-8 강제
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

import numpy as np
import pandas as pd
from scipy.stats import ttest_ind, pearsonr

from krx_data_fetcher import _parse_week_label

ROOT = os.path.dirname(os.path.abspath(__file__))

MARKETING_HISTORY_FILE = os.path.join(ROOT, "marketing_history.json")
DID_HISTORY_FILE = os.path.join(ROOT, "did_history.parquet")
BANK_ZSCORE_FILE = os.path.join(ROOT, "bank_zscore_history.parquet")
OUTPUT_FILE = os.path.join(ROOT, "marketing_backtest_result.json")


# ── 0. 주차 레이블 Fuzzy 매핑 ────────────────────────────────────────────────

def _week_start_end(label: str):
    """'6.8-6.12' -> (date(2026,6,8), date(2026,6,12))"""
    d_start = _parse_week_label(label)
    if d_start is None:
        return None, None
    # end: 레이블의 두 번째 날짜
    parts = label.split("-")
    if len(parts) == 2:
        end_str = parts[1].strip()  # '6.12'
        ep = end_str.split(".")
        if len(ep) == 2:
            try:
                d_end = date(d_start.year, int(ep[0]), int(ep[1]))
                return d_start, d_end
            except Exception:
                pass
    return d_start, d_start + timedelta(days=4)


def build_week_map(mkt_weeks: list, data_weeks: list) -> dict:
    """
    마케팅 주차 레이블 -> 데이터 주차 레이블 매핑 (날짜 겹침 기반)
    겹치는 날짜가 1일 이상이면 매핑, 없으면 None
    """
    mapping = {}
    for mw in mkt_weeks:
        m_start, m_end = _week_start_end(mw)
        if m_start is None:
            continue
        best = None
        best_overlap = 0
        for dw in data_weeks:
            d_start, d_end = _week_start_end(dw)
            if d_start is None:
                continue
            overlap_start = max(m_start, d_start)
            overlap_end = min(m_end, d_end)
            overlap = (overlap_end - overlap_start).days + 1
            if overlap > best_overlap:
                best_overlap = overlap
                best = dw
        if best and best_overlap >= 1:
            mapping[mw] = best
        else:
            mapping[mw] = None  # 매핑 불가
    return mapping


# ── 1. 마케팅 이벤트 로드 ─────────────────────────────────────────────────────

def load_marketing_flags(history: dict) -> pd.DataFrame:
    """주차별 채널별 이벤트 개수 및 마케팅 여부 DataFrame 반환"""
    rows = []
    for week, entry in history.items():
        row = {"week": week}
        for ch in ["securities", "bank", "mass"]:
            sess = entry.get(ch) or {}
            events_data = sess.get("events") or {}
            events_list = events_data.get("events") or []
            row[f"{ch}_n_events"] = len(events_list)
            row[f"{ch}_has_marketing"] = len(events_list) > 0
        rows.append(row)
    return pd.DataFrame(rows)


# ── 2. DiD 데이터 로드 ────────────────────────────────────────────────────────

def load_did() -> pd.DataFrame:
    """did_history.parquet 로드."""
    df = pd.read_parquet(DID_HISTORY_FILE)
    return df[["week", "channel", "code", "name", "did"]].copy()


def load_bank_zscore() -> pd.DataFrame:
    """bank_zscore_history.parquet 로드. value -> did로 통일."""
    df = pd.read_parquet(BANK_ZSCORE_FILE)
    out = df[["week", "channel", "code", "name", "value", "marketing_detected"]].copy()
    out = out.rename(columns={"value": "did"})
    return out


# ── 3. 채널별 분석 ────────────────────────────────────────────────────────────

def analyze_channel(
    channel_key: str,
    mkt_df_mapped: pd.DataFrame,   # week 컬럼이 이미 데이터 주차 레이블로 변환됨
    values_df: pd.DataFrame,        # week, code, name, did
    data_limitation: str = "",
) -> dict:
    """채널별 마케팅 유효성 분석. values_df의 did NaN 제외."""
    has_col = f"{channel_key}_has_marketing"
    n_col = f"{channel_key}_n_events"

    # 매핑된 주차만 사용
    mkt_map_valid = mkt_df_mapped.dropna(subset=["week"])

    if mkt_map_valid.empty:
        print(f"  [{channel_key}] 매핑된 주차 없음 — 분석 불가")
        return {
            "status": "insufficient_data",
            "reason": "마케팅 이력과 DiD 데이터 간 겹치는 주차 없음",
            "data_limitation": data_limitation,
        }

    # week -> (has_marketing, n_events) 맵
    mkt_index = mkt_map_valid.set_index("week")[[has_col, n_col]]

    # values_df에 마케팅 플래그 병합 후 NaN 제거
    merged = values_df.merge(mkt_index, on="week", how="inner")
    merged = merged.dropna(subset=["did"])

    if merged.empty:
        print(f"  [{channel_key}] 유효 DiD 값 없음 (모두 NaN 또는 주차 불일치)")
        return {
            "status": "insufficient_data",
            "reason": "유효한 DiD/Z-score 값 없음 (NaN 또는 주차 불일치)",
            "data_limitation": data_limitation,
        }

    weeks_with = merged[merged[has_col] == True]["week"].unique()
    weeks_without = merged[merged[has_col] == False]["week"].unique()

    print(f"\n  [{channel_key}]")
    print(f"    마케팅 있는 주: {len(weeks_with)}주 {list(weeks_with)}")
    print(f"    마케팅 없는 주: {len(weeks_without)}주 {list(weeks_without)}")

    with_vals = merged[merged[has_col] == True]["did"].values
    without_vals = merged[merged[has_col] == False]["did"].values

    mean_with = float(np.mean(with_vals)) if len(with_vals) > 0 else None
    mean_without = float(np.mean(without_vals)) if len(without_vals) > 0 else None

    t_stat, p_value, significant = None, None, False
    if len(with_vals) >= 2 and len(without_vals) >= 2:
        t_stat, p_value = ttest_ind(with_vals, without_vals, equal_var=False)
        t_stat, p_value = float(t_stat), float(p_value)
        significant = p_value < 0.05
        print(f"    전체 평균 DiD — 마케팅O: {mean_with:.4f}, 마케팅X: {mean_without:.4f}")
        print(f"    t-검정: t={t_stat:.3f}, p={p_value:.4f} {'★ 유의' if significant else '비유의'}")
    else:
        print(f"    전체 평균 DiD — 마케팅O: {mean_with}, 마케팅X: {mean_without}")
        print(f"    샘플 부족으로 t-검정 생략 (with={len(with_vals)}, without={len(without_vals)})")

    # Pearson 상관: 이벤트 개수 vs 주간 평균 DiD
    pearson_r, pearson_p = None, None
    week_avg = merged.groupby("week").agg(
        mean_did=("did", "mean"),
        n_events=(n_col, "first"),
    ).reset_index()

    if len(week_avg) >= 3:
        r, p = pearsonr(week_avg["n_events"].values, week_avg["mean_did"].values)
        pearson_r, pearson_p = float(r), float(p)
        print(f"    Pearson r(이벤트수, 주간DiD) = {pearson_r:.4f}, p={pearson_p:.4f}")
    else:
        print(f"    주차 수 부족으로 Pearson 상관 생략 (n={len(week_avg)})")

    # ETF별 분해
    by_etf = {}
    for code, grp in merged.groupby("code"):
        name = grp["name"].iloc[0]
        g_with = grp[grp[has_col] == True]["did"].values
        g_without = grp[grp[has_col] == False]["did"].values
        by_etf[str(code)] = {
            "name": str(name),
            "mean_with": float(np.mean(g_with)) if len(g_with) > 0 else None,
            "mean_without": float(np.mean(g_without)) if len(g_without) > 0 else None,
            "n_with": int(len(g_with)),
            "n_without": int(len(g_without)),
        }

    # ETF별 효과 크기 순
    etf_effect = [
        (code, v)
        for code, v in by_etf.items()
        if v["mean_with"] is not None and v["mean_without"] is not None
    ]
    etf_effect.sort(key=lambda x: (x[1]["mean_with"] or 0) - (x[1]["mean_without"] or 0), reverse=True)
    print(f"    ETF별 효과 상위 5개:")
    for code, v in etf_effect[:5]:
        diff = (v["mean_with"] or 0) - (v["mean_without"] or 0)
        print(f"      {code} {v['name']}: Δ={diff:+.4f}")

    return {
        "status": "ok",
        "n_weeks_with_marketing": int(len(weeks_with)),
        "n_weeks_without": int(len(weeks_without)),
        "mean_did_with": mean_with,
        "mean_did_without": mean_without,
        "t_stat": t_stat,
        "p_value": p_value,
        "significant": significant,
        "pearson_r": pearson_r,
        "pearson_p": pearson_p,
        "data_limitation": data_limitation,
        "by_etf": by_etf,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("마케팅 백테스트 분석 시작")
    print("=" * 60)

    # 마케팅 히스토리 로드
    print("\n[1] marketing_history.json 로드...")
    with open(MARKETING_HISTORY_FILE, encoding="utf-8") as f:
        history = json.load(f)
    print(f"    총 {len(history)}주차 데이터: {list(history.keys())}")

    mkt_df = load_marketing_flags(history)
    print(f"    채널별 이벤트 집계:\n{mkt_df.to_string(index=False)}")

    # DiD 히스토리 로드
    print("\n[2] did_history.parquet 로드...")
    did_df = load_did()
    print(f"    rows={len(did_df)}, 주차={sorted(did_df['week'].unique())}")
    print(f"    채널별: {dict(did_df.groupby('channel')['week'].apply(lambda x: sorted(x.unique())))}")

    # 은행 Z-score 로드
    print("\n[3] bank_zscore_history.parquet 로드...")
    bank_df = load_bank_zscore()
    print(f"    rows={len(bank_df)}, 주차={sorted(bank_df['week'].unique())}")

    # 주차 매핑
    print("\n[4] 주차 레이블 Fuzzy 매핑...")
    all_data_weeks = sorted(set(did_df["week"].unique()) | set(bank_df["week"].unique()))
    week_map = build_week_map(list(mkt_df["week"].values), all_data_weeks)
    print(f"    마케팅→데이터 주차 매핑: {week_map}")

    # mkt_df에 데이터 주차 레이블 컬럼 추가
    mkt_df_mapped = mkt_df.copy()
    mkt_df_mapped["week"] = mkt_df_mapped["week"].map(week_map)
    n_mapped = mkt_df_mapped["week"].notna().sum()
    print(f"    매핑 성공: {n_mapped} / {len(mkt_df)} 주차")

    # 채널별 분석
    print("\n[5] 채널별 분석...")
    channels_result = {}

    # securities — did_history (channel='securities')
    sec_did = did_df[did_df["channel"] == "securities"][["week", "code", "name", "did"]].copy()
    sec_limit = (
        f"did_history(securities) 데이터: {sorted(sec_did['week'].unique())} | "
        f"marketing_history: {list(history.keys())} | "
        "두 기간이 겹치지 않아 통계 분석 불가"
        if sec_did.empty or n_mapped == 0
        else ""
    )
    channels_result["securities"] = analyze_channel(
        "securities", mkt_df_mapped, sec_did, data_limitation=sec_limit
    )

    # mass — did_history (channel='mass')
    mass_did = did_df[did_df["channel"] == "mass"][["week", "code", "name", "did"]].copy()
    mass_limit = (
        f"did_history(mass) 주차: {sorted(mass_did['week'].unique())} | "
        "6.9-6.13 주차 did 값이 모두 NaN (수집 미완성 가능성)"
        if mass_did["did"].isna().all()
        else ""
    )
    channels_result["mass"] = analyze_channel(
        "mass", mkt_df_mapped, mass_did, data_limitation=mass_limit
    )

    # bank — bank_zscore_history
    # bank_zscore에는 자체 marketing_detected 컬럼 있음 — 보조 분석으로도 활용
    bank_vals = bank_df[["week", "code", "name", "did"]].copy()
    bank_limit = (
        f"bank_zscore 주차: {sorted(bank_df['week'].unique())} | "
        f"marketing_history(bank) 이벤트 있는 주: "
        f"{[w for w in history if (history[w].get('bank',{}).get('events',{}).get('events') or [])]}"
    )
    channels_result["bank"] = analyze_channel(
        "bank", mkt_df_mapped, bank_vals, data_limitation=bank_limit
    )

    # bank_zscore 내장 marketing_detected로 보조 분석
    print("\n  [bank 보조 분석: bank_zscore 내장 marketing_detected 기준]")
    bank_with = bank_df[bank_df["marketing_detected"] == True]["did"].dropna().values
    bank_without = bank_df[bank_df["marketing_detected"] == False]["did"].dropna().values
    bank_aux = {}
    if len(bank_with) >= 2 and len(bank_without) >= 2:
        t2, p2 = ttest_ind(bank_with, bank_without, equal_var=False)
        bank_aux = {
            "mean_did_with": float(np.mean(bank_with)),
            "mean_did_without": float(np.mean(bank_without)),
            "t_stat": float(t2),
            "p_value": float(p2),
            "significant": float(p2) < 0.05,
            "note": "bank_zscore_history 내장 marketing_detected 컬럼 기준 분석",
        }
        print(f"    마케팅O avg={bank_aux['mean_did_with']:.4f}, X avg={bank_aux['mean_did_without']:.4f}")
        print(f"    t={bank_aux['t_stat']:.3f}, p={bank_aux['p_value']:.4f}")
    else:
        bank_aux = {
            "note": "bank_zscore 내 marketing_detected=False 주차 없음 (모든 수집 주차가 마케팅 기간)",
            "mean_did_with": float(np.mean(bank_with)) if len(bank_with) > 0 else None,
            "mean_did_without": None,
        }
        print(f"    {bank_aux['note']}")
        if bank_aux["mean_did_with"] is not None:
            print(f"    마케팅 있는 주 평균 Z-score: {bank_aux['mean_did_with']:.4f}")

    # 결과 저장
    result = {
        "generated_at": datetime.now().isoformat(),
        "n_total_weeks_in_marketing_history": len(history),
        "marketing_history_weeks": list(history.keys()),
        "week_map": week_map,
        "data_weeks": {
            "did_securities": sorted(did_df[did_df["channel"] == "securities"]["week"].unique()),
            "did_mass": sorted(did_df[did_df["channel"] == "mass"]["week"].unique()),
            "bank_zscore": sorted(bank_df["week"].unique()),
        },
        "channels": channels_result,
        "bank_aux_analysis": bank_aux,
        "analysis_note": (
            "현재 marketing_history(2주)와 did/bank 데이터 기간 불일치로 대부분 채널에서 "
            "통계적 유의성 검증이 제한됩니다. 데이터가 누적될수록 분석 신뢰도 향상 예상."
        ),
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n[6] 결과 저장 완료: {OUTPUT_FILE}")
    print("\n" + "=" * 60)
    print("분석 완료")
    print("=" * 60)

    # 요약 출력
    print("\n📊 채널별 요약:")
    for ch, res in channels_result.items():
        print(f"\n  [{ch}]")
        if res.get("status") == "insufficient_data":
            print(f"    ⚠ 데이터 부족: {res.get('reason')}")
            continue
        mw = res.get("mean_did_with")
        mwo = res.get("mean_did_without")
        print(f"    마케팅 있는 주: {res.get('n_weeks_with_marketing')}주, 없는 주: {res.get('n_weeks_without')}주")
        if mw is not None:
            print(f"    평균 DiD — 있음: {mw:.4f} / 없음: {mwo}")
        pv = res.get("p_value")
        if pv is not None:
            sig = "★ 유의 (p<0.05)" if res.get("significant") else "비유의"
            print(f"    p-value: {pv:.4f} → {sig}")
        pr = res.get("pearson_r")
        if pr is not None:
            print(f"    Pearson r: {pr:.4f}")
        if res.get("data_limitation"):
            print(f"    ⚠ 데이터 제약: {res['data_limitation']}")

    print(f"\n  [bank 보조 분석]")
    note = bank_aux.get("note", "")
    mw2 = bank_aux.get("mean_did_with")
    mwo2 = bank_aux.get("mean_did_without")
    if mw2 is not None:
        print(f"    마케팅O 평균 Z-score: {mw2:.4f}")
    if mwo2 is not None:
        print(f"    마케팅X 평균 Z-score: {mwo2:.4f}")
    if bank_aux.get("significant") is not None:
        sig = "★ 유의" if bank_aux["significant"] else "비유의"
        print(f"    p={bank_aux['p_value']:.4f} → {sig}")
    print(f"    {note}")


if __name__ == "__main__":
    main()
