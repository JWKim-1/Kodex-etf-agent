"""
analyzer.py — 하위 호환 재수출 모듈
모든 실제 구현은 agents/securities/analyzer.py에 있음.
이 파일을 import하면 securities 버전이 자동으로 사용됨.
"""
from agents.securities.analyzer import (
    MarketingAnalyzer,
    ExcelLoader,
    ETFDiDResult,
    LPResult,
    CompetitorResult,
    COMPARISON_MAP,
    ALL_KNOWN_CODES,
    auto_map_competitors,
    extract_keyword,
)
from did_calculator import extract_target_etfs_with_llm

__all__ = [
    "MarketingAnalyzer", "ExcelLoader", "ETFDiDResult", "LPResult",
    "CompetitorResult", "COMPARISON_MAP", "ALL_KNOWN_CODES",
    "auto_map_competitors", "extract_keyword", "extract_target_etfs_with_llm",
]
