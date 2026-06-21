"""
ETF 상장폐지 / 신규상장 모니터링
- 네이버 뉴스 API: "ETF 상장폐지" / "ETF 신규상장" 키워드
- DART API: 삼성자산운용 등 주요 운용사 공시 (만기/청산)
- pykrx: 주차별 티커 비교로 변동 감지
결과 → lifecycle_history.json 저장
"""

import os, sys, json, re, requests, logging
from datetime import date, datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).parent
sys.path.insert(0, str(_ROOT))
from dotenv import load_dotenv
load_dotenv()

HISTORY_FILE = _ROOT / "lifecycle_history.json"
DART_API_KEY = os.getenv("DART_API_KEY", "")
NAVER_ID     = os.getenv("NAVER_CLIENT_ID", "")
NAVER_SEC    = os.getenv("NAVER_CLIENT_SECRET", "")

# 주요 운용사 DART corp_code
CORP_CODES = {
    "삼성자산운용":     "00260453",
    "미래에셋자산운용": "00259776",
    "KB자산운용":       "00104500",
    "한국투자신탁운용": "00324548",
    "키움투자자산운용": "00120191",
}

logger = logging.getLogger(__name__)


def load_history() -> dict:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"delistings": [], "new_listings": [], "last_updated": ""}


def save_history(h: dict):
    h["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    HISTORY_FILE.write_text(json.dumps(h, ensure_ascii=False, indent=2), encoding="utf-8")


def _clean_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


# ── 네이버 뉴스 수집 ─────────────────────────────────────────────────────────
def fetch_naver_news(query: str, days_back: int = 90) -> list:
    if not NAVER_ID:
        return []
    try:
        r = requests.get(
            "https://openapi.naver.com/v1/search/news.json",
            params={"query": query, "display": 50, "sort": "date"},
            headers={"X-Naver-Client-Id": NAVER_ID, "X-Naver-Client-Secret": NAVER_SEC},
            timeout=10,
        )
        cutoff = datetime.now() - timedelta(days=days_back)
        items = []
        for x in r.json().get("items", []):
            try:
                pub = datetime.strptime(x["pubDate"], "%a, %d %b %Y %H:%M:%S %z").replace(tzinfo=None)
            except Exception:
                pub = datetime.now()
            if pub >= cutoff:
                items.append({
                    "title":    _clean_html(x.get("title", "")),
                    "link":     x.get("link", ""),
                    "pub_date": pub.strftime("%Y-%m-%d"),
                    "description": _clean_html(x.get("description", ""))[:200],
                })
        return items
    except Exception as e:
        logger.warning(f"네이버 뉴스 조회 실패 ({query}): {e}")
        return []


# ── DART 공시 수집 ───────────────────────────────────────────────────────────
def fetch_dart_notices(corp_code: str, bgn_de: str, end_de: str, keywords=("만기", "해지", "청산", "상장폐지")) -> list:
    if not DART_API_KEY:
        return []
    results = []
    for page in range(1, 6):
        try:
            r = requests.get("https://opendart.fss.or.kr/api/list.json", params={
                "crtfc_key": DART_API_KEY,
                "corp_code": corp_code,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "page_count": 100,
                "page_no": page,
            }, timeout=10)
            items = r.json().get("list") or []
            if not items:
                break
            for x in items:
                name = x.get("report_nm", "")
                if any(k in name for k in keywords):
                    results.append({
                        "date":        x.get("rcept_dt", ""),
                        "report_name": name,
                        "rcept_no":    x.get("rcept_no", ""),
                        "corp_name":   x.get("corp_name", ""),
                        "dart_url":    f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={x.get('rcept_no','')}",
                    })
            if len(items) < 100:
                break
        except Exception as e:
            logger.warning(f"DART 조회 실패: {e}")
            break
    return results


# ── pykrx 티커 비교 (신규/소멸 감지) ─────────────────────────────────────────
def detect_krx_changes(weeks_back: int = 4) -> dict:
    """최근 N주간 pykrx ETF 티커 목록 비교."""
    try:
        from pykrx import stock
    except ImportError:
        return {"new": [], "gone": []}

    today = date.today()
    results = {"new": [], "gone": []}
    prev_set = None
    prev_date = None

    for i in range(weeks_back, -1, -1):
        target = today - timedelta(weeks=i)
        # 금요일로 보정
        target = target - timedelta(days=target.weekday()) + timedelta(days=4)
        date_str = target.strftime("%Y%m%d")
        try:
            tickers = set(stock.get_etf_ticker_list(date_str) or [])
        except Exception:
            continue
        if prev_set is not None and tickers:
            new = tickers - prev_set
            gone = prev_set - tickers
            for code in new:
                try:
                    name = stock.get_etf_ticker_name(code) or code
                except Exception:
                    name = code
                results["new"].append({"code": code, "name": name, "detected_week": target.strftime("%Y-%m-%d")})
            for code in gone:
                try:
                    name = stock.get_etf_ticker_name(code) or code
                except Exception:
                    name = code
                results["gone"].append({"code": code, "name": name, "detected_week": target.strftime("%Y-%m-%d")})
        prev_set = tickers
        prev_date = target

    return results


# ── 메인 수집 함수 ───────────────────────────────────────────────────────────
def collect_lifecycle(days_back: int = 180) -> dict:
    history = load_history()
    bgn_de = (datetime.now() - timedelta(days=days_back)).strftime("%Y%m%d")
    end_de = datetime.now().strftime("%Y%m%d")

    # 1. 네이버 뉴스
    delist_news   = fetch_naver_news("ETF 상장폐지", days_back)
    newlist_news  = fetch_naver_news("ETF 신규상장", days_back)
    maturity_news = fetch_naver_news("ETF 만기상환", days_back)
    logger.info(f"뉴스 수집: 상폐 {len(delist_news)}건 / 신규 {len(newlist_news)}건 / 만기 {len(maturity_news)}건")

    # 2. DART 공시 (삼성자산운용 위주)
    dart_notices = []
    for corp_name, corp_code in CORP_CODES.items():
        notices = fetch_dart_notices(corp_code, bgn_de, end_de)
        for n in notices:
            n["운용사"] = corp_name
        dart_notices.extend(notices)
        if notices:
            logger.info(f"DART {corp_name}: {len(notices)}건")

    # 3. 저장
    history["delist_news"]   = delist_news
    history["newlist_news"]  = newlist_news
    history["maturity_news"] = maturity_news
    history["dart_notices"]  = dart_notices
    history["collected_at"]  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_history(history)
    logger.info(f"lifecycle_history.json 저장 완료")
    return history


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    result = collect_lifecycle(days_back=180)
    print(f"상폐 뉴스: {len(result['delist_news'])}건")
    print(f"신규 뉴스: {len(result['newlist_news'])}건")
    print(f"DART 공시: {len(result['dart_notices'])}건")
    for x in result["delist_news"][:5]:
        print(" ", x["pub_date"], x["title"])
