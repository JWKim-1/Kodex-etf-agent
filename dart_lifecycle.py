"""
ETF 상장폐지 / 신규상장 모니터링
흐름:
  네이버뉴스 수집 → LLM 판별(실제 상폐/신규인지, ETF명/운용사) → 키워드 폴백
  신규상장 감지 시 → 해당 ETF명으로 뉴스/유튜브 검색 → 마케팅 활동 요약
  수집 단위: 최근 1주 (히스토리에 누적)
"""

import os, sys, json, re, requests, logging
from datetime import date, datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).parent
sys.path.insert(0, str(_ROOT))
from dotenv import load_dotenv
load_dotenv()

HISTORY_FILE  = _ROOT / "lifecycle_history.json"
DART_API_KEY  = os.getenv("DART_API_KEY", "")
NAVER_ID      = os.getenv("NAVER_CLIENT_ID", "")
NAVER_SEC     = os.getenv("NAVER_CLIENT_SECRET", "")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")

CORP_CODES = {
    "삼성자산운용":     "00260453",
    "미래에셋자산운용": "00259776",
    "KB자산운용":       "00104500",
    "한국투자신탁운용": "00324548",
    "키움투자자산운용": "00120191",
}

# 키워드 폴백용
_KW_DELIST  = ["상장폐지", "상폐", "만기상환", "청산", "해지상환"]
_KW_NEW     = ["신규상장", "상장 예정", "새로 상장", "상장일"]

logger = logging.getLogger(__name__)


def load_history() -> dict:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"weeks": {}, "last_updated": ""}


