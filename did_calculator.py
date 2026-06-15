"""
DiD 공용 계산 모듈
증권사(금융투자) + 매스(개인) 채널이 공유하는 코어 로직.
은행(bank)은 8주 + 2단계 Z-score로 방법론이 달라 agents/bank/analyzer.py 에서 별도 관리.

사용법:
    from did_calculator import MarketingAnalyzerBase, ExcelLoader, ETFDiDResult

    class SecuritiesAnalyzer(MarketingAnalyzerBase):
        TARGET_COLUMN = "financial"   # 금융투자
        BASELINE_WEEKS = 4
        USE_LP_DETECTION = True

    class MassAnalyzer(MarketingAnalyzerBase):
        TARGET_COLUMN = "individual"  # 개인
        BASELINE_WEEKS = 4
        USE_LP_DETECTION = False      # 개인 컬럼은 LP 노이즈 없음
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── 비교 ETF 운용사 프리픽스 ─────────────────────────────────────────────────
COMPETITOR_PREFIXES = ["TIGER", "ACE", "PLUS", "KINDEX", "SOL", "HANARO", "KB", "BNK", "iM"]

_STRIP_WORDS = ["KODEX", "액티브", "(합성)", "(H)", "TR", "Plus", "PLUS"]
_VARIANT_TAGS = ["레버리지", "인버스", "2X", "선물", "커버드콜", "타겟", "위클리",
                 "바이오테크", "산업재", "헬스케어", "IT", "금융", "에너지", "소비재"]


def extract_keyword(etf_name: str) -> str:
    name = etf_name
    for w in _STRIP_WORDS:
        name = name.replace(w, "")
    return re.sub(r"\s+", " ", name).strip()


def _variant_tags_in(name: str) -> set:
    return {tag for tag in _VARIANT_TAGS if tag in name}


_index_cache: Dict[str, str] = {}

def get_tracking_index(code: str) -> str:
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


def auto_map_competitors(kodex_name: str, kodex_code: str,
                         etf_universe: pd.DataFrame) -> List[Dict]:
    keyword = extract_keyword(kodex_name)
    if not keyword:
        return []
    kodex_variants = _variant_tags_in(kodex_name)
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
            ccode = str(row["종목코드"]).split("*")[0].strip()
            if _variant_tags_in(cname) != kodex_variants:
                continue
            name_diff = abs(len(cname) - len(kodex_name))
            base_score = len(keyword) * 10 - name_diff
            index_bonus = 0
            if kodex_index:
                cand_index = get_tracking_index(ccode)
                if cand_index and kodex_index == cand_index:
                    index_bonus = 50
                elif cand_index and (kodex_index in cand_index or cand_index in kodex_index):
                    index_bonus = 30
            results.append({"code": ccode, "name": cname, "provider": prefix,
                             "match_score": base_score + index_bonus,
                             "tracking_index": get_tracking_index(ccode) or "미확인",
                             "index_matched": index_bonus > 0})
    priority = {"TIGER": 0, "ACE": 1, "PLUS": 1, "KINDEX": 2, "SOL": 2, "HANARO": 3}
    by_provider: Dict[str, dict] = {}
    for r in sorted(results, key=lambda x: -x["match_score"]):
        p = r["provider"]
        if p not in by_provider:
            by_provider[p] = r
    sorted_providers = sorted(by_provider.keys(), key=lambda p: priority.get(p, 3))
    return [by_provider[p] for p in sorted_providers][:2]


# ── 데이터클래스 ──────────────────────────────────────────────────────────────

@dataclass
class ETFWeekData:
    code: str
    name: str
    financial_investment: float
    individual: float
    week_label: str = ""


@dataclass
class Baseline:
    code: str
    name: str
    fi_avg: float
    ind_avg: float
    fi_std: float
    ind_std: float
    fi_mabs: float
    ind_mabs: float
    weeks_used: int
    history: List[Dict] = field(default_factory=list)


@dataclass
class LPResult:
    code: str
    suspicious: bool
    z_score: float
    direction_mismatch: bool
    use_metric: str
    reliability: str
    note: str
    is_estimate: bool = False


@dataclass
class CompetitorResult:
    code: str
    name: str
    provider: str
    change_pct: float
    current_fi: float
    current_ind: float
    baseline_fi_avg: float
    baseline_ind_avg: float
    metric_used: str
    fi_mabs: float = 0.0    # 계산식 표시용
    ind_mabs: float = 0.0   # 계산식 표시용


@dataclass
class ETFDiDResult:
    kodex_code: str
    kodex_name: str
    current: ETFWeekData
    baseline: Baseline
    lp: LPResult
    kodex_change_pct: float
    control_avg_pct: float
    did_value: float
    judgement: str
    judgement_emoji: str
    competitors: List[CompetitorResult] = field(default_factory=list)
    mapping_source: str = ""
    tiger_change_pct: Optional[float] = None
    ace_change_pct: Optional[float] = None
    no_competitors: bool = False
    notes: List[str] = field(default_factory=list)
    calculation_log: List[str] = field(default_factory=list)


# ── Excel 로더 ────────────────────────────────────────────────────────────────

class ExcelLoader:
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
        df_raw = pd.read_excel(xl, sheet_name=sheet_name, header=None)
        header_row = None
        for i, row in df_raw.iterrows():
            row_str = " ".join(str(v) for v in row.values)
            if re.search(r"금융투자|단축코드|종목코드|종목명", row_str):
                header_row = i
                break
        if header_row is None:
            return None
        cols = [str(v).strip() for v in df_raw.iloc[header_row].values]
        data = df_raw.iloc[header_row + 1:].copy()
        data.columns = cols
        data = data.dropna(how="all").reset_index(drop=True)
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
        for col in ["금융투자", "개인"]:
            if col in data.columns:
                data[col] = (data[col].astype(str)
                             .str.replace(",", "", regex=False)
                             .str.replace("(", "-", regex=False)
                             .str.replace(")", "", regex=False)
                             .str.strip())
                data[col] = pd.to_numeric(data[col], errors="coerce")
        if "종목코드" in data.columns:
            data["종목코드"] = (data["종목코드"].astype(str).str.strip()
                              .str.split("*").str[0].str.zfill(6))
        return data

    def get_etf_row(self, df: pd.DataFrame, code: str, name: str) -> Optional[ETFWeekData]:
        if df is None or df.empty:
            return None
        # KRX 캐시는 '단축코드', 엑셀은 '종목코드' — 둘 다 처리
        _code_col = "단축코드" if "단축코드" in df.columns else "종목코드" if "종목코드" in df.columns else None
        if _code_col:
            bare_code = code.split("*")[0].strip().zfill(6)
            mask = df[_code_col].astype(str).str.split("*").str[0].str.strip().str.zfill(6) == bare_code
            if mask.any():
                return self._row_to_etf(df[mask].iloc[0], code, name)
        if "종목명" in df.columns:
            keyword = re.sub(r"(KODEX|TIGER|ACE|KINDEX|SOL)\s*", "", name).strip()
            mask = df["종목명"].astype(str).str.contains(keyword, na=False, regex=False)
            if mask.any():
                return self._row_to_etf(df[mask].iloc[0], code, name)
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
        return ETFWeekData(code=code,
                           name=str(row.get("종목명", name)),
                           financial_investment=_safe(row.get("금융투자", 0)),
                           individual=_safe(row.get("개인", 0)))


# ── 공용 분석 베이스 클래스 ───────────────────────────────────────────────────

class MarketingAnalyzerBase:
    """
    증권사(TARGET_COLUMN="financial") + 매스(TARGET_COLUMN="individual") 공통 베이스.
    서브클래스에서 TARGET_COLUMN, BASELINE_WEEKS, USE_LP_DETECTION 오버라이드.
    """
    TARGET_COLUMN: str = "financial"   # "financial" | "individual"
    BASELINE_WEEKS: int = 4
    USE_LP_DETECTION: bool = True      # 개인 컬럼은 LP 없으므로 False
    CHANNEL_TYPE: str = "securities"   # did_history 저장용

    def __init__(self):
        self.loader = ExcelLoader()

    def load_excel(self, file_obj) -> Dict[str, pd.DataFrame]:
        return self.loader.load(file_obj)

    # ── 순수 계산 (공용) ──────────────────────────────────────────────────────

    def _normalized_change(self, cur_val: float, avg_val: float, mabs_val: float) -> float:
        """(현재 - 평균) / |평균| — 부호반전 폭발 방지, ETF 규모 보정"""
        if mabs_val == 0:
            return 0.0
        return round((cur_val - avg_val) / mabs_val, 4)

    def _judge(self, did: float):
        if did >= 1.0:   return "마케팅 효과 강함", "🟢"
        elif did >= 0.3: return "마케팅 효과 있음", "🟡"
        elif did >= -0.3: return "효과 불분명", "⚪"
        else:             return "유의미한 효과 확인 어려움", "🔴"

    def _compute_baseline(self, code: str, name: str,
                          history: Dict[str, pd.DataFrame]) -> Baseline:
        records = []
        for sheet_name, df in history.items():
            row = self.loader.get_etf_row(df, code, name)
            if row:
                records.append({"week": sheet_name,
                                 "fi": row.financial_investment,
                                 "ind": row.individual})
        recent = records[-self.BASELINE_WEEKS:] if len(records) >= self.BASELINE_WEEKS else records
        if not recent:
            return Baseline(code, name, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 0, [])
        fi_vals  = [r["fi"]  for r in recent if not pd.isna(r["fi"])]  or [0.0]
        ind_vals = [r["ind"] for r in recent if not pd.isna(r["ind"])] or [0.0]
        return Baseline(
            code=code, name=name,
            fi_avg=float(np.mean(fi_vals)),
            ind_avg=float(np.mean(ind_vals)),
            fi_std=float(np.std(fi_vals, ddof=1)) if len(fi_vals) > 1 else abs(fi_vals[0]) * 0.1 + 1,
            ind_std=float(np.std(ind_vals, ddof=1)) if len(ind_vals) > 1 else abs(ind_vals[0]) * 0.1 + 1,
            # 라플라스 α=100만 (천원 단위 데이터 기준 — 원 단위였던 10억에서 ÷1000 조정)
            fi_mabs=float(np.mean(np.abs(fi_vals))) + 1_000_000,
            ind_mabs=float(np.mean(np.abs(ind_vals))) + 1_000_000,
            weeks_used=len(recent),
            history=recent,
        )

    def _get_lp_result_noop(self, code: str) -> LPResult:
        """LP 감지 불필요한 채널(개인 컬럼) 용 — 항상 개인 기준 반환."""
        return LPResult(code=code, suspicious=False, z_score=0.0,
                        direction_mismatch=False, use_metric="individual",
                        reliability="high", note="개인 컬럼 — LP 감지 불필요", is_estimate=False)

    def _change_rate_by_metric(self, current: ETFWeekData, baseline: Baseline,
                               metric: str) -> float:
        if metric == "financial":
            return self._normalized_change(current.financial_investment, baseline.fi_avg, baseline.fi_mabs)
        return self._normalized_change(current.individual, baseline.ind_avg, baseline.ind_mabs)

    def _change_rate(self, current: ETFWeekData, baseline: Baseline,
                     lp: LPResult) -> float:
        metric = "individual" if self.TARGET_COLUMN == "individual" else lp.use_metric
        return self._change_rate_by_metric(current, baseline, metric)


# ── LLM ETF 추출 (공용) ──────────────────────────────────────────────────────

def extract_target_etfs_with_llm(collection_results: Dict,
                                  anthropic_api_key: str = "",
                                  channel_context: str = "삼성증권") -> Dict:
    """채널 컨텍스트만 파라미터로 받아 3채널 공용으로 사용."""
    marketing_texts = []
    collected_image_urls = []
    for result in collection_results.values():
        if not result.success or not result.data:
            continue
        d = result.data
        label = f"[{result.channel_name}]"
        if "raw_text" in d:
            marketing_texts.append(f"{label}\n{d['raw_text'][:600]}")
        elif "videos" in d:
            lines = [f"- {v['title']} {v.get('url','')}"
                     for v in d["videos"][:5] if v.get("is_etf_related")]
            if lines: marketing_texts.append(f"{label}\n" + "\n".join(lines))
        elif "posts" in d:
            lines = [f"- {p['title']} {p.get('link','')}" for p in d["posts"][:5]]
            if lines: marketing_texts.append(f"{label}\n" + "\n".join(lines))
        elif "articles" in d:
            lines = [f"- {a['title']} {a.get('link','')}" for a in d["articles"][:5]]
            marketing_texts.append(f"{label}\n" + "\n".join(lines))
        elif "events" in d and d["events"]:
            marketing_texts.append(f"{label}\n" + "\n".join(d["events"][:5]))
        for ev in d.get("event_details", []):
            img = ev.get("image_url", "")
            if img and img.startswith("http"):
                collected_image_urls.append(img)

    if not marketing_texts:
        return {"marketing_detected": False, "etf_codes": [], "summary": "수집된 마케팅 텍스트 없음"}

    prompt = f"""다음은 {channel_context} 마케팅 채널에서 수집된 텍스트입니다.

