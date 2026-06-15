"""
마케팅 채널 수집 결과 아카이브 모듈
- 문제: 뉴스 RSS/유튜브/블로그 등은 "현재 시점 기준 최근 글"만 반환 → 1주만 지나도
  그 주차에 감지했던 마케팅 활동(링크/내용)이 사라져서 재조회 불가
- 해결: 특정 주차에 채널 수집을 1번 돌리면 결과(성공여부/데이터/링크/리포트)를
  그대로 JSON에 보존 → 이후 같은 주차를 다시 선택하면 라이브 재수집 대신
  보존된 결과를 그대로 보여줌 (그 시점 데이터 영구 확보)
"""
import os
import json
from datetime import datetime
from typing import Dict, Optional, Any

_ARCHIVE_PATH = os.path.join(os.path.dirname(__file__), "channel_archive.json")


def _load_all() -> Dict[str, Any]:
    if not os.path.exists(_ARCHIVE_PATH):
        return {}
    try:
        with open(_ARCHIVE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_all(archive: Dict[str, Any]):
    with open(_ARCHIVE_PATH, "w", encoding="utf-8") as f:
        json.dump(archive, f, ensure_ascii=False, indent=2, default=str)


def has_archive(week_label: str) -> bool:
    """해당 주차의 채널 수집 결과가 이미 보존되어 있는지 확인."""
    return week_label in _load_all()


def save_channel_results(week_label: str, collection_results: Dict[str, "ChannelResult"]):
    """
    채널 수집 결과를 주차별로 보존.
    collection_results: {channel_key: ChannelResult} (collector.collect_all() 반환값)
    """
    archive = _load_all()
    snapshot = {}
    for key, r in collection_results.items():
        snapshot[key] = {
            "channel": getattr(r, "channel", None) or getattr(r, "channel_key", key),
            "channel_name": getattr(r, "channel_name", key),
            "success": getattr(r, "success", None) if getattr(r, "success", None) is not None else getattr(r, "detected", False),
            "data": getattr(r, "data", None),
            "error": getattr(r, "error", None),
            "error_type": getattr(r, "error_type", None),
            "error_label": getattr(r, "error_label", None),
            "collected_at": getattr(r, "collected_at", None),
        }
    archive[week_label] = {
        "archived_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "channels": snapshot,
    }
    _save_all(archive)


def load_channel_results(week_label: str) -> Optional[Dict[str, "ChannelResult"]]:
    """
    보존된 주차별 채널 수집 결과를 ChannelResult 객체로 복원해서 반환.
    아카이브가 없으면 None.
    """
    archive = _load_all()
    entry = archive.get(week_label)
    if not entry:
        return None

    is_bank = week_label.startswith("bank_")

    if is_bank:
        import importlib.util, pathlib
        _p = pathlib.Path(__file__).parent / "agents" / "bank" / "collector.py"
        _spec = importlib.util.spec_from_file_location("bank_collector", _p)
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        BankCR = _mod.ChannelResult
        restored = {}
        for key, snap in entry.get("channels", {}).items():
            restored[key] = BankCR(
                channel_key=snap.get("channel", key),
                channel_name=snap.get("channel_name", key),
                detected=snap.get("success", False),
                data=snap.get("data") or {},
                error=snap.get("error"),
            )
        return restored

    from collector import ChannelResult  # 지연 import (순환참조 방지)

    restored = {}
    for key, snap in entry.get("channels", {}).items():
        restored[key] = ChannelResult(
            channel=snap.get("channel", key),
            channel_name=snap.get("channel_name", key),
            success=snap.get("success", False),
            data=snap.get("data"),
            error=snap.get("error"),
            error_type=snap.get("error_type"),
            error_label=snap.get("error_label"),
            collected_at=snap.get("collected_at", entry.get("archived_at", "")),
        )
    return restored


def get_archived_at(week_label: str) -> Optional[str]:
    """해당 주차 아카이브가 저장된 시각 반환 (없으면 None)."""
    archive = _load_all()
    entry = archive.get(week_label)
    return entry.get("archived_at") if entry else None


def save_raw_data(key: str, data: Any):
    """LLM 분석 결과 등 임의 dict를 아카이브에 저장."""
    archive = _load_all()
    archive[key] = {
        "archived_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "raw": data,
    }
    _save_all(archive)


def load_raw_data(key: str) -> Optional[Any]:
    """저장된 raw dict 반환 (없으면 None)."""
    archive = _load_all()
    entry = archive.get(key)
    if entry and "raw" in entry:
        return entry["raw"]
    return None
