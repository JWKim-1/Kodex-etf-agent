"""
증권사 채널 DiD Analyzer
공용 로직은 did_calculator.MarketingAnalyzerBase 에서 상속.
증권사 전용: 금융투자 컬럼, 4주 베이스라인, LP 감지
"""

import json
import logging
import re
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd

# 공용 모듈에서 import
from did_calculator import (
    MarketingAnalyzerBase, ExcelLoader,
    ETFWeekData, Baseline, LPResult, CompetitorResult, ETFDiDResult,
    COMPETITOR_PREFIXES, extract_keyword, _variant_tags_in,
    get_tracking_index, auto_map_competitors, extract_target_etfs_with_llm,
)

logger = logging.getLogger(__name__)


# ── COMPARISON_MAP (레거시 호환용, etf_mapping.json 으로 대체됨) ──────────────
COMPARISON_MAP: Dict[str, Dict] = {
    "069500": {"name": "KODEX 200", "competitors": [
        {"code": "102110", "name": "TIGER 200", "provider": "TIGER"},
        {"code": "152100", "name": "PLUS 200",  "provider": "ACE"},
    ]},
    "229200": {"name": "KODEX 코스닥150", "competitors": [
        {"code": "232080", "name": "TIGER 코스닥150", "provider": "TIGER"},
    ]},
    "091160": {"name": "KODEX 반도체", "competitors": [
        {"code": "091230", "name": "TIGER 반도체", "provider": "TIGER"},
    ]},
    "498400": {"name": "KODEX 200타겟위클리커버드콜", "competitors": [
        {"code": "0104N0", "name": "TIGER 200타겟위클리커버드콜", "provider": "TIGER"},
    ]},
}

ALL_KNOWN_CODES = set(COMPARISON_MAP.keys()) | {
    c["code"] for v in COMPARISON_MAP.values() for c in v["competitors"]
}

# ── 분석 엔진 (LP 감지는 증권사 전용) ────────────────────────────────────────