def save_history(h: dict):
    h["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    HISTORY_FILE.write_text(json.dumps(h, ensure_ascii=False, indent=2), encoding="utf-8")


def _clean_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def _week_label() -> str:
    today = date.today()
    mon = today - timedelta(days=today.weekday())
    fri = mon + timedelta(days=4)
    return f"{mon.month}.{mon.day}-{fri.month}.{fri.day}"


# ── 네이버 뉴스 수집 ─────────────────────────────────────────────────────────
def _naver_news(query: str, days: int = 7) -> list:
    if not NAVER_ID:
        return []
    try:
        r = requests.get(
            "https://openapi.naver.com/v1/search/news.json",
            params={"query": query, "display": 30, "sort": "date"},
            headers={"X-Naver-Client-Id": NAVER_ID, "X-Naver-Client-Secret": NAVER_SEC},
            timeout=10,
        )
        cutoff = datetime.now() - timedelta(days=days)
        items = []
        for x in r.json().get("items", []):
            try:
                pub = datetime.strptime(x["pubDate"], "%a, %d %b %Y %H:%M:%S %z").replace(tzinfo=None)
            except Exception:
                pub = datetime.now()
            if pub >= cutoff:
                items.append({
                    "title":       _clean_html(x.get("title", "")),
                    "link":        x.get("link", ""),
                    "pub_date":    pub.strftime("%Y-%m-%d"),
                    "description": _clean_html(x.get("description", ""))[:200],
                })
        return items
    except Exception as e:
        logger.warning(f"네이버뉴스 실패 ({query}): {e}")
        return []


# ── DART 공시 수집 ───────────────────────────────────────────────────────────
def _dart_notices(days: int = 7) -> list:
    if not DART_API_KEY:
        return []
    bgn = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")
    results = []
    kw = ("만기", "해지", "청산", "상장폐지")
    for corp_name, corp_code in CORP_CODES.items():
        try:
            r = requests.get("https://opendart.fss.or.kr/api/list.json", params={
                "crtfc_key": DART_API_KEY,
                "corp_code": corp_code,
                "bgn_de": bgn, "end_de": end,
                "page_count": 50,
            }, timeout=10)
            for x in (r.json().get("list") or []):
                nm = x.get("report_nm", "")
                if any(k in nm for k in kw):
                    results.append({
                        "date":        x.get("rcept_dt", ""),
                        "report_name": nm,
                        "corp_name":   x.get("corp_name", ""),
                        "운용사":      corp_name,
                        "dart_url":    f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={x.get('rcept_no','')}",
                    })
        except Exception as e:
            logger.warning(f"DART {corp_name}: {e}")
    return results


# ── ETF 운용사 사이트 상폐 공지 Selenium 수집 ───────────────────────────────
def _selenium_delist_notices() -> list:
    """TIGER/HANARO 등 운용사 공지 페이지에서 상폐 공지 Selenium 수집."""
    DELIST_KW = ["상장폐지","상폐","만기","만기상환","청산","해지상환","존속기한","사전안내"]
    ETF_SITES = [
        ("미래에셋(TIGER)", "https://investments.miraeasset.com/tigeretf/ko/customer/notice/list.do"),
        ("NH아문디(HANARO)", "https://www.hanaroetf.com/customer/notice"),
        ("KB자산운용(RISE)", "https://www.riseetf.co.kr/cust/notice"),
        ("한국투자신탁(ACE)", "https://www.aceetf.co.kr/cs/notice"),
        ("삼성자산운용(KODEX)", "https://www.samsungfund.com/etf/lounge/notice.do"),
    ]
    results = []
    try:
        import sys as _sys; _sys.path.insert(0, str(_ROOT))
        from collector import _selenium_driver
        import time as _time
        driver = _selenium_driver()
        try:
            for corp_name, url in ETF_SITES:
                try:
                    driver.get(url); _time.sleep(3)
                    text = driver.find_element("tag name","body").text
                    lines = [l.strip() for l in text.split("\n") if l.strip()]
                    for i, line in enumerate(lines):
                        if any(k in line for k in DELIST_KW) and len(line) > 8:
                            # 다음 줄에 날짜 있으면 같이 수집
                            date_str = ""
                            if i+1 < len(lines):
                                dm = re.search(r"20\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2}", lines[i+1])
                                if dm: date_str = dm.group()
                            results.append({
                                "title": line[:80],
                                "corp_name": corp_name,
                                "date": date_str,
                                "url": url,
                                "source": "운용사공지",
                            })
                except Exception as e:
                    logger.warning(f"Selenium {corp_name}: {e}")
        finally:
            driver.quit()
    except Exception as e:
        logger.warning(f"Selenium 상폐 수집 실패: {e}")
    # 중복 제거
    seen = set()
    dedup = []
    for r in results:
        key = r["title"][:40]
        if key not in seen:
            seen.add(key); dedup.append(r)
    return dedup


# ── LLM 판별 ────────────────────────────────────────────────────────────────
def _llm_classify(news_items: list, task: str) -> list:
    """
    1단계: 키워드로 후보만 추림
    2단계: 후보가 있을 때만 LLM에 넘겨서 실제 상폐/신규인지 + ETF명/운용사 추출
    키워드 후보 없으면 LLM 완전 스킵
    """
    if not news_items:
        return []

    kw_list = _KW_DELIST if task == "delist" else _KW_NEW

    # 1단계: 키워드 필터
    candidates = [x for x in news_items
                  if any(k in x["title"] or k in x.get("description","") for k in kw_list)]

    if not candidates:
        return []

    # 키워드만으로도 충분히 명확한 경우 or API 키 없으면 그대로 반환
    if not ANTHROPIC_KEY:
        for x in candidates:
            x.setdefault("etf_name", "")
            x.setdefault("운용사", "")
        return candidates

    # 2단계: 후보만 LLM에 전달
    action = "ETF 상장폐지·만기상환·청산" if task == "delist" else "ETF 신규상장·상장 예정"
    lines = "\n".join(f"[{i}] {x['pub_date']} {x['title']} — {x.get('description','')[:80]}"
                      for i, x in enumerate(candidates))

    prompt = f"""다음은 키워드로 1차 필터링된 뉴스입니다. 실제 '{action}' 기사만 남기고,
ETF 이름과 운용사를 추출하세요. 관련 없는 기사(시황·전략·추천 등)는 제외합니다.

{lines}

JSON 배열로만 응답 (설명 없이):
[{{"idx": 번호, "etf_name": "ETF명(모르면 빈값)", "운용사": "운용사명(모르면 빈값)"}}]
없으면 []"""

    try:
        import anthropic as ant
        client = ant.Anthropic(api_key=ANTHROPIC_KEY)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        hits = json.loads(raw)
        results = []
        for h in hits:
            idx = h.get("idx")
            if idx is None or idx >= len(candidates):
                continue
            item = dict(candidates[idx])
            item["etf_name"] = h.get("etf_name", "")
            item["운용사"]   = h.get("운용사", "")
            results.append(item)
        return results
    except Exception as e:
        logger.warning(f"LLM 판별 실패 ({task}): {e} → 키워드 후보 그대로 반환")
        for x in candidates:
            x.setdefault("etf_name", "")
            x.setdefault("운용사", "")
        return candidates


# ── 신규상장 마케팅 활동 수집 ─────────────────────────────────────────────────
def _fetch_launch_marketing(etf_name: str, 운용사: str, days: int = 14) -> dict:
    """신규상장 ETF의 뉴스·유튜브 홍보 활동 요약."""
    if not etf_name:
        return {}

    query = etf_name.replace("KODEX", "").replace("TIGER", "").replace("ACE", "").strip()
    news = _naver_news(f"{etf_name} 상장", days=days)
    yt_news = _naver_news(f"{운용사} {query} ETF", days=days)

    all_titles = [x["title"] for x in news + yt_news][:10]

    if not all_titles:
        return {"summary": "마케팅 활동 정보 없음", "activities": []}

    if ANTHROPIC_KEY:
        try:
            import anthropic as ant
            client = ant.Anthropic(api_key=ANTHROPIC_KEY)
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                messages=[{"role": "user", "content":
                    f"다음은 '{etf_name}' ETF 신규상장 관련 뉴스 제목들입니다.\n"
                    f"{chr(10).join(all_titles)}\n\n"
                    f"이 ETF 출시 시 {운용사}가 어떤 마케팅 활동을 했는지 2-3문장으로 요약하세요. "
                    f"(뉴스 보도, 유튜브, 블로그 홍보 여부 등)"
                }],
            )
            summary = msg.content[0].text.strip()
        except Exception:
            summary = f"관련 뉴스 {len(all_titles)}건 발견"
    else:
        summary = f"관련 뉴스 {len(all_titles)}건 발견"

    return {
        "summary": summary,
        "activities": [{"title": x["title"], "link": x["link"], "date": x["pub_date"]}
                       for x in (news + yt_news)[:5]],
    }


