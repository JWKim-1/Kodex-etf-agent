"""
은행 채널 마케팅 활동 수집기
- 유튜브 RSS 5개 (KB/신한/하나/우리/농협)
- 농협 네이버 블로그 RSS
- 하나은행 공식 블로그
- 네이버 뉴스 (모바일 스크래핑)
- 구글 뉴스 RSS

증권사 채널과 완전 격리 — analyzer.py 기준 컬럼: 은행
"""

import os
import re
import logging
import requests
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from bs4 import BeautifulSoup
from urllib.parse import unquote_plus, quote

logger = logging.getLogger(__name__)

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}
MOBILE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

# ── 채널 레이블 ────────────────────────────────────────────────────────────────
CHANNEL_LABELS = {
    "kb_youtube":       "KB국민은행 유튜브",
    "shinhan_youtube":  "신한은행 유튜브",
    "hana_youtube":     "하나은행 유튜브",
    "woori_youtube":    "우리은행 유튜브",
    "nh_youtube":       "NH농협은행 유튜브",
    "nh_blog":          "NH농협은행 블로그",
    "hana_blog":        "하나은행 블로그",
    "naver_news":       "네이버 뉴스 (은행+ETF)",
    "google_news":      "구글 뉴스 (은행+ETF)",
}

# ── 유튜브 채널 ID ─────────────────────────────────────────────────────────────
YOUTUBE_CHANNEL_IDS = {
    "kb_youtube":      "UCHq8auIJ8ewo7iD2pqX22UA",
    "shinhan_youtube": "UC4E394G9WuS9y6SlBZslMsQ",
    "hana_youtube":    "UCejh7cdlFSkCh_rqQT6WB8Q",
    "woori_youtube":   "UCcQ9V6nEYVMSRWWOrvHQqLg",
    "nh_youtube":      "UCsR09lr9oy0DMv6gtqh-XCw",
}

# ── 뉴스 검색 키워드 ───────────────────────────────────────────────────────────
NEWS_KEYWORDS = [
    "KB국민은행 ETF 이벤트",
    "신한은행 ETF 이벤트",
    "하나은행 ETF 이벤트",
    "우리은행 ETF 이벤트",
    "농협은행 ETF 이벤트",
    "KODEX 은행 이벤트",
    "삼성자산운용 은행 이벤트",
    "은행 ETF 프로모션",
]


@dataclass
class ChannelResult:
    channel_key: str
    channel_name: str
    detected: bool
    data: dict = field(default_factory=dict)
    error: Optional[str] = None


