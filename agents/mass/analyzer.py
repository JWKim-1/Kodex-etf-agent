"""
대고객 디지털(매스) 채널 DiD Analyzer
공용 로직: did_calculator.MarketingAnalyzerBase 상속

증권사와 차이:
- 기준 컬럼: 개인 (LP 노이즈 없음 → LP 감지 불필요)
- 채널: 삼성자산운용 직접 채널 (유튜브/블로그/이벤트)
- 베이스라인: 4주 (증권사와 동일)
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from typing import Dict, List, Optional
import numpy as np
import pandas as pd

from did_calculator import (
    MarketingAnalyzerBase, ExcelLoader,
    ETFWeekData, Baseline, LPResult, CompetitorResult, ETFDiDResult,
    COMPETITOR_PREFIXES, extract_keyword, auto_map_competitors,
    extract_target_etfs_with_llm,
)
from etf_mapping_loader import get_competitors as _get_comp

import logging
logger = logging.getLogger(__name__)


class MassAnalyzer(MarketingAnalyzerBase):
    """
    대고객 디지털 채널: 개인 컬럼, 8주 베이스라인, LP 감지 없음.
    삼성자산운용이 직접 소비자에게 하는 마케팅 효과 측정.
    """
    TARGET_COLUMN = "individual"   # 개인 순매수
    BASELINE_WEEKS = 8
    ZSCORE_WINDOW = 15
    USE_LP_DETECTION = False       # 개인 컬럼은 LP 노이즈 구조적으로 없음
    CHANNEL_TYPE = "mass"

    def analyze(
        self,
        all_sheets: Dict[str, pd.DataFrame],
        target_etf_codes: List[str],
        current_sheet_name: str,
    ) -> Dict[str, ETFDiDResult]:
        sheet_names = list(all_sheets.keys())
        current_df = all_sheets[current_sheet_name]
        current_idx = sheet_names.index(current_sheet_name)
        history_sheets = {k: all_sheets[k] for k in sheet_names[:current_idx]}

        _code_col = "단축코드" if "단축코드" in current_df.columns else "종목코드"
        etf_universe = current_df[[_code_col, "종목명"]].rename(
            columns={_code_col: "종목코드"}).dropna(subset=["종목명"])

        results = {}
        for code in target_etf_codes:
            row = self.loader.get_etf_row(current_df, code, code)
            kodex_name = row.name if row else code
            result = self._analyze_one(
                code, kodex_name, history_sheets, current_df,
                current_sheet_name, etf_universe
            )
            if result:
                # ── 2단계: Z-score + sigmoid 점수 ──
                if not result.no_competitors:
                    did_history = []
                    for hw in list(history_sheets.keys())[-self.ZSCORE_WINDOW:]:
                        hidx = sheet_names.index(hw)
                        hhistory = {k: all_sheets[k] for k in sheet_names[:hidx]}
                        hres = self._analyze_one(code, kodex_name, hhistory, all_sheets[hw], hw, etf_universe)
                        if hres and not hres.no_competitors:
                            did_history.append(hres.did_value)
                    z, score = self._compute_zscore_score(result.did_value, did_history)
                    if z is not None:
                        result.raw_did_value = result.did_value
                        result.zscore = z
                        result.marketing_score = score
                        result.did_value = z
                        j, e = self._judge_score(score)
                        result.judgement = j
                        result.judgement_emoji = e
                        result.calculation_log.append(
                            f"[2단계 Z-score] 이력 {len(did_history)}주  Z={z:+.4f}  점수={score:.1f}"
                        )
                results[code] = result

        # DiD 결과 누적 저장
        try:
            from did_history import save_results
            save_results(current_sheet_name, [
                {"code": c, "name": r.kodex_name, "did": r.did_value,
                 "judgement": r.judgement, "marketing_detected": True,
                 "no_competitors": r.no_competitors}
                for c, r in results.items()
            ], channel_type="mass")
        except Exception:
            pass

        return results

    def _analyze_one(
        self,
        kodex_code: str,
        kodex_name: str,
        history: Dict[str, pd.DataFrame],
        current_df: pd.DataFrame,
        week_label: str,
        etf_universe: pd.DataFrame,
    ) -> Optional[ETFDiDResult]:

        current_kodex = self.loader.get_etf_row(current_df, kodex_code, kodex_name)
        if current_kodex is None:
            return None
        current_kodex.week_label = week_label

        log = []
        notes = []
        log.append(f"[KODEX 현재주] 개인={current_kodex.individual:,.0f}")

        baseline = self._compute_baseline(kodex_code, kodex_name, history)
        log.append(f"[베이스라인] 개인 {self.BASELINE_WEEKS}주평균={baseline.ind_avg:,.0f} ({baseline.weeks_used}주)")

        # LP 감지 없음 — 개인 컬럼은 LP 노이즈 구조적으로 없음
        lp = self._get_lp_result_noop(kodex_code)

        # 비교군 정의 (공용 매핑)
        if _get_comp(kodex_code):
            comp_defs = _get_comp(kodex_code)
            mapping_source = "사전 매핑"
        else:
            comp_defs = auto_map_competitors(kodex_name, kodex_code, etf_universe)
            mapping_source = f"실시간 매핑 (키워드: '{extract_keyword(kodex_name)}')"
        log.append(f"[비교군 매핑] {mapping_source} → {[c['name'] for c in comp_defs]}")

        no_competitors = len(comp_defs) == 0
        competitor_results: List[CompetitorResult] = []
        ctrl_vals = []
        tiger_chg = ace_chg = None

        for comp in comp_defs:
            ccode, cname = comp["code"], comp["name"]
            cdata = self.loader.get_etf_row(current_df, ccode, cname)
            if cdata is None:
                continue
            cb = self._compute_baseline(ccode, cname, history)
            # 매스는 항상 개인 컬럼
            cchg = self._normalized_change(cdata.individual, cb.ind_avg, cb.ind_mabs)
            log.append(f"  · {cname}: ({cdata.individual:,.0f} − {cb.ind_avg:,.0f}) ÷ {cb.ind_mabs:,.0f} = {cchg:+.4f}")
            ctrl_vals.append(cchg)
            competitor_results.append(CompetitorResult(
                code=ccode, name=cname, provider=comp.get("provider", ""),
                change_pct=cchg,
                current_fi=cdata.financial_investment, current_ind=cdata.individual,
                baseline_fi_avg=cb.fi_avg, baseline_ind_avg=cb.ind_avg,
                metric_used="individual",
                corr=comp.get("corr"),
            ))
            if comp.get("provider") == "TIGER": tiger_chg = cchg
            elif comp.get("provider") in ("ACE", "PLUS"): ace_chg = cchg

        # 개인 컬럼 변화율
        kodex_chg = self._normalized_change(current_kodex.individual, baseline.ind_avg, baseline.ind_mabs)
        log.append(f"[KODEX 변화율] ({current_kodex.individual:,.0f} − {baseline.ind_avg:,.0f}) ÷ {baseline.ind_mabs:,.0f} = {kodex_chg:+.4f}")

        if ctrl_vals:
            control_avg = float(np.mean(ctrl_vals))
            did = kodex_chg - control_avg
            log.append(f"[DiD 계산] KODEX({kodex_chg:+.4f}) - 비교군평균({control_avg:+.4f}) = {did:+.4f}")
        else:
            control_avg = 0.0
            did = kodex_chg
            log.append(f"[DiD] 비교군 없음 → KODEX 절대 변화율 {did:+.4f}")

        judgement, emoji = self._judge(did) if not no_competitors else ("비교군 없음 — DiD 불가", "⚫")
        log.append(f"[판정] {emoji} {judgement}")

        # 기저효과 착시 경고
        try:
            from did_history import check_base_effect
            warn = check_base_effect(kodex_code, did, week_label, channel_type="mass")
            if warn:
                notes.append(warn)
        except Exception:
            pass

        return ETFDiDResult(
            kodex_code=kodex_code, kodex_name=kodex_name,
            current=current_kodex, baseline=baseline, lp=lp,
            kodex_change_pct=round(kodex_chg, 2),
            control_avg_pct=round(control_avg, 2),
            did_value=did, judgement=judgement, judgement_emoji=emoji,
            competitors=competitor_results, no_competitors=no_competitors,
            mapping_source=mapping_source,
            tiger_change_pct=round(tiger_chg, 2) if tiger_chg is not None else None,
            ace_change_pct=round(ace_chg, 2) if ace_chg is not None else None,
            notes=notes, calculation_log=log,
        )
