"""
Weekly Report Generator — HTML + 텍스트 주간 리포트 생성
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from analyzer import ETFDiDResult, LPResult, COMPARISON_MAP
from collector import ChannelResult, ERROR_TYPES


def _fmt(val: float, unit: str = "") -> str:
    if val is None:
        return "N/A"
    return f"{val:+.1f}{unit}" if val != 0 else f"0.0{unit}"


def _reliability_badge(rel: str) -> str:
    return {"high": "🟢 높음", "medium": "🟡 중간", "low": "🔴 낮음"}.get(rel, rel)


def build_report(
    collection_results: Dict[str, ChannelResult],
    llm_result: Dict,
    did_results: Dict[str, ETFDiDResult],
    week_label: str = "",
    google_trends_data: Optional[Dict] = None,
) -> Dict:
    """
    구조화된 리포트 딕셔너리 반환.
    report.py는 데이터 모델만 생성하고, 렌더링은 app.py / export_html이 담당.
    """
    now = datetime.now()
    week_label = week_label or now.strftime("%Y-%m-%d")

    # ── 채널 수집 현황 ──────────────────────────────────────────────────────
    channels_ok = [r for r in collection_results.values() if r.success]
    channels_fail = [r for r in collection_results.values() if not r.success]

    channel_summary = {
        "total": len(collection_results),
        "success": len(channels_ok),
        "fail": len(channels_fail),
        "items": [
            {
                "name": r.channel_name,
                "success": r.success,
                "note": (r.error_label or "") if not r.success else _summarize_channel_data(r),
            }
            for r in collection_results.values()
        ],
    }

    # ── 마케팅 감지 ─────────────────────────────────────────────────────────
    marketing = {
        "detected": llm_result.get("marketing_detected", False),
        "etf_codes": llm_result.get("etf_codes", []),
        "summary": llm_result.get("summary", ""),
    }

    # ── ETF별 DiD 결과 ──────────────────────────────────────────────────────
    etf_reports = []
    for code, res in did_results.items():
        etf_reports.append({
            "code": code,
            "name": res.kodex_name,
            "judgement": res.judgement,
            "judgement_emoji": res.judgement_emoji,
            "did_value": res.did_value,
            "kodex_change_pct": res.kodex_change_pct,
            "tiger_change_pct": res.tiger_change_pct,
            "ace_change_pct": res.ace_change_pct,
            "control_avg_pct": res.control_avg_pct,
            "lp": {
                "suspicious": res.lp.suspicious,
                "z_score": res.lp.z_score,
                "reliability": res.lp.reliability,
                "reliability_label": _reliability_badge(res.lp.reliability),
                "is_estimate": res.lp.is_estimate,
                "note": res.lp.note,
            },
            "current_fi": res.current.financial_investment,
            "current_ind": res.current.individual,
            "baseline_fi_avg": res.baseline.fi_avg,
            "baseline_ind_avg": res.baseline.ind_avg,
            "weeks_used": res.baseline.weeks_used,
            "notes": res.notes,
            "calculation_log": res.calculation_log,
        })

    # ── 구글 트렌드 요약 ────────────────────────────────────────────────────
    trends_summary = None
    if google_trends_data:
        trends_summary = google_trends_data

    # ── 다음 주 체크포인트 ──────────────────────────────────────────────────
    checkpoints = _build_checkpoints(did_results, llm_result)

    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "week_label": week_label,
        "channel_summary": channel_summary,
        "marketing": marketing,
        "etf_reports": etf_reports,
        "trends_summary": trends_summary,
        "checkpoints": checkpoints,
    }


def _summarize_channel_data(r: ChannelResult) -> str:
    if not r.data:
        return "데이터 없음"
    d = r.data
    if "videos" in d:
        etf_cnt = sum(1 for v in d["videos"] if v.get("is_etf_related"))
        return f"영상 {len(d['videos'])}개 수집 (ETF 관련 {etf_cnt}개)"
    if "articles" in d:
        return f"기사 {len(d['articles'])}건 수집"
    if "events" in d:
        return f"이벤트 {len(d['events'])}건 수집"
    if "trends" in d:
        parts = [f"{k}: {v['change_pct']:+.1f}%" for k, v in d["trends"].items()]
        return ", ".join(parts)
    if "news" in d:
        return f"보도자료 {len(d['news'])}건 수집"
    if "posts" in d:
        return f"게시물 {len(d['posts'])}건 수집"
    return "수집 완료"


def _build_checkpoints(did_results: Dict[str, ETFDiDResult], llm_result: Dict) -> List[str]:
    pts = []
    for code, res in did_results.items():
        if res.lp.suspicious:
            pts.append(f"⚠️ {res.kodex_name}: LP 개입 여부 추가 확인 필요 (z={res.lp.z_score})")
        if res.did_value >= 0.3:
            pts.append(f"✅ {res.kodex_name}: 마케팅 효과 감지 (DiD={res.did_value:+.2f}) — 다음 주 지속 여부 추적")
        if res.did_value < -0.3:
            pts.append(f"🔴 {res.kodex_name}: 유의미한 효과 확인 어려움 — 추가 확인 필요")
        if res.baseline.weeks_used < 4:
            pts.append(f"📊 {res.kodex_name}: 베이스라인 데이터 {res.baseline.weeks_used}주만 확보 — 4주 이상 축적 필요")
    if not llm_result.get("marketing_detected"):
        pts.append("📋 이번 주 마케팅 활동 미감지 — 베이스라인만 업데이트됨")
    if not pts:
        pts.append("📌 특이사항 없음")
    return pts


# ── HTML 내보내기 ─────────────────────────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>KODEX ETF 마케팅 효과 주간 리포트 — {week_label}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif; background: #f0f2f6; color: #222; }}
  .container {{ max-width: 960px; margin: 0 auto; padding: 24px 16px; }}
  header {{ background: #003087; color: #fff; border-radius: 8px; padding: 24px; margin-bottom: 20px; }}
  header h1 {{ font-size: 1.5rem; }}
  header .sub {{ font-size: 0.9rem; opacity: 0.8; margin-top: 4px; }}
  .card {{ background: #fff; border-radius: 8px; padding: 20px; margin-bottom: 16px; box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
  .card h2 {{ font-size: 1.05rem; color: #003087; border-bottom: 2px solid #003087; padding-bottom: 8px; margin-bottom: 14px; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.78rem; font-weight: 700; }}
  .badge-green {{ background: #d4edda; color: #155724; }}
  .badge-red {{ background: #f8d7da; color: #721c24; }}
  .badge-yellow {{ background: #fff3cd; color: #856404; }}
  .badge-gray {{ background: #e9ecef; color: #495057; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; }}
  th {{ background: #003087; color: #fff; padding: 8px 10px; text-align: left; }}
  td {{ padding: 8px 10px; border-bottom: 1px solid #e9ecef; }}
  tr:nth-child(even) td {{ background: #f8f9fa; }}
  .did-strong {{ color: #155724; font-weight: 700; }}
  .did-medium {{ color: #856404; font-weight: 700; }}
  .did-neutral {{ color: #495057; }}
  .did-neg {{ color: #721c24; font-weight: 700; }}
  .channel-ok {{ color: #28a745; }}
  .channel-fail {{ color: #dc3545; }}
  .log-block {{ background: #f8f9fa; border-left: 3px solid #003087; padding: 10px 14px;
                font-size: 0.82rem; font-family: monospace; white-space: pre-wrap; margin-top: 8px; }}
  .checkpoint {{ background: #fffbea; border-left: 3px solid #ffc107; padding: 8px 12px;
                 margin-bottom: 6px; border-radius: 0 4px 4px 0; font-size: 0.9rem; }}
  footer {{ text-align: center; font-size: 0.78rem; color: #888; margin-top: 24px; }}
  @media print {{ body {{ background: #fff; }} .container {{ padding: 0; }} }}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>📊 KODEX ETF 마케팅 효과 주간 리포트</h1>
    <div class="sub">기준 주: {week_label} &nbsp;|&nbsp; 생성: {generated_at}</div>
  </header>

  <!-- 1. 채널 수집 현황 -->
  <div class="card">
    <h2>1. 채널별 수집 현황</h2>
    <p style="margin-bottom:12px;font-size:.9rem;">
      총 {ch_total}개 채널 &nbsp;|&nbsp;
      <span class="channel-ok">✔ 성공 {ch_success}개</span> &nbsp;|&nbsp;
      <span class="channel-fail">✘ 실패 {ch_fail}개</span>
    </p>
    <table>
      <tr><th>채널</th><th>결과</th><th>비고</th></tr>
      {channel_rows}
    </table>
  </div>

  <!-- 2. 마케팅 활동 감지 -->
  <div class="card">
    <h2>2. 마케팅 활동 감지</h2>
    {marketing_block}
  </div>

  <!-- 3. ETF별 DiD 분석 -->
  <div class="card">
    <h2>3. ETF별 DiD 분석 결과</h2>
    {did_table}
  </div>

  <!-- 4. ETF 상세 계산 -->
  {etf_detail_blocks}

  <!-- 5. 다음 주 체크포인트 -->
  <div class="card">
    <h2>5. 다음 주 체크포인트</h2>
    {checkpoint_items}
  </div>

  <footer>Generated by KODEX ETF Marketing Effect Agent &nbsp;|&nbsp; Powered by Claude</footer>
</div>
</body>
</html>"""