class BankChannelCollector:
    """
    은행 채널 마케팅 활동 수집기.
    기준 컬럼: 은행 (금융투자 아님 — 증권사와 완전 격리)
    """

    def __init__(self, week_start: datetime = None, week_end: datetime = None):
        from dotenv import load_dotenv
        load_dotenv()
        self.naver_client_id = os.getenv("NAVER_CLIENT_ID", "")
        self.naver_client_secret = os.getenv("NAVER_CLIENT_SECRET", "")

        if week_start is None:
            today = datetime.now()
            week_start = today - timedelta(days=today.weekday())
        if week_end is None:
            week_end = week_start + timedelta(days=6)
        self.week_start = week_start
        self.week_end = week_end

    def _in_range(self, dt: datetime) -> bool:
        return self.week_start <= dt <= self.week_end

    def _parse_date(self, s: str) -> Optional[datetime]:
        if not s:
            return None
        for fmt in ["%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z",
                    "%Y.%m.%d. %p %I:%M", "%Y.%m.%d."]:
            try:
                dt = datetime.strptime(s.strip()[:25], fmt)
                return dt.replace(tzinfo=None)
            except Exception:
                pass
        # 숫자만 추출
        nums = re.findall(r'\d+', s)
        if len(nums) >= 3:
            try:
                return datetime(int(nums[0]), int(nums[1]), int(nums[2]))
            except Exception:
                pass
        return None

    # ── 유튜브 RSS ────────────────────────────────────────────────────────────

    def _fetch_youtube_rss(self, key: str, ch_id: str) -> ChannelResult:
        name = CHANNEL_LABELS[key]
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={ch_id}"
        try:
            r = requests.get(url, headers=BROWSER_HEADERS, timeout=10)
            root = ET.fromstring(r.text)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            entries = root.findall("atom:entry", ns)

            # 유튜브는 ETF 직접 마케팅 키워드만 — 시황/일반 금융 콘텐츠 제외
            etf_keywords = ["ETF", "KODEX", "코덱스", "IRP", "연금저축펀드", "ETF 이벤트", "ETF 추천"]
            videos = []
            for e in entries:
                title_el = e.find("atom:title", ns)
                pub_el = e.find("atom:published", ns)
                link_el = e.find("atom:link", ns)
                if title_el is None:
                    continue
                title = title_el.text or ""
                pub_str = pub_el.text if pub_el is not None else ""
                pub_dt = self._parse_date(pub_str)
                link = link_el.get("href", "") if link_el is not None else ""

                is_etf = any(k in title for k in etf_keywords)
                videos.append({
                    "title": title, "pub_date": pub_str,
                    "link": link, "etf_related": is_etf,
                })

            etf_videos = [v for v in videos if v["etf_related"]]
            detected = len(etf_videos) > 0
            return ChannelResult(key, name, detected, data={
                "videos": videos[:15],
                "etf_videos": etf_videos,
                "total": len(videos),
            })
        except Exception as e:
            return ChannelResult(key, name, False, error=str(e))

    # ── 농협 네이버 블로그 RSS ────────────────────────────────────────────────

    def _ch_nh_blog(self) -> ChannelResult:
        key, name = "nh_blog", CHANNEL_LABELS["nh_blog"]
        try:
            r = requests.get(
                "https://rss.blog.naver.com/nhbanksns.xml",
                headers=BROWSER_HEADERS, timeout=10
            )
            root = ET.fromstring(r.text)
            items = root.findall(".//item")
            # 블로그도 ETF 직접 관련 키워드만
            etf_kws = ["ETF", "KODEX", "코덱스", "IRP", "연금저축펀드"]
            posts = []
            for item in items:
                title = item.findtext("title", "")
                link = item.findtext("link", "")
                pub = item.findtext("pubDate", "")
                is_relevant = any(k in title for k in etf_kws)
                posts.append({"title": title, "link": link, "pub_date": pub, "relevant": is_relevant})

            relevant = [p for p in posts if p["relevant"]]
            return ChannelResult(key, name, len(relevant) > 0, data={
                "posts": posts[:20], "relevant": relevant,
            })
        except Exception as e:
            return ChannelResult(key, name, False, error=str(e))

    # ── 하나은행 블로그 ───────────────────────────────────────────────────────

    def _ch_hana_blog(self) -> ChannelResult:
        key, name = "hana_blog", CHANNEL_LABELS["hana_blog"]
        try:
            r = requests.get("https://blog.hanabank.com/", headers=BROWSER_HEADERS, timeout=10)
            soup = BeautifulSoup(r.text, "lxml")
            etf_kws = ["ETF", "KODEX", "코덱스", "IRP", "연금저축펀드"]
            posts = []
            for a in soup.select("a"):
                title = a.get_text(strip=True)
                if len(title) < 10:
                    continue
                href = a.get("href", "")
                is_relevant = any(k in title for k in etf_kws)
                posts.append({"title": title, "link": href, "relevant": is_relevant})

            # 중복 제거
            seen = set()
            unique = []
            for p in posts:
                if p["title"] not in seen and len(p["title"]) > 10:
                    seen.add(p["title"])
                    unique.append(p)

            relevant = [p for p in unique if p["relevant"]]
            return ChannelResult(key, name, len(relevant) > 0, data={
                "posts": unique[:20], "relevant": relevant,
            })
        except Exception as e:
            return ChannelResult(key, name, False, error=str(e))

    # ── 네이버 뉴스 (API 우선, 모바일 스크래핑 fallback) ─────────────────────

    def _ch_naver_news(self) -> ChannelResult:
        key, name = "naver_news", CHANNEL_LABELS["naver_news"]
        articles = []
        seen = set()

        # ── API 방식 (키 있을 때) ──
        if self.naver_client_id and self.naver_client_secret:
            api_headers = {
                **BROWSER_HEADERS,
                "X-Naver-Client-Id": self.naver_client_id,
                "X-Naver-Client-Secret": self.naver_client_secret,
            }
            for kw in NEWS_KEYWORDS:
                try:
                    r = requests.get(
                        "https://openapi.naver.com/v1/search/news.json",
                        headers=api_headers,
                        params={"query": kw, "display": 20, "sort": "date"},
                        timeout=10,
                    )
                    for item in r.json().get("items", []):
                        title = re.sub(r"<[^>]+>", "", item.get("title", "")).replace("&quot;", '"').replace("&amp;", "&")
                        link = item.get("link", "")
                        pub = item.get("pubDate", "")
                        pub_dt = self._parse_date(pub)
                        if title and title not in seen:
                            seen.add(title)
                            articles.append({
                                "title": title, "link": link,
                                "pub_date": pub, "keyword": kw,
                                "in_range": self._in_range(pub_dt) if pub_dt else False,
                                "source": "naver_api",
                            })
                except Exception as e:
                    logger.debug(f"네이버 뉴스 API 실패 ({kw}): {e}")
            if articles:
                detected = any(a["in_range"] for a in articles)
                return ChannelResult(key, name, detected, data={"articles": articles[:50]})

        # ── 스크래핑 fallback ──
        for kw in NEWS_KEYWORDS:
            try:
                url = f"https://m.search.naver.com/search.naver?where=m_news&query={quote(kw)}&sort=1"
                r = requests.get(url, headers=MOBILE_HEADERS, timeout=10)
                soup = BeautifulSoup(r.text, "lxml")
                for a in soup.find_all("a", href=re.compile(r"news\.naver\.com|n\.news")):
                    title = a.get_text(strip=True)
                    if len(title) > 10 and re.search(r"[가-힣]", title) and title not in seen:
                        seen.add(title)
                        articles.append({"title": title, "link": a.get("href", ""),
                                         "keyword": kw, "source": "naver_mobile"})
            except Exception as e:
                logger.debug(f"네이버 뉴스 스크래핑 실패 ({kw}): {e}")

        detected = len(articles) > 0
        return ChannelResult(key, name, detected, data={"articles": articles[:50]})

    # ── 구글 뉴스 RSS ─────────────────────────────────────────────────────────

    def _ch_google_news(self) -> ChannelResult:
        key, name = "google_news", CHANNEL_LABELS["google_news"]
        articles = []
        seen = set()

        for kw in NEWS_KEYWORDS[:4]:  # 주요 키워드만
            try:
                url = f"https://news.google.com/rss/search?q={quote(kw)}&hl=ko&gl=KR&ceid=KR:ko"
                r = requests.get(url, headers=BROWSER_HEADERS, timeout=10)
                root = ET.fromstring(r.text)
                for item in root.findall(".//item"):
                    title = item.findtext("title", "")
                    link = item.findtext("link", "")
                    pub = item.findtext("pubDate", "")
                    if title and title not in seen:
                        seen.add(title)
                        pub_dt = self._parse_date(pub)
                        articles.append({
                            "title": title, "link": link,
                            "pub_date": pub, "keyword": kw,
                            "in_range": self._in_range(pub_dt) if pub_dt else False,
                        })
            except Exception as e:
                logger.debug(f"구글 뉴스 실패 ({kw}): {e}")

        detected = any(a["in_range"] for a in articles)
        return ChannelResult(key, name, detected, data={"articles": articles[:30]})

    # ── 전체 수집 ─────────────────────────────────────────────────────────────

    def collect_all(self, progress_callback=None) -> dict:
        channels = [
            ("kb_youtube",      lambda: self._fetch_youtube_rss("kb_youtube",      YOUTUBE_CHANNEL_IDS["kb_youtube"])),
            ("shinhan_youtube",  lambda: self._fetch_youtube_rss("shinhan_youtube", YOUTUBE_CHANNEL_IDS["shinhan_youtube"])),
            ("hana_youtube",    lambda: self._fetch_youtube_rss("hana_youtube",    YOUTUBE_CHANNEL_IDS["hana_youtube"])),
            ("woori_youtube",   lambda: self._fetch_youtube_rss("woori_youtube",   YOUTUBE_CHANNEL_IDS["woori_youtube"])),
            ("nh_youtube",      lambda: self._fetch_youtube_rss("nh_youtube",      YOUTUBE_CHANNEL_IDS["nh_youtube"])),
            ("nh_blog",         self._ch_nh_blog),
            ("hana_blog",       self._ch_hana_blog),
            ("naver_news",      self._ch_naver_news),
            ("google_news",     self._ch_google_news),
        ]

        results = {}
        for idx, (key, func) in enumerate(channels):
            if progress_callback:
                progress_callback(idx + 1, len(channels), CHANNEL_LABELS[key])
            try:
                results[key] = func()
            except Exception as e:
                results[key] = ChannelResult(key, CHANNEL_LABELS[key], False, error=str(e))

        return results


# ── 빠른 테스트 ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    collector = BankChannelCollector()

    def prog(idx, total, name):
        print(f"  [{idx}/{total}] {name}")

    print("은행 채널 수집 테스트")
    results = collector.collect_all(progress_callback=prog)
    print()
    for key, r in results.items():
        status = "감지" if r.detected else "없음"
        err = f" | 오류: {r.error}" if r.error else ""
        count = len(r.data.get("articles", r.data.get("videos", r.data.get("posts", []))))
        print(f"[{status}] {r.channel_name}: {count}건{err}")
