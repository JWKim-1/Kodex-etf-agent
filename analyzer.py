"""
DiD Analyzer for Samsung Securities ETF Marketing Effect Measurement
이중차분법(DiD) 분석 모듈
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── 비교 ETF 운용사 프리픽스 (우선순위 순) ────────────────────────────────────
COMPETITOR_PREFIXES = ["TIGER", "ACE", "PLUS", "KINDEX", "SOL", "HANARO", "KB", "BNK", "iM"]

# ── KODEX 이름에서 핵심 키워드 추출 시 제거할 단어 ────────────────────────────
_STRIP_WORDS = ["KODEX", "액티브", "(합성)", "(H)", "TR", "Plus", "PLUS"]

# 변형 태그 (레버리지·인버스 등): 원본에 없으면 비교군에서도 제외
_VARIANT_TAGS = ["레버리지", "인버스", "2X", "선물", "커버드콜", "타겟", "위클리",
                 "바이오테크", "산업재", "헬스케어", "IT", "금융", "에너지", "소비재"]


def extract_keyword(etf_name: str) -> str:
    """KODEX ETF 이름에서 핵심 테마 키워드 추출."""
    name = etf_name
    for w in _STRIP_WORDS:
        name = name.replace(w, "")
    return re.sub(r"\s+", " ", name).strip()


def _variant_tags_in(name: str) -> set:
    return {tag for tag in _VARIANT_TAGS if tag in name}


_index_cache: Dict[str, str] = {}  # 기초지수 캐시 (ETF코드 → 지수명)

def get_tracking_index(code: str) -> str:
    """네이버 금융에서 ETF 기초지수 조회 (캐시 사용)."""
    import requests as _req
    from bs4 import BeautifulSoup as _BS

    if code in _index_cache:
        return _index_cache[code]

    try:
        h = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com"}
        r = _req.get(f"https://finance.naver.com/item/main.naver?code={code}", headers=h, timeout=8)
        soup = _BS(r.text, "lxml")
        for th in soup.find_all("th", string=re.compile("기초지수")):
            td = th.find_next_sibling("td")
            if td:
                val = td.get_text(strip=True)
                if val and not re.match(r"^[\d,.\s]+$", val) and len(val) > 2:
                    _index_cache[code] = val
                    return val
    except Exception:
        pass

    _index_cache[code] = ""
    return ""


def auto_map_competitors(
    kodex_name: str,
    kodex_code: str,
    etf_universe: pd.DataFrame,
) -> List[Dict]:
    # [설계 의도] ETF 이름 키워드 + 기초지수(네이버 금융) 동일 여부로 비교군 탐색
    # 기초지수 조회 실패 시 이름 유사도만으로 fallback
    """
    엑셀 전체 ETF에서 비교군 자동 탐색.
    - 핵심 키워드 일치
    - 레버리지·인버스 등 변형 태그 원본과 동일해야 매칭
    - 이름 길이 차이 최소 = 가장 유사한 ETF 우선
    """
    keyword = extract_keyword(kodex_name)
    if not keyword:
        return []

    # 원본 KODEX의 변형 태그 추출
    kodex_variants = _variant_tags_in(kodex_name)

    # 기초지수 조회 (네이버 금융) — 캐시 활용
    kodex_index = get_tracking_index(kodex_code)

    results = []
    for prefix in COMPETITOR_PREFIXES:
        mask = (
            etf_universe["종목명"].astype(str).str.startswith(prefix)
            & etf_universe["종목명"].astype(str).str.contains(keyword, regex=False, na=False)
            & (etf_universe["종목코드"].astype(str) != kodex_code)
        )
        for _, row in etf_universe[mask].iterrows():
            cname = str(row["종목명"])
            ccode = str(row["종목코드"])
            cand_variants = _variant_tags_in(cname)
            if cand_variants != kodex_variants:
                continue

            name_diff = abs(len(cname) - len(kodex_name))
            base_score = len(keyword) * 10 - name_diff

            # 기초지수 일치 시 보너스 점수 (+50)
            index_bonus = 0
            if kodex_index:
                cand_index = get_tracking_index(ccode)
                if cand_index and kodex_index == cand_index:
                    index_bonus = 50
                elif cand_index and (kodex_index in cand_index or cand_index in kodex_index):
                    index_bonus = 30  # 부분 일치

            results.append({
                "code": ccode,
                "name": cname,
                "provider": prefix,
                "match_score": base_score + index_bonus,
                "tracking_index": get_tracking_index(ccode) or "미확인",
                "index_matched": index_bonus > 0,
            })

    # 운용사별로 최고 매칭 1개씩 선택 후 우선순위 적용
    # 목표: TIGER 1개 + ACE/PLUS/SOL 1개 조합 (최대 2개)
    priority = {"TIGER": 0, "ACE": 1, "PLUS": 1, "KINDEX": 2, "SOL": 2, "HANARO": 3}

    # 운용사별 최고 매칭 선택
    by_provider: Dict[str, dict] = {}
    for r in sorted(results, key=lambda x: -x["match_score"]):
        p = r["provider"]
        if p not in by_provider:
            by_provider[p] = r

    # 우선순위 순으로 최대 2개, 단 같은 운용사 중복 없이
    sorted_providers = sorted(by_provider.keys(), key=lambda p: priority.get(p, 3))
    unique = [by_provider[p] for p in sorted_providers]

    return unique[:2]  # 최대 2개 (DiD = KODEX - 비교군평균÷2)


# ── 비교군 매핑 테이블 (하드코딩 폴백) ───────────────────────────────────────
COMPARISON_MAP: Dict[str, Dict] = {
    "069500": {
        "name": "KODEX 200",
        "competitors": [
            {"code": "102110", "name": "TIGER 200", "provider": "TIGER"},
            {"code": "152100", "name": "PLUS 200", "provider": "ACE"},  # ACE→PLUS 리브랜딩
        ],
    },
    "229200": {
        "name": "KODEX 코스닥150",
        "competitors": [
            {"code": "232080", "name": "TIGER 코스닥150", "provider": "TIGER"},
        ],
    },
    "091160": {
        "name": "KODEX 반도체",
        "competitors": [
            {"code": "091230", "name": "TIGER 반도체", "provider": "TIGER"},
        ],
    },
    "498400": {
        "name": "KODEX 200타겟위클리커버드콜",
        "competitors": [
            {"code": "0104N0", "name": "TIGER 200타겟위클리커버드콜", "provider": "TIGER"},
        ],
    },
}

ALL_KNOWN_CODES = set(COMPARISON_MAP.keys()) | {
    c["code"] for v in COMPARISON_MAP.values() for c in v["competitors"]
}


# ── 데이터 클래스 ─────────────────────────────────────────────────────────────

@dataclass
class ETFWeekData:
    code: str
    name: str
    financial_investment: float  # 금융투자 순매수
    individual: float            # 개인 순매수
    week_label: str = ""


@dataclass
class Baseline:
    code: str
    name: str
    fi_avg: float      # 금융투자 4주 평균
    ind_avg: float     # 개인 4주 평균
    fi_std: float      # 금융투자 표준편차 (LP 감지용)
    ind_std: float     # 개인 표준편차
    fi_mabs: float     # 금융투자 평균절댓값 (정규화 분모)
    ind_mabs: float    # 개인 평균절댓값 (정규화 분모)
    weeks_used: int
    history: List[Dict] = field(default_factory=list)


@dataclass
class LPResult:
    code: str
    suspicious: bool
    z_score: float
    direction_mismatch: bool
    use_metric: str          # "financial" | "individual" | "average" | "both"
    reliability: str         # "high" | "medium" | "low"
    note: str
    is_estimate: bool = False


@dataclass
class CompetitorResult:
    """비교군 ETF 개별 결과."""
    code: str
    name: str
    provider: str
    change_pct: float
    current_fi: float
    current_ind: float
    baseline_fi_avg: float
    baseline_ind_avg: float
    metric_used: str   # "financial" or "individual"


@dataclass
class ETFDiDResult:
    kodex_code: str
    kodex_name: str
    current: ETFWeekData
    baseline: Baseline
    lp: LPResult
    # 변화율
    kodex_change_pct: float
    control_avg_pct: float
    did_value: float
    judgement: str
    judgement_emoji: str
    # 비교군 상세
    competitors: List[CompetitorResult] = field(default_factory=list)
    mapping_source: str = ""
    # 호환성 유지
    tiger_change_pct: Optional[float] = None
    ace_change_pct: Optional[float] = None
    no_competitors: bool = False
    notes: List[str] = field(default_factory=list)
    calculation_log: List[str] = field(default_factory=list)


# ── Excel 로더 ────────────────────────────────────────────────────────────────

class ExcelLoader:
    """
    멀티 시트 엑셀 → {시트명: DataFrame} 변환
    컬럼명을 최대한 유연하게 인식
    """

    # 실제 엑셀 컬럼 → 내부 표준명 매핑
    COL_MAP = {
        "종목코드": ["단축코드", "종목코드", "code", "Code", "ticker", "ETF코드", "종목 코드"],
        "종목명":   ["종목명", "name", "Name", "ETF명", "종목", "펀드명", "상품명"],
        "금융투자": ["금융투자", "금융투자합", "금투", "증권", "금융투자(순매수)"],
        "개인":     ["개인", "개인합", "개인투자자", "개인(순매수)"],
    }

    def load(self, file_obj) -> Dict[str, pd.DataFrame]:
        xl = pd.ExcelFile(file_obj)
        result = {}
        for sheet in xl.sheet_names:
            df = self._load_sheet(xl, sheet)
            if df is not None and not df.empty:
                result[sheet] = df
        return result

    def _load_sheet(self, xl, sheet_name: str) -> Optional[pd.DataFrame]:
        """단일 시트를 읽어 표준 컬럼으로 변환."""
        # 먼저 header=0으로 시도 (일반적 구조)
        df_raw = pd.read_excel(xl, sheet_name=sheet_name, header=None)

        # 헤더 행 탐지: '금융투자' 또는 '단축코드'가 있는 첫 번째 행
        header_row = None
        for i, row in df_raw.iterrows():
            row_str = " ".join(str(v) for v in row.values)
            if re.search(r"금융투자|단축코드|종목코드|종목명", row_str):
                header_row = i
                break

        if header_row is None:
            return None

        # 헤더 이후 데이터
        cols = [str(v).strip() for v in df_raw.iloc[header_row].values]
        data = df_raw.iloc[header_row + 1:].copy()
        data.columns = cols
        data = data.dropna(how="all").reset_index(drop=True)

        # 컬럼명 정규화
        rename = {}
        used_src = set()
        for target, aliases in self.COL_MAP.items():
            for col in data.columns:
                if col in used_src:
                    continue
                if col in aliases or any(a in col for a in aliases):
                    rename[col] = target
                    used_src.add(col)
                    break

        data = data.rename(columns=rename)

        # 숫자 변환
        for col in ["금융투자", "개인"]:
            if col in data.columns:
                data[col] = (
                    data[col].astype(str)
                    .str.replace(",", "", regex=False)
                    .str.replace("(", "-", regex=False)
                    .str.replace(")", "", regex=False)
                    .str.strip()
                )
                data[col] = pd.to_numeric(data[col], errors="coerce")

        # 종목코드 정규화: '069500*001' → '069500'
        if "종목코드" in data.columns:
            data["종목코드"] = (
                data["종목코드"].astype(str).str.strip()
                .str.split("*").str[0]   # *001 제거
                .str.zfill(6)
            )

        return data

    def get_etf_row(self, df: pd.DataFrame, code: str, name: str) -> Optional[ETFWeekData]:
        if df is None or df.empty:
            return None

        # 코드로 검색
        if "종목코드" in df.columns:
            mask = df["종목코드"] == code.zfill(6)
            if mask.any():
                return self._row_to_etf(df[mask].iloc[0], code, name)

        # 종목명으로 검색 (부분 일치)
        if "종목명" in df.columns:
            keyword = re.sub(r"(KODEX|TIGER|ACE|KINDEX|SOL)\s*", "", name).strip()
            mask = df["종목명"].astype(str).str.contains(keyword, na=False, regex=False)
            if mask.any():
                return self._row_to_etf(df[mask].iloc[0], code, name)

        # 전체 텍스트 검색 (중복 컬럼 안전 처리)
        seen_cols = set()
        for col in df.select_dtypes(include="object").columns:
            if col in seen_cols:
                continue
            seen_cols.add(col)
            col_series = df[col]
            if isinstance(col_series, pd.DataFrame):
                col_series = col_series.iloc[:, 0]
            mask = col_series.astype(str).str.contains(code, na=False)
            if mask.any():
                return self._row_to_etf(df[mask].iloc[0], code, name)

        return None

    def _row_to_etf(self, row: pd.Series, code: str, name: str) -> ETFWeekData:
        def _safe(v):
            try:
                f = float(v)
                return 0.0 if pd.isna(f) else f
            except (TypeError, ValueError):
                return 0.0
        fi = _safe(row.get("금융투자", 0))
        ind = _safe(row.get("개인", 0))
        nm = str(row.get("종목명", name))
        return ETFWeekData(code=code, name=nm, financial_investment=fi, individual=ind)


# ── 분석 엔진 ─────────────────────────────────────────────────────────────────

class MarketingAnalyzer:
    def __init__(self):
        self.loader = ExcelLoader()

    def load_excel(self, file_obj) -> Dict[str, pd.DataFrame]:
        return self.loader.load(file_obj)

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
                results[code] = result

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

        # ── Step B-1: 비교군 정의 (LP 감지 전에 필요) ──
        if kodex_code in COMPARISON_MAP:
            comp_defs = COMPARISON_MAP[kodex_code]["competitors"]
            mapping_source = "하드코딩 매핑"
        else:
            comp_defs = auto_map_competitors(kodex_name, kodex_code, etf_universe)
            mapping_source = f"자동 매핑 (키워드: '{extract_keyword(kodex_name)}')"

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
        log.append(
            f"[KODEX 변화율] ({cur_val:,.0f} ÷ {base_val:,.0f} - 1) × 100 = {kodex_chg:+.1f}%"
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
            log.append(
                f"  · {cname}: ({c_cur:,.0f} ÷ {c_base:,.0f} - 1) × 100 = {cchg:+.1f}%"
            )
            competitor_results.append(CompetitorResult(
                code=ccode, name=cname, provider=cprov,
                change_pct=cchg,
                current_fi=cdata.financial_investment, current_ind=cdata.individual,
                baseline_fi_avg=cb.fi_avg, baseline_ind_avg=cb.ind_avg,
                metric_used=force_metric,
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

        # 직전 8주 이평선 (2개월 기준선)
        # [설계 의도] 8주 = ETF 이벤트 잔존 효과 충분히 소멸 + 시장 환경 크게 안 변함
        # 4주: 직전 이벤트 오염 가능 / 20주: 시장 환경 변화 큼 → 8주가 적정
        recent = records[-8:] if len(records) >= 8 else records

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
            # 라플라스 스무딩(α): 소형 ETF 분모 폭발 방지
            # α = 10억원(10M) — 시장 주간 평균 거래 규모의 최소 단위
            _ALPHA = 10_000_000
            fi_mabs=max(float(np.mean(np.abs(fi_vals))), _ALPHA),
            ind_mabs=max(float(np.mean(np.abs(ind_vals))), _ALPHA),
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
        # 단위: 정규화 절대 변화값 (= 평소 변동 크기 대비 초과분)
        # [설계 의도] 1.0 = 평소 변동 크기만큼 초과, 0.3 = 30% 초과
        # [한계] 임계값(1.0/0.3/-0.3)은 이론적 설정 — 실증 데이터 기반 보정 필요
        if did >= 1.0:
            return "마케팅 효과 강함", "🟢"
        elif did >= 0.3:
            return "마케팅 효과 있음", "🟡"
        elif did >= -0.3:
            return "효과 불분명", "⚪"
        else:
            return "유의미한 효과 확인 어려움", "🔴"


# ── LLM ETF 추출 ──────────────────────────────────────────────────────────────

def extract_target_etfs_with_llm(collection_results: Dict, anthropic_api_key: str = "") -> Dict:
    """
    수집된 마케팅 채널 텍스트에서 LLM으로 대상 ETF 코드 및 마케팅 활동 요약 추출.
    반환: {"marketing_detected": bool, "etf_codes": [...], "summary": str}
    """
    import anthropic as ant

    marketing_texts = []
    for result in collection_results.values():
        if not result.success or not result.data:
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
- 단순 시세 정보, 리서치 보고서, 일반 뉴스 기사는 마케팅 활동 아님

감지 대상: 이벤트, 프로모션, 수수료 혜택, 추천/기획 콘텐츠 등 삼성증권이 고객에게 KODEX ETF 매수를 유도하는 활동

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
        key = anthropic_api_key or __import__("os").getenv("ANTHROPIC_API_KEY", "")
        client = ant.Anthropic(api_key=key)
        msg = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        logger.warning(f"LLM ETF 추출 실패: {e}")

    return {"marketing_detected": False, "etf_codes": [], "summary": f"LLM 분석 실패: {e}"}