class MarketingAnalyzer(MarketingAnalyzerBase):
    """증권사 채널: 금융투자 컬럼, 8주 베이스라인, LP 감지 포함."""
    TARGET_COLUMN = "financial"
    BASELINE_WEEKS = 8
    ZSCORE_WINDOW = 15
    USE_LP_DETECTION = True
    CHANNEL_TYPE = "securities"


    def analyze(
        self,
        all_sheets: Dict[str, pd.DataFrame],
        target_etf_codes: List[str],
        current_sheet_name: str,
    ) -> Dict[str, "ETFDiDResult"]:
        sheet_names = list(all_sheets.keys())
        current_df = all_sheets[current_sheet_name]

        # 현재 주 이전 시트만 history로 사용 (시트 순서 = 시간 순서)
        current_idx = sheet_names.index(current_sheet_name)
        history_names = sheet_names[:current_idx]          # 현재 이전만
        history_sheets = {k: all_sheets[k] for k in history_names}

        # 전체 ETF 유니버스 (자동 매핑용)
        etf_universe = current_df[["종목코드", "종목명"]].dropna(subset=["종목명"])

        results = {}
        for code in target_etf_codes:
            # 종목명 확인
            row = self.loader.get_etf_row(current_df, code, code)
            kodex_name = row.name if row else COMPARISON_MAP.get(code, {}).get("name", code)

            result = self._analyze_one(
                code, kodex_name, history_sheets, current_df,
                current_sheet_name, etf_universe
            )
            if result:
                # ── 2단계: Z-score + sigmoid 점수 (비교군 없어도 절대변화율 기반으로 산출) ──
                did_history = []
                for hw in list(history_sheets.keys())[-self.ZSCORE_WINDOW:]:
                    hidx = sheet_names.index(hw)
                    hhistory = {k: all_sheets[k] for k in sheet_names[:hidx]}
                    hres = self._analyze_one(code, kodex_name, hhistory, all_sheets[hw], hw, etf_universe)
                    if hres:
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
                    no_did_note = " ⚠️ DiD 미적용(시장효과 미제거)" if result.no_competitors else ""
                    result.calculation_log.append(
                        f"[2단계 Z-score] 이력 {len(did_history)}주  Z={z:+.4f}  점수={score:.1f}{no_did_note}"
                    )
                    if result.no_competitors:
                        result.notes.append("⚠️ 비교군 없음 — 절대변화율 Z-score (시장 공통 효과 미제거, 참고용)")
                results[code] = result

        # ── DiD 결과 누적 저장 ──
        try:
            import sys as _sys, os as _os
            _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), "../.."))
            from did_history import save_results
            save_results(current_sheet_name, [
                {"code": code, "name": r.kodex_name, "did": r.did_value,
                 "judgement": r.judgement, "marketing_detected": True,
                 "no_competitors": r.no_competitors}
                for code, r in results.items()
            ], channel_type="securities")
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
        log = []
        notes = []

        # ── Step A: 현재 주 Kodex 데이터 ──
        current_kodex = self.loader.get_etf_row(current_df, kodex_code, kodex_name)
        if current_kodex is None:
            logger.warning(f"현재 주 데이터에서 {kodex_name}({kodex_code}) 찾기 실패")
            return None
        current_kodex.week_label = week_label
        log.append(f"[KODEX 현재주] 금융투자={current_kodex.financial_investment:,.0f}  개인={current_kodex.individual:,.0f}")

        # ── Step B: 베이스라인 ──
        baseline = self._compute_baseline(kodex_code, kodex_name, history)
        log.append(
            f"[베이스라인] 금융투자 4주평균={baseline.fi_avg:,.0f} (σ={baseline.fi_std:,.0f})  "
            f"개인 4주평균={baseline.ind_avg:,.0f} ({baseline.weeks_used}주 사용)"
        )

        # ── Step B-0: 베이스라인 부족 시 AUM 상대강도 폴백 ──
        if baseline.weeks_used < self.BASELINE_WEEKS:
            from etf_mapping_loader import get_competitors as _gc
            _code_s = kodex_code.replace("*001", "")
            _comp_defs = _gc(_code_s) or auto_map_competitors(kodex_name, _code_s, etf_universe)
            return self._aum_did_fallback(
                kodex_code, kodex_name, current_df, current_kodex,
                _comp_defs, "financial", baseline.weeks_used, log
            )

        # ── Step B-1: 비교군 정의 (LP 감지 전에 필요) ──
        # 우선순위: ① etf_mapping.json (사전 매핑) ② 실시간 auto_map (fallback)
        from etf_mapping_loader import get_competitors as _get_comp
        if _get_comp(kodex_code):
            comp_defs = _get_comp(kodex_code)
            mapping_source = "사전 매핑"
        else:
            comp_defs = auto_map_competitors(kodex_name, kodex_code, etf_universe)
            mapping_source = f"실시간 매핑 (키워드: '{extract_keyword(kodex_name)}')"

        # ── Step C: LP 노이즈 감지 (비교군도 함께 확인 → 장세 전환 오탐 방지) ──
        first_comp_data, first_comp_baseline = None, None
        if comp_defs:
            fc = comp_defs[0]
            first_comp_data = self.loader.get_etf_row(current_df, fc["code"], fc["name"])
            if first_comp_data:
                first_comp_baseline = self._compute_baseline(fc["code"], fc["name"], history)
        lp = self._detect_lp(current_kodex, baseline, first_comp_data, first_comp_baseline)
        log.append(f"[LP 감지] {lp.note}")

        # ── Step D: Kodex 변화율 ──
        kodex_chg = self._change_rate(current_kodex, baseline, lp)
        metric_label = "금융투자" if lp.use_metric == "financial" else "개인"
        cur_val = current_kodex.financial_investment if lp.use_metric == "financial" else current_kodex.individual
        base_val = baseline.fi_avg if lp.use_metric == "financial" else baseline.ind_avg
        mabs_val = baseline.fi_mabs if lp.use_metric == "financial" else baseline.ind_mabs
        log.append(
            f"[KODEX 변화율] ({cur_val:,.0f} − {base_val:,.0f}) ÷ {mabs_val:,.0f} = {kodex_chg:+.4f}"
            f"  [{metric_label} 기준{'  ※추정값' if lp.is_estimate else ''}]"
        )

        # ── Step E: 비교군 변화율 ──
        force_metric = lp.use_metric if lp.use_metric in ("financial", "individual") else "individual"
        log.append(f"[비교군 매핑] {mapping_source} → {[c['name'] for c in comp_defs]}")
        log.append(f"[비교군 지표] KODEX LP={lp.suspicious} → 비교군도 동일하게 '{force_metric}' 사용")

        competitor_results: List[CompetitorResult] = []
        for comp in comp_defs:
            ccode, cname, cprov = comp["code"], comp["name"], comp["provider"]
            cdata = self.loader.get_etf_row(current_df, ccode, cname)
            if cdata is None:
                notes.append(f"{cname}({ccode}) 현재 주 데이터 없음")
                log.append(f"  · {cname}: 데이터 없음 — 제외")
                continue
            cdata.week_label = week_label
            cb = self._compute_baseline(ccode, cname, history)
            cchg = self._change_rate_by_metric(cdata, cb, force_metric)

            c_cur = cdata.financial_investment if force_metric == "financial" else cdata.individual
            c_base = cb.fi_avg if force_metric == "financial" else cb.ind_avg
            c_mabs = cb.fi_mabs if force_metric == "financial" else cb.ind_mabs
            log.append(
                f"  · {cname}: ({c_cur:,.0f} − {c_base:,.0f}) ÷ {c_mabs:,.0f} = {cchg:+.4f}"
            )
            competitor_results.append(CompetitorResult(
                code=ccode, name=cname, provider=cprov,
                change_pct=cchg,
                current_fi=cdata.financial_investment, current_ind=cdata.individual,
                baseline_fi_avg=cb.fi_avg, baseline_ind_avg=cb.ind_avg,
                metric_used=force_metric,
                fi_mabs=cb.fi_mabs, ind_mabs=cb.ind_mabs,
                corr=comp.get("corr"),
            ))

        # 호환성: TIGER/ACE 별도 추출
        tiger_chg = next((c.change_pct for c in competitor_results if c.provider == "TIGER"), None)
        ace_chg   = next((c.change_pct for c in competitor_results if c.provider in ("ACE", "PLUS")), None)

        # ── Step F: DiD 계산 ──
        ctrl_vals = [c.change_pct for c in competitor_results]
        no_competitors = len(ctrl_vals) == 0

        if ctrl_vals:
            control_avg = sum(ctrl_vals) / len(ctrl_vals)
            did = round(kodex_chg - control_avg, 2)
            ctrl_str = " + ".join(f"{v:+.4f}" for v in ctrl_vals)
            log.append(
                f"[DiD 계산] KODEX({kodex_chg:+.4f}) - 비교군평균(({ctrl_str}) ÷ {len(ctrl_vals)}) "
                f"= {kodex_chg:+.4f} - {control_avg:+.4f} = {did:+.4f}"
            )
        else:
            notes.append(
                "⚠️ 비교군 없음 — DiD 측정 불가 | "
                "유사 상품(TIGER·ACE·PLUS 등)을 찾지 못했습니다. | "
                "아래 수치는 KODEX 단독 변화율이며 시장 전체 영향이 제거되지 않았습니다. 해석에 주의하세요."
            )
            control_avg = 0.0
            did = kodex_chg
            log.append(f"[DiD] 비교군 없음 → KODEX 절대 변화율 {did:+.4f} (DiD 아님, 해석 주의)")

        judgement, emoji = self._judge(did) if not no_competitors else ("비교군 없음 — DiD 불가", "⚫")
        log.append(f"[판정] {emoji} {judgement}  ({'DiD = ' + f'{did:+.4f}' if not no_competitors else '절대 변화율만 표시'})")

        # ── 기저효과 착시 경고 ──
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), "../.."))
        try:
            from did_history import check_base_effect
            base_warn = check_base_effect(kodex_code, did, week_label, channel_type="securities")
            if base_warn:
                notes.append(base_warn)
                log.append(f"[기저효과] {base_warn}")
        except Exception:
            pass

        return ETFDiDResult(
            kodex_code=kodex_code,
            kodex_name=kodex_name,
            current=current_kodex,
            baseline=baseline,
            lp=lp,
            kodex_change_pct=round(kodex_chg, 2),
            control_avg_pct=round(control_avg, 2),
            did_value=did,
            judgement=judgement,
            judgement_emoji=emoji,
            competitors=competitor_results,
            no_competitors=no_competitors,
            mapping_source=mapping_source,
            tiger_change_pct=round(tiger_chg, 2) if tiger_chg is not None else None,
            ace_change_pct=round(ace_chg,   2) if ace_chg   is not None else None,
            notes=notes,
            calculation_log=log,
        )

    def _compute_baseline(
        self, code: str, name: str, history: Dict[str, pd.DataFrame]
    ) -> Baseline:
        records = []
        for sheet_name, df in history.items():
            row = self.loader.get_etf_row(df, code, name)
            if row:
                records.append({"week": sheet_name, "fi": row.financial_investment, "ind": row.individual})

        # 직전 8주 이평선 (은행·증권·매스 통일)
        recent = records[-self.BASELINE_WEEKS:] if len(records) >= self.BASELINE_WEEKS else records

        if not recent:
            return Baseline(code, name, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 0, [])

        fi_vals  = [r["fi"]  for r in recent if not pd.isna(r["fi"])]  or [0.0]
        ind_vals = [r["ind"] for r in recent if not pd.isna(r["ind"])] or [0.0]

        return Baseline(
            code=code,
            name=name,
            fi_avg=float(np.mean(fi_vals)),
            ind_avg=float(np.mean(ind_vals)),
            fi_std=float(np.std(fi_vals, ddof=1)) if len(fi_vals) > 1 else abs(fi_vals[0]) * 0.1 + 1,
            ind_std=float(np.std(ind_vals, ddof=1)) if len(ind_vals) > 1 else abs(ind_vals[0]) * 0.1 + 1,
            fi_mabs=float(np.mean(np.abs(fi_vals))) + 1000000,   # 라플라스 α=100만 (천원 단위 데이터 기준, +로 항상 분모에 포함)
            ind_mabs=float(np.mean(np.abs(ind_vals))) + 1000000,
            weeks_used=len(recent),
            history=recent,
        )

    def _detect_lp(self, current: ETFWeekData, baseline: Baseline,
                   comp_current: ETFWeekData = None, comp_baseline: Baseline = None) -> LPResult:
        if baseline.fi_std == 0 or baseline.weeks_used == 0:
            return LPResult(
                code=current.code, suspicious=False, z_score=0.0,
                direction_mismatch=False, use_metric="financial",
                reliability="medium", note="베이스라인 부족 — LP 탐지 불가", is_estimate=False,
            )

        z = abs((current.financial_investment - baseline.fi_avg) / baseline.fi_std)

        # ── LP 의심 조건 ──────────────────────────────────────────────
        # [설계 의도] z > 2.0 AND 부호 반전 둘 다 해당할 때만 LP 의심
        # [한계] z > 2.0 임계값은 통계적 관례(95% 신뢰구간)이나 실증 검증 미완료
        # [한계] 부호 반전은 LP 정상 헤징에서도 발생 → 단독으로는 조건 부족해서 AND로 묶음
        z_suspicious = z > 2.0

        sign_flip = (
            baseline.fi_avg != 0
            and current.financial_investment != 0
            and np.sign(baseline.fi_avg) != np.sign(current.financial_investment)
        )

        suspicious = z_suspicious and sign_flip

        # 비교군(TIGER 등)도 같은 부호 반전이면 → LP 아닌 장세 전환으로 처리
        # [한계] 비교군 1개만 체크 / 금리인하·전쟁종료 등 장세 전환도 여기서 걸러지나
        #        비교군이 없거나 비교군도 특수한 경우 오탐 가능성 있음
        if suspicious and comp_current is not None and comp_baseline is not None:
            comp_sign_flip = (
                comp_baseline.fi_avg != 0
                and comp_current.financial_investment != 0
                and np.sign(comp_baseline.fi_avg) != np.sign(comp_current.financial_investment)
            )
            if comp_sign_flip:
                suspicious = False
                return LPResult(
                    code=current.code, suspicious=False, z_score=round(z, 2),
                    direction_mismatch=False, use_metric="financial",
                    reliability="high",
                    note=f"정상 처리 (z={z:.2f}, 부호 반전이나 비교군도 동일 패턴 → 장세 전환으로 판단, LP 아님)",
                    is_estimate=False,
                )

        # 금융투자 vs 개인 방향 불일치 체크
        fi_dir = np.sign(current.financial_investment)
        ind_dir = np.sign(current.individual)
        mismatch = (fi_dir != ind_dir) and (fi_dir != 0) and (ind_dir != 0)

        if suspicious and mismatch:
            reason = []
            if sign_flip:
                reason.append(f"부호 반전 (베이스라인 평균 {baseline.fi_avg/1e6:.1f}M → 이번주 {current.financial_investment/1e6:.1f}M)")
            if z_suspicious:
                reason.append(f"z={z:.2f}>2.0")
            return LPResult(
                code=current.code, suspicious=True, z_score=round(z, 2),
                direction_mismatch=True, use_metric="individual",
                reliability="low",
                note=("⚠️ LP 개입 의심 (" + ", ".join(reason) + ") + 금융투자↔개인 방향 불일치 "
                      f"— 개인 기준 사용, 신뢰도 낮음\n"
                      "※ 금리인하·지정학 이슈 등 장세 전반 전환 시 LP가 아닌 시장 전체 현상일 수 있음"),
                is_estimate=True,
            )
        elif suspicious:
            reason = []
            if sign_flip:
                reason.append(f"부호 반전 (베이스라인 {baseline.fi_avg/1e6:.1f}M → 이번주 {current.financial_investment/1e6:.1f}M)")
            if z_suspicious:
                reason.append(f"z={z:.2f}>2.0")
            return LPResult(
                code=current.code, suspicious=True, z_score=round(z, 2),
                direction_mismatch=False, use_metric="individual",
                reliability="medium",
                note="⚠️ LP 개입 의심 (" + ", ".join(reason) + ") — 개인 기준으로 전환 (추정값)\n"
                     "※ 단, 금리인하·지정학 이슈 등 장세 전반 전환 시 LP가 아닌 시장 전체 현상일 수 있음. 당일 시장 상황 병행 확인 권장",
                is_estimate=True,
            )
        else:
            return LPResult(
                code=current.code, suspicious=False, z_score=round(z, 2),
                direction_mismatch=False, use_metric="financial",
                reliability="high",
                note=f"정상 (z={z:.2f}) — 금융투자 기준 분석",
                is_estimate=False,
            )

    def _normalized_change(self, cur_val: float, avg_val: float, mabs_val: float) -> float:
        """
        정규화 절대 변화 = (현재 - 평균) / 평균절댓값
        - 분모가 평균절댓값이라 부호 반전·0 근처 폭발 없음
        - ETF 규모 차이도 자동 보정
        """
        if mabs_val == 0:
            return 0.0
        return round((cur_val - avg_val) / mabs_val, 4)

    def _change_rate_by_metric(self, current: ETFWeekData, baseline: Baseline, metric: str) -> float:
        """지표를 직접 지정해서 정규화 변화 계산 (비교군 공정비교용)."""
        if metric == "financial":
            return self._normalized_change(
                current.financial_investment, baseline.fi_avg, baseline.fi_mabs)
        else:
            return self._normalized_change(
                current.individual, baseline.ind_avg, baseline.ind_mabs)

    def _change_rate(self, current: ETFWeekData, baseline: Baseline, lp: LPResult) -> float:
        if lp.use_metric == "financial":
            return self._normalized_change(
                current.financial_investment, baseline.fi_avg, baseline.fi_mabs)
        else:  # individual (LP 감지 시 전환)
            return self._normalized_change(
                current.individual, baseline.ind_avg, baseline.ind_mabs)

    def _judge(self, did: float):
        # 1단계 raw DiD 임시 판정 (2단계 Z-score 전 fallback용)
        if did >= 1.0:   return "마케팅 효과 강함", "🟢"
        elif did >= 0.3: return "마케팅 효과 있음", "🟡"
        elif did >= -0.3: return "효과 불분명", "⚪"
        else:             return "유의미한 효과 확인 어려움", "🔴"


