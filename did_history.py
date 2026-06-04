"""
DiD 분석 결과 누적 저장 모듈
- 3개 채널(증권사/은행/매스) 공통 사용
- ETF별 주차별 DiD 결과 parquet으로 저장
- 기저효과 착시 경고에도 활용
"""
import os
import pandas as pd
import numpy as np
from typing import Optional, Dict, List

# 증권사/매스: DiD값 저장
_CACHE_PATH = os.path.join(os.path.dirname(__file__), "did_history.parquet")
# 은행: Z-score 저장 (방법론이 달라 완전 분리)
_BANK_CACHE_PATH = os.path.join(os.path.dirname(__file__), "bank_zscore_history.parquet")

def _get_path(channel_type: str) -> str:
    """채널별 저장 파일 경로. 은행은 별도 파일."""
    return _BANK_CACHE_PATH if channel_type == "bank" else _CACHE_PATH


def save_results(week_label: str, results: List[Dict], channel_type: str = "securities"):
    """
    분석 결과 저장.
    channel_type: "securities" | "bank" | "mass"

    저장 컬럼:
    - securities/mass: did = 정규화 절대 변화 DiD값
    - bank: did = Z-score (2단계 이상지수), 방법론이 달라 채널별로만 비교
    """
    rows = []
    for r in results:
        rows.append({
            "week": week_label,
            "channel": channel_type,
            # 채널별 지표 단위 구분
            # securities/mass: DiD (정규화 절대 변화), bank: Z-score
            "metric": "did" if channel_type in ("securities", "mass") else "zscore",
            "code": r.get("code", ""),
            "name": r.get("name", ""),
            "value": float(r.get("did", 0.0)) if r.get("did") is not None else None,
            "judgement": r.get("judgement", ""),
            "marketing_detected": bool(r.get("marketing_detected", False)),
            "no_competitors": bool(r.get("no_competitors", False)),
        })
    if not rows:
        return

    new_df = pd.DataFrame(rows)

    path = _get_path(channel_type)
    if os.path.exists(path):
        existing = pd.read_parquet(path)
        existing = existing[~(
            existing["week"].isin(new_df["week"]) &
            existing["channel"].isin(new_df["channel"]) &
            existing["code"].isin(new_df["code"])
        )]
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df

    combined.to_parquet(path, index=False)


def load_history(code: str, channel_type: str = "securities",
                 exclude_week: Optional[str] = None) -> pd.DataFrame:
    """특정 ETF의 과거 DiD/Z-score 히스토리 로드."""
    path = _get_path(channel_type)
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_parquet(path)
    mask = (df["code"] == code) & (df["channel"] == channel_type)
    if exclude_week:
        mask &= (df["week"] != exclude_week)
    return df[mask].sort_values("week")


def check_base_effect(code: str, current_did: float,
                      current_week: str, channel_type: str = "securities",
                      lookback: int = 4) -> Optional[str]:
    """
    기저효과 착시 경고 체크.
    직전 N주 DiD 평균이 양수였는데 이번 주 급락 시 경고 반환.
    반환값: 경고 문자열 or None
    """
    hist = load_history(code, channel_type, exclude_week=current_week)
    if len(hist) < 2:
        return None

    recent = hist.tail(lookback)["value"].dropna().tolist()
    if len(recent) < 2:
        return None

    avg_prev = float(np.mean(recent))
    std_prev = float(np.std(recent)) if len(recent) > 1 else 0.0

    # 직전 평균이 +0.3 이상(효과있음 수준)이었는데 이번 주가 평균 - 1σ 미만으로 급락
    if avg_prev >= 0.3 and current_did < avg_prev - max(std_prev, 0.3):
        drop_pct = int((avg_prev - current_did) / max(abs(avg_prev), 0.01) * 100)
        return (f"⚠️ 기저효과 의심: 직전 {len(recent)}주 평균 DiD {avg_prev:+.2f} → "
                f"이번 주 {current_did:+.2f} ({drop_pct}% 급락). "
                f"이전 마케팅 효과가 베이스라인에 흡수됐을 수 있습니다.")

    return None


def get_summary(channel_type: str = "securities") -> pd.DataFrame:
    """채널별 전체 히스토리 요약. 은행은 별도 파일."""
    path = _get_path(channel_type)
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_parquet(path)
    return df[df["channel"] == channel_type].sort_values(["code", "week"])
