"""
ETF 비교군 매핑 공용 로더
- 루트의 etf_mapping.json 한 번 로드 후 캐시
- 3개 채널(증권사/은행/대고객) 공통 사용
- 코드 형식: "069500" 또는 "069500*001" 모두 허용
"""
import json
import os
from typing import List, Dict, Optional

_MAPPING: Optional[Dict] = None  # 모듈 레벨 캐시 (프로세스 당 1회 로드)
_MAPPING_PATH = os.path.join(os.path.dirname(__file__), "etf_mapping.json")


def _load() -> Dict:
    global _MAPPING
    if _MAPPING is None:
        try:
            with open(_MAPPING_PATH, encoding="utf-8") as f:
                _MAPPING = json.load(f)
        except Exception:
            _MAPPING = {}
    return _MAPPING


def _normalize(code: str) -> str:
    """069500*001 → 069500"""
    return str(code).split("*")[0].strip()


def get_competitors(kodex_code: str) -> List[Dict]:
    """
    KODEX ETF 코드로 비교군 목록 반환.
    반환값: [{"code": "102110", "name": "TIGER 200", "provider": "TIGER", ...}, ...]
    코드는 *001 suffix 제거된 순수 6자리.
    """
    mapping = _load()
    bare = _normalize(kodex_code)

    # 키 형식이 "069500*001" 또는 "069500" 모두 대응
    entry = mapping.get(f"{bare}*001") or mapping.get(bare)
    if not entry:
        return []

    comps = entry.get("competitors", [])
    # 코드 정규화 (저장 시 *001 포함될 수 있으므로)
    return [
        {**c, "code": _normalize(c["code"])}
        for c in comps
    ]


def is_loaded() -> bool:
    """매핑 파일 로드 여부"""
    return bool(_load())


def reload():
    """매핑 파일 강제 재로드 (파일 업데이트 후 반영 시)"""
    global _MAPPING
    _MAPPING = None
    return _load()