# ── LLM ETF 추출 ──────────────────────────────────────────────────────────────

def extract_target_etfs_with_llm(collection_results: Dict, anthropic_api_key: str = "") -> Dict:
    """
    수집된 마케팅 채널 텍스트에서 LLM으로 대상 ETF 코드 및 마케팅 활동 요약 추출.
    반환: {"marketing_detected": bool, "etf_codes": [...], "summary": str}
    """
    import anthropic as ant

    marketing_texts = []
    for result in collection_results.values():
        _ok = getattr(result, "success", None)
        if _ok is None:
            _ok = bool(getattr(result, "data", None))
        if not _ok or result.data is None:
            continue
        d = result.data
        label = f"[{result.channel_name}]"
        if "raw_text" in d:
            marketing_texts.append(f"{label}\n{d['raw_text'][:600]}")
        elif "videos" in d:
            lines = []
            for v in d["videos"][:5]:
                if v.get("is_etf_related"):
                    url = v.get("url", "")
                    lines.append(f"- {v['title']} {url}")
            if lines:
                marketing_texts.append(f"{label}\n" + "\n".join(lines))
        elif "posts" in d:
            lines = []
            for p in d["posts"][:5]:
                url = p.get("link", "")
                lines.append(f"- {p['title']} {url}")
            if lines:
                marketing_texts.append(f"{label}\n" + "\n".join(lines))
        elif "articles" in d:
            lines = []
            for a in d["articles"][:5]:
                url = a.get("link", "")
                lines.append(f"- {a['title']} {url}")
            marketing_texts.append(f"{label}\n" + "\n".join(lines))
        elif "events" in d and d["events"]:
            marketing_texts.append(f"{label}\n" + "\n".join(d["events"][:5]))
        elif "trends" in d:
            lines = [f"{kw}: 현재={v['current']}, 4주평균={v['avg_4w']}, 변화={v['change_pct']:+.1f}%"
                     for kw, v in d["trends"].items()]
            marketing_texts.append(f"{label}\n" + "\n".join(lines))

    if not marketing_texts:
        return {"marketing_detected": False, "etf_codes": [], "summary": "수집된 마케팅 텍스트 없음"}

    prompt = f"""다음은 삼성증권 마케팅 채널(유튜브, 블로그, 뉴스 등)에서 수집된 텍스트입니다.

{chr(10).join(marketing_texts)}

[분석 기준 — 반드시 준수]
- '삼성증권' 채널에서 직접 진행한 마케팅 활동만 감지합니다
- 타사 증권사(미래에셋, KB, 한국투자 등)가 KODEX ETF를 판매하는 내용은 제외
- 삼성자산운용의 자체 이벤트도 삼성증권 채널을 통한 경우에만 포함
- 단순 시세 정보, 리서치 보고서, 일반 뉴스 기사, ETF 교육/분석 콘텐츠는 마케팅 활동 아님
- 유튜브 영상의 경우: "ETF 전망 분석", "ETF란 무엇인가" 등 교육/분석은 제외. "지금 사면 혜택", "이벤트 신청", "수수료 무료" 등 매수 유도만 포함

감지 대상: 이벤트, 프로모션, 수수료 혜택, 매수 유도 CTA, 특정 ETF 직접 추천 등
비감지 대상: 시황 분석, ETF 교육, 종목 분석, 단순 ETF 언급

마케팅 활동이 없거나 삼성증권 채널과 무관하면 marketing_detected: false, etf_codes: []

JSON만 출력:
{{
  "marketing_detected": true,
  "etf_codes": ["069500"],
  "summary": "감지된 삼성증권 채널 마케팅 활동 요약 (2-3문장)",
  "evidence": [
    {{
      "channel": "채널명 (예: 유튜브, 블로그, 뉴스)",
      "title": "해당 콘텐츠 제목",
      "url": "링크 (있으면)",
      "reason": "이 콘텐츠가 마케팅 활동으로 판단된 이유 (1문장)",
      "marketing_type": "이벤트|프로모션|추천콘텐츠|수수료혜택|기타 중 하나",
      "marketing_reason": "단순 키워드 노출이 아닌 마케팅 활동으로 분류한 구체적 근거 (이벤트 기간 명시·혜택 내용·매수 유도 문구 등)",
      "etf_codes": ["069500"]
    }}
  ]
}}"""

    try:
        from llm_client import call_llm
        gem_key = __import__("os").getenv("GEMINI_API_KEY", "")
        text = call_llm(prompt, anthropic_key=anthropic_api_key, gemini_key=gem_key, max_tokens=2000)
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
        logger.warning(f"LLM ETF 추출 실패: {e}")

    return {"marketing_detected": False, "etf_codes": [], "summary": "LLM 분석 실패"}