# ── 메인 수집 함수 (최근 1주) ────────────────────────────────────────────────
def collect_lifecycle(days: int = 7) -> dict:
    history = load_history()
    week = _week_label()

    # 이미 이번 주 수집됐으면 스킵
    if week in history.get("weeks", {}) and not os.getenv("LIFECYCLE_FORCE"):
        logger.info(f"이번 주({week}) 이미 수집됨 — 스킵")
        return history

    logger.info(f"[lifecycle] {week} 수집 시작...")

    # 1. 뉴스 + 운용사 공지 수집
    delist_raw  = _naver_news("ETF 상장폐지", days)
    newlist_raw = _naver_news("ETF 신규상장", days)
    dart        = _dart_notices(days)
    # 1-1. 운용사 사이트 상폐 공지 (Selenium)
    selenium_delist = _selenium_delist_notices()
    logger.info(f"  뉴스: 상폐후보 {len(delist_raw)}건 / 신규후보 {len(newlist_raw)}건 / DART {len(dart)}건 / 운용사공지 {len(selenium_delist)}건")

    # 2. LLM 판별
    delistings  = _llm_classify(delist_raw,  "delist")
    new_listings = _llm_classify(newlist_raw, "newlist")
    logger.info(f"  판별 후: 상폐 {len(delistings)}건 / 신규 {len(new_listings)}건")

    # 3. 신규상장 마케팅 활동
    for item in new_listings:
        etf_name = item.get("etf_name", item.get("title", ""))[:20]
        운용사   = item.get("운용사", "")
        item["launch_marketing"] = _fetch_launch_marketing(etf_name, 운용사, days=14)
        logger.info(f"  마케팅수집: {etf_name}")

    # 4. 히스토리 누적 저장
    if "weeks" not in history:
        history["weeks"] = {}
    history["weeks"][week] = {
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "delistings":   delistings,
        "new_listings": new_listings,
        "dart_notices": dart,
        "etf_site_delist": selenium_delist,
    }
    # 전체 집계 (UI용 플랫 리스트)
    history["delist_news"]  = [x for w in history["weeks"].values() for x in w.get("delistings", [])]
    history["newlist_news"] = [x for w in history["weeks"].values() for x in w.get("new_listings", [])]
    history["dart_notices"] = [x for w in history["weeks"].values() for x in w.get("dart_notices", [])]

    save_history(history)
    logger.info(f"  저장 완료: lifecycle_history.json")
    return history


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    result = collect_lifecycle(days=7)
    week = _week_label()
    w = result.get("weeks", {}).get(week, {})
    print(f"상폐: {len(w.get('delistings',[]))}건")
    print(f"신규: {len(w.get('new_listings',[]))}건")
    print(f"DART: {len(w.get('dart_notices',[]))}건")
    for x in w.get("delistings", []):
        print(" ", x.get("pub_date"), x.get("etf_name"), x.get("title","")[:50])