def export_html(report: Dict) -> str:
    ch = report["channel_summary"]
    mk = report["marketing"]

    # 채널 행
    channel_rows = ""
    for item in ch["items"]:
        ok = item["success"]
        cls = "channel-ok" if ok else "channel-fail"
        icon = "✔" if ok else "✘"
        channel_rows += f'<tr><td>{item["name"]}</td><td class="{cls}">{icon}</td><td>{item["note"]}</td></tr>'

    # 마케팅 블록
    if mk["detected"]:
        etf_list = ", ".join(
            f'{COMPARISON_MAP.get(c, {}).get("name", c)} ({c})' for c in mk["etf_codes"]
        )
        marketing_block = (
            f'<p><span class="badge badge-green">마케팅 감지됨</span></p>'
            f'<p style="margin-top:8px;"><strong>대상 ETF:</strong> {etf_list}</p>'
            f'<p style="margin-top:6px;">{mk["summary"]}</p>'
        )
    else:
        marketing_block = (
            '<p><span class="badge badge-gray">이번 주 마케팅 활동 없음</span></p>'
            f'<p style="margin-top:8px;font-size:.9rem;">{mk["summary"]}</p>'
        )

    # DiD 테이블
    did_rows = ""
    for e in report["etf_reports"]:
        did = e["did_value"]
        # 새 판정 기준: 정규화 절대 변화 (≥1.0 강함 / ≥0.3 효과있음 / ≥-0.3 불분명 / <-0.3 어려움)
        if did >= 1.0:
            cls = "did-strong"
        elif did >= 0.3:
            cls = "did-medium"
        elif did >= -0.3:
            cls = "did-neutral"
        else:
            cls = "did-neg"

        lp_badge = ""
        if e["lp"]["suspicious"]:
            lp_badge = f' <span class="badge badge-yellow">LP⚠️</span>'
        rel_badge = {
            "high": '<span class="badge badge-green">신뢰도 高</span>',
            "medium": '<span class="badge badge-yellow">신뢰도 中</span>',
            "low": '<span class="badge badge-red">신뢰도 低</span>',
        }.get(e["lp"]["reliability"], "")

        # 비교군 변화율 (소수점 4자리, 단위 없음)
        tiger = f'{e["tiger_change_pct"]:+.4f}' if e["tiger_change_pct"] is not None else "N/A"
        ace   = f'{e["ace_change_pct"]:+.4f}'   if e["ace_change_pct"]   is not None else "N/A"

        did_rows += (
            f'<tr>'
            f'<td><strong>{e["judgement_emoji"]} {e["name"]}</strong>{lp_badge}</td>'
            f'<td>{e["kodex_change_pct"]:+.4f}</td>'
            f'<td>{tiger}</td>'
            f'<td>{ace}</td>'
            f'<td>{e["control_avg_pct"]:+.4f}</td>'
            f'<td class="{cls}"><strong>{did:+.4f}</strong></td>'
            f'<td>{e["judgement"]}</td>'
            f'<td>{rel_badge}</td>'
            f'</tr>'
        )

    did_table = (
        f'<table>'
        f'<tr><th>ETF</th><th>KODEX 변화율</th><th>TIGER</th><th>ACE</th>'
        f'<th>비교군 평균</th><th>DiD</th><th>판정</th><th>신뢰도</th></tr>'
        f'{did_rows}'
        f'</table>'
        f'<p style="font-size:.8rem;margin-top:6px;opacity:.7;">'
        f'※ 변화율 단위: 정규화 절대 변화 (≥1.0 강함 / ≥0.3 효과있음 / ≥-0.3 불분명 / &lt;-0.3 효과 확인 어려움)'
        f'</p>'
    )

    # 상세 계산 블록
    detail_blocks = ""
    for i, e in enumerate(report["etf_reports"], start=4):
        log_html = "\n".join(e["calculation_log"])
        notes_html = "".join(f"<li>{n}</li>" for n in e["notes"]) if e["notes"] else "<li>없음</li>"
        detail_blocks += f"""
  <div class="card">
    <h2>{i}. {e["name"]} 상세 계산</h2>
    <p><strong>LP 노이즈:</strong> {e["lp"]["note"]}</p>
    <p style="margin-top:6px;"><strong>기타 노트:</strong><ul style="margin-left:16px;margin-top:4px;">{notes_html}</ul></p>
    <div class="log-block">{log_html}</div>
  </div>"""

    # 체크포인트
    checkpoint_items = "".join(f'<div class="checkpoint">{p}</div>' for p in report["checkpoints"])

    return HTML_TEMPLATE.format(
        week_label=report["week_label"],
        generated_at=report["generated_at"],
        ch_total=ch["total"],
        ch_success=ch["success"],
        ch_fail=ch["fail"],
        channel_rows=channel_rows,
        marketing_block=marketing_block,
        did_table=did_table,
        etf_detail_blocks=detail_blocks,
        checkpoint_items=checkpoint_items,
    )