{chr(10).join(marketing_texts)}

[분석 기준]
- {channel_context} 채널에서 직접 진행한 마케팅 활동만 감지
- 단순 시세·교육·분석 콘텐츠는 제외. 이벤트·프로모션·수수료혜택·매수유도만 포함

JSON만 출력:
{{
  "marketing_detected": true,
  "etf_codes": ["069500"],
  "summary": "감지된 마케팅 활동 요약 (2-3문장)",
  "evidence": [{{
    "channel": "채널명",
    "title": "콘텐츠 제목",
    "url": "링크",
    "event_period": "YYYY-MM-DD ~ YYYY-MM-DD 또는 기간 설명 (없으면 null)",
    "reason": "마케팅으로 판단한 이유 (1문장)",
    "marketing_type": "이벤트|프로모션|추천콘텐츠|수수료혜택|기타",
    "etf_codes": ["069500"]
  }}]
}}"""

    try:
        from llm_client import call_llm, call_llm_with_images
        gem_key = __import__("os").getenv("GEMINI_API_KEY", "")
        if collected_image_urls:
            img_note = f"\n\n[첨부 이미지 {len(collected_image_urls)}개: 이벤트 배너. 이미지에서 이벤트 기간·대상 ETF·혜택 조건을 추출해주세요.]"
            text = call_llm_with_images(
                prompt + img_note,
                collected_image_urls,
                anthropic_key=anthropic_api_key,
                gemini_key=gem_key,
                max_tokens=800,
            )
        else:
            text = call_llm(prompt, anthropic_key=anthropic_api_key, gemini_key=gem_key, max_tokens=512)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        logger.warning(f"LLM ETF 추출 실패: {e}")

    return {"marketing_detected": False, "etf_codes": [], "summary": "LLM 분석 실패"}
