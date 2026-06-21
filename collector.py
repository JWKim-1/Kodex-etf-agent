"""
Samsung Securities ETF Marketing Data Collector
11개 채널 각각 try-except + 실패 분류 보고
"""

import io
import os
import re
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

ERROR_TYPES = {
    "ACCESS_BLOCKED": "접속 차단 (403/429)",
    "SPA_STRUCTURE": "SPA 구조로 정적 크롤링 불가",
    "LOGIN_REQUIRED": "로그인 필요",
    "API_KEY_REQUIRED": "API 키 필요",
    "BOT_DETECTED": "봇 탐지",
    "CONNECTION_ERROR": "네트워크 연결 오류",
    "PARSE_ERROR": "데이터 파싱 오류",
    "SUBSCRIBER_ONLY": "구독자만 수신 가능한 구조",
    "UNKNOWN": "알 수 없는 오류",
}

CHANNEL_LABELS = {
    # ── 삼성자산운용 이벤트 (증권채널 이벤트 주 소스) ──
    "samsung_fund_event":  "삼성자산운용 이벤트 페이지",
    # ── 삼성증권 ─────────────────────────────────────
    "samsung_youtube":     "삼성증권 유튜브",
    "samsung_blog":        "삼성증권 블로그 (네이버)",
    "kodex_blog":          "KODEX 공식 블로그 (samsungfundblog.com)",
    # ── 미래에셋증권 ─────────────────────────────────
    "mirae_youtube":       "미래에셋증권 유튜브",
    "mirae_blog":          "미래에셋증권 블로그 (how2invest)",
    # ── 키움증권 ─────────────────────────────────────
    "kiwoom_youtube":      "키움증권 유튜브",
    "kiwoom_blog":         "키움증권 블로그 (네이버)",
    # ── 토스증권 ─────────────────────────────────────
    "toss_youtube":        "토스증권 유튜브",
    # ── 한국투자증권 ─────────────────────────────────
    "kis_youtube":         "한국투자증권 유튜브",
    # ── 신한투자증권 ─────────────────────────────────
    "shinhan_youtube":     "신한투자증권 유튜브",
    # ── KB증권 ───────────────────────────────────────
    "kb_youtube":          "KB증권 유튜브",
    # ── 공통 채널 ────────────────────────────────────
    # krx_news, krx_trading 제거 — KRX 데이터는 pykrx로 자동 수집 (krx_data_cache.parquet)
    "news":                "네이버/구글 뉴스",
    "instagram":           "삼성증권 인스타그램",
    "kakao":               "삼성자산운용 카카오채널",
    "google_trends":       "구글 트렌드 + 네이버 데이터랩",
    # ── 증권사 카카오 채널 ──────────────────────────────────────────────────
    "samsung_sec_kakao":   "삼성증권 카카오채널",
    "mirae_sec_kakao":     "미래에셋증권 카카오채널",
    "kiwoom_sec_kakao":    "키움증권 카카오채널",
    "kis_sec_kakao":       "한국투자증권 카카오채널",
    "shinhan_sec_kakao":   "신한투자증권 카카오채널",
    # ── ETF 운용사 채널 (개인·경쟁사 모드 공용) ─────────────────────────
    "kodex_youtube":    "KODEX ETF 유튜브 (삼성자산운용)",
    "tiger_youtube":    "TIGER ETF 유튜브 (미래에셋자산운용)",
    "ace_youtube":      "ACE ETF 유튜브 (한국투자신탁운용)",
    "rise_youtube":     "RISE ETF 유튜브 (KB자산운용)",
    "hanaro_youtube":   "HANARO ETF 유튜브 (NH-Amundi)",
    "sol_youtube":      "SOL ETF 유튜브 (신한자산운용)",
    "tiger_event":      "TIGER ETF 이벤트 페이지",
    "ace_event":        "ACE ETF 이벤트 공지",
    "rise_event":       "RISE ETF 이벤트 페이지",
    "hanaro_event":     "HANARO ETF 이벤트 공지",
    "sol_event":        "SOL ETF 이벤트 공지",
    # ── ETF 운용사 카카오 채널 ──────────────────────────────────────────────
    "kodex_kakao":      "KODEX ETF 카카오채널 (삼성자산운용)",
    "tiger_kakao":      "TIGER ETF 카카오채널 (미래에셋자산운용)",
    "ace_kakao":        "ACE ETF 카카오채널 (한국투자신탁운용)",
    "rise_kakao":       "RISE ETF 카카오채널 (KB자산운용)",
    "hanaro_kakao":     "HANARO ETF 카카오채널 (NH-Amundi)",
    "plus_kakao":       "PLUS ETF 카카오채널 (한화자산운용)",
    "sol_kakao":        "SOL ETF 카카오채널 (신한자산운용)",
    "sol_blog":         "SOL ETF 블로그 (네이버)",
    "etf_am_news":      "ETF 운용사 뉴스 (네이버/구글)",
}

# 삼성자산운용 이벤트 페이지 → 대고객 디지털 마케팅 채널로 이동 (증권 채널 아님)
# 제거된 채널 (구조적 불가) → 제거된채널목록.md 참조


@dataclass
class ChannelResult:
    channel: str
    channel_name: str
    success: bool
    data: Any = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    error_label: Optional[str] = None
    collected_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def __post_init__(self):
        if self.error_type and not self.error_label:
            self.error_label = ERROR_TYPES.get(self.error_type, self.error_type)


BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _selenium_driver():
    """Headless Chrome driver via webdriver-manager."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_argument(f"user-agent={BROWSER_HEADERS['User-Agent']}")
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)


# ──────────────────────────────────────────────────────────────────────────────

class DataCollector:
    def __init__(
        self,
        youtube_api_key: str = "",
        naver_client_id: str = "",
        naver_client_secret: str = "",
        anthropic_api_key: str = "",
        week_start: datetime = None,
        week_end: datetime = None,
    ):
        self.youtube_api_key = youtube_api_key or os.getenv("YOUTUBE_API_KEY", "")
        self.naver_client_id = naver_client_id or os.getenv("NAVER_CLIENT_ID", "")
        self.naver_client_secret = naver_client_secret or os.getenv("NAVER_CLIENT_SECRET", "")
        self.anthropic_api_key = anthropic_api_key or os.getenv("ANTHROPIC_API_KEY", "")
        # 분석 대상 주차 범위 (None이면 최근 7일)
        self.week_start: datetime = week_start
        self.week_end: datetime = week_end or datetime.now()

    def _in_range(self, dt: datetime) -> bool:
        """날짜가 분석 대상 주차 범위 내인지 확인."""
        if self.week_start is None:
            return dt >= datetime.now() - timedelta(days=7)
        return self.week_start <= dt <= self.week_end + timedelta(days=1)

    def _fetch_og_image(self, url: str, base_domain: str = "") -> str:
        """이벤트 페이지 URL에서 OG 이미지 URL 추출. 없으면 빈 문자열."""
        if not url or not url.startswith("http"):
            return ""
        try:
            r = requests.get(url, headers=BROWSER_HEADERS, timeout=8)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            # 1순위: og:image
            for attr in [{"property":"og:image"}, {"name":"og:image"}, {"property":"twitter:image"}]:
                tag = soup.find("meta", attrs=attr)
                if tag and tag.get("content","").startswith("http"):
                    return tag["content"]
            # 2순위: 이벤트/배너/썸네일 관련 img src
            for img in soup.find_all("img"):
                src = img.get("src","") or img.get("data-src","")
                if not src: continue
                src_lower = src.lower()
                if any(kw in src_lower for kw in ["event","banner","thumb","main","poster","visual"]):
                    if src.startswith("http"):
                        return src
                    if src.startswith("/") and base_domain:
                        return base_domain.rstrip("/") + src
        except Exception:
            pass
        return ""

    def _fetch_article_text(self, url: str) -> str:
        """기사 URL에서 본문 전문 추출."""
        if not url or not url.startswith("http"):
            return ""
        try:
            r = requests.get(url, headers=BROWSER_HEADERS, timeout=8)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            # 스크립트/스타일 제거
            for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
                tag.decompose()
            # 본문 텍스트 추출 (article, main, .content 등 시도)
            for selector in ["article", "main", ".article-content", ".news-content",
                             ".article_body", "#articleBody", ".article-body"]:
                el = soup.select_one(selector)
                if el:
                    text = el.get_text(" ", strip=True)
                    if len(text) > 200:
                        return text[:2000]
            # fallback: body 전체
            return soup.get_text(" ", strip=True)[:2000]
        except Exception:
            return ""

    def _parse_pub_date(self, pub_str: str) -> Optional[datetime]:
        """RSS pubDate 문자열 → datetime."""
        if not pub_str:
            return None
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(pub_str).replace(tzinfo=None)
        except Exception:
            pass
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S"):
            try:
                return datetime.strptime(pub_str[:len(fmt)+2].strip(), fmt)
            except Exception:
                pass
        return None

    def collect_all(self, progress_callback=None) -> Dict[str, ChannelResult]:
        channels = [
            # 삼성자산운용 이벤트 (증권채널 이벤트 주 소스)
            ("samsung_fund_event", self._ch_samsung_fund_event),
            # 삼성증권
            ("samsung_youtube",    self._ch_samsung_youtube),
            ("samsung_blog",       self._ch_samsung_blog),
            # 미래에셋증권
            ("mirae_youtube",      self._ch_mirae_youtube),
            ("mirae_blog",         self._ch_mirae_blog),
            # 키움증권
            ("kiwoom_youtube",     self._ch_kiwoom_youtube),
            ("kiwoom_blog",        self._ch_kiwoom_blog),
            # 토스증권
            ("toss_youtube",       self._ch_toss_youtube),
            # 한국투자증권
            ("kis_youtube",        self._ch_kis_youtube),
            # 신한투자증권
            ("shinhan_youtube",    self._ch_shinhan_youtube),
            # KB증권
            ("kb_youtube",         self._ch_kb_youtube),
            # 공통 (KRX 데이터는 pykrx 자동 수집으로 대체됨 — krx_news/krx_trading 제거)
            ("news",               self._ch_news),
            # 추가 채널
            ("instagram",          self._ch_instagram),
            ("kakao",              self._ch_kakao),
            ("google_trends",      self._ch_google_trends),
            ("samsung_sec_kakao",  lambda: self._ch_kakao_etf("_xlfheu",  "samsung_sec_kakao")),
            ("mirae_sec_kakao",    lambda: self._ch_kakao_etf("_xgqfxkC", "mirae_sec_kakao")),
            ("kiwoom_sec_kakao",   lambda: self._ch_kakao_etf("_FZeAd",   "kiwoom_sec_kakao")),
            ("kis_sec_kakao",      lambda: self._ch_kakao_etf("_YCAes",   "kis_sec_kakao")),
            ("shinhan_sec_kakao",  lambda: self._ch_kakao_etf("_xdnLFd",  "shinhan_sec_kakao")),
        ]
        results: Dict[str, ChannelResult] = {}
        for idx, (key, func) in enumerate(channels):
            name = CHANNEL_LABELS[key]
            if progress_callback:
                progress_callback(idx + 1, len(channels), name)
            try:
                result = func()
            except Exception as e:
                logger.exception(f"[{name}] 예외")
                result = ChannelResult(key, name, False, error=str(e), error_type="UNKNOWN")
            result.channel = key
            result.channel_name = name
            if result.error_type and not result.error_label:
                result.error_label = ERROR_TYPES.get(result.error_type, result.error_type)
            results[key] = result
        return results

    @property
    def ETF_AM_CHANNELS(self):
        return [
            ("kodex_youtube",  self._ch_kodex_youtube),
            ("tiger_youtube",  self._ch_tiger_youtube),
            ("ace_youtube",    self._ch_ace_youtube),
            ("rise_youtube",   self._ch_rise_youtube),
            ("hanaro_youtube", self._ch_hanaro_youtube),
            ("sol_youtube",    self._ch_sol_youtube),
            ("tiger_event",    self._ch_tiger_event),
            ("ace_event",      self._ch_ace_event),
            ("rise_event",     self._ch_rise_event),
            ("hanaro_event",   self._ch_hanaro_event),
            ("sol_event",      self._ch_sol_event),
            ("sol_blog",       self._ch_sol_blog),
            ("kodex_blog",     self._ch_kodex_blog),
            ("etf_am_news",    self._ch_etf_am_news),
            # 카카오 채널
            ("kodex_kakao",    lambda: self._ch_kakao_etf("_UxctLxb", "kodex_kakao")),
            ("tiger_kakao",    lambda: self._ch_kakao_etf("_NVuxexb", "tiger_kakao")),
            ("ace_kakao",      lambda: self._ch_kakao_etf("_xnRfxoxj", "ace_kakao")),
            ("rise_kakao",     lambda: self._ch_kakao_etf("_lFdxhs", "rise_kakao")),
            ("hanaro_kakao",   lambda: self._ch_kakao_etf("_xlimsG", "hanaro_kakao")),
            ("plus_kakao",     lambda: self._ch_kakao_etf("_LdQkG", "plus_kakao")),
            ("sol_kakao",      lambda: self._ch_kakao_etf("_JAxkgG", "sol_kakao")),
        ]

    _yt_handle_cache: dict = {}  # @handle → UC 채널 ID 캐시 (클래스 공유)

    def _collect_etf_am(self, progress_callback=None) -> Dict[str, ChannelResult]:
        """
        ETF 운용사 채널 공통 수집 로직.
        개인 채널(mass)·경쟁사 채널(competitor) 모두 이 함수를 사용.
        ETF_AM_CHANNELS 목록에 등록된 채널만 수집.
        """
        base = [
            ("samsung_fund_event", self._ch_samsung_fund_event),  # 삼성자산운용 이벤트 (항상 포함)
            ("news",               self._ch_news),
        ]
        channels = base + list(self.ETF_AM_CHANNELS)
        results: Dict[str, ChannelResult] = {}
        for idx, (key, func) in enumerate(channels):
            name = CHANNEL_LABELS.get(key, key)
            if progress_callback:
                progress_callback(idx + 1, len(channels), name)
            try:
                result = func()
            except Exception as e:
                logger.exception(f"[{name}] 예외")
                result = ChannelResult(key, name, False, error=str(e), error_type="UNKNOWN")
            result.channel = key
            result.channel_name = name
            if result.error_type and not result.error_label:
                result.error_label = ERROR_TYPES.get(result.error_type, result.error_type)
            results[key] = result
        return results

    def collect_all_mass(self, progress_callback=None) -> Dict[str, ChannelResult]:
        """개인 채널 — ETF 운용사 전체 채널 수집 (경쟁사 포함), 개인 순매수 DiD용."""
        return self._collect_etf_am(progress_callback)

    def collect_all_competitor(self, progress_callback=None) -> Dict[str, ChannelResult]:
        """경쟁사 채널 — ETF 운용사 전체 채널 수집, 이벤트 보드 표시용."""
        return self._collect_etf_am(progress_callback)

    # ── CH1: 삼성자산운용 이벤트 페이지 ───────────────────────────────────────

    def _ch_samsung_fund_event(self) -> ChannelResult:
        ch, name = "samsung_fund_event", CHANNEL_LABELS["samsung_fund_event"]
        list_url = "https://www.samsungfund.com/etf/lounge/event.do"
        base_url = "https://www.samsungfund.com/etf/lounge/"
        try:
            r = requests.get(list_url, headers=BROWSER_HEADERS, timeout=15)
            if r.status_code == 403:
                return ChannelResult(ch, name, False, error="HTTP 403 — 접속 차단", error_type="ACCESS_BLOCKED")
            r.raise_for_status()

            soup = BeautifulSoup(r.text, "lxml")

            # 진행중 이벤트 링크 추출 (중복 제거)
            events = []
            seen_seqs = set()
            raw_combined = ""

            for a in soup.find_all("a", href=re.compile(r"event-view\.do\?seq=")):
                href = a.get("href", "")
                seq_m = re.search(r"seq=(\d+)", href)
                if not seq_m:
                    continue
                seq = seq_m.group(1)
                if seq in seen_seqs:
                    continue
                seen_seqs.add(seq)

                full_text = a.get_text(" ", strip=True)
                title = re.split(r"이벤트기간|당첨자발표", full_text)[0].strip()
                title = re.sub(r"진행중\s*", "", title).strip()

                if "당첨자" in title or "event-view-end" in href:
                    continue

                url_full = base_url + href if not href.startswith("http") else href

                # 제목에서 ETF 이름 1차 추출
                etf_m = re.findall(
                    r"[Kk][Oo][Dd][Ee][Xx]\s+((?!ETF|이벤트|매수|투자|증권사)[\w가-힣\-\+\.]+(?:\s+(?!ETF|이벤트|매수|투자)[\w가-힣\-\+\.]+)*)",
                    title
                )
                etf_m = ["KODEX " + e.strip() for e in etf_m if e.strip()]

                # 항상 본문 페이지 전체를 긁어서 ETF명 보완 + 전체 텍스트 수집
                page_full_text = title  # 최소한 제목은 포함
                og_image = ""
                try:
                    detail_r = requests.get(url_full, headers=BROWSER_HEADERS, timeout=10)
                    detail_soup = BeautifulSoup(detail_r.text, "lxml")
                    # OG 이미지 추출
                    og_tag = detail_soup.find("meta", property="og:image") or \
                             detail_soup.find("meta", attrs={"name": "og:image"})
                    if og_tag and og_tag.get("content"):
                        og_image = og_tag["content"]
                        if og_image.startswith("/"):
                            og_image = "https://www.samsungfund.com" + og_image
                    # 이벤트 배너 이미지 직접 탐색 (og 없을 때)
                    if not og_image:
                        for img in detail_soup.find_all("img"):
                            src = img.get("src","")
                            if any(kw in src.lower() for kw in ["event","banner","thumb","main"]):
                                og_image = src if src.startswith("http") else "https://www.samsungfund.com" + src
                                break
                    detail_text = detail_soup.get_text(" ", strip=True)
                    alt_texts = " ".join(
                        img.get("alt", "") for img in detail_soup.find_all("img")
                        if img.get("alt", "").strip()
                    )
                    page_full_text = detail_text + " " + alt_texts

                    # 본문에서 ETF 이름 추가 추출 (제목에서 못 찾은 것 보완)
                    etf_m2 = re.findall(
                        r"[Kk][Oo][Dd][Ee][Xx]\s+((?!ETF|이벤트|매수|투자|증권사|페이지로|분배금|search|Kodex|200액티브|fang)[\w가-힣\-\+\.]+(?:\s+(?!ETF|이벤트|매수|투자|search)[\w가-힣\-\+\.]+){0,3})",
                        page_full_text
                    )
                    extra = list(dict.fromkeys([
                        "KODEX " + e.strip() for e in etf_m2
                        if e.strip() and len(e.strip()) >= 3
                        and not re.match(r'^(ETF|Kodex|search|fang|액티브|인기|키워드)$', e.strip())
                    ]))[:3]
                    # 제목 추출과 합치되 중복 제거
                    etf_m = list(dict.fromkeys(etf_m + [e for e in extra if e not in etf_m]))[:3]
                except Exception:
                    pass

                events.append({
                    "title": title,
                    "url": url_full,
                    "etf_names": etf_m,
                    "image_url": og_image,
                    "full_text": page_full_text[:1500],  # 전체 텍스트 저장 (키워드 매칭용)
                })
                raw_combined += " " + page_full_text[:500]  # 제목 대신 본문도 포함

            if not events:
                return ChannelResult(ch, name, True,
                    data={"events": [], "event_details": [], "raw_text": ""},
                    error_label="이번 주 진행 중인 이벤트 없음")

            return ChannelResult(ch, name, True, data={
                "events": [e["title"] for e in events],
                "event_details": events,
                "raw_text": raw_combined,
                "url": list_url,
                "etf_names": list(dict.fromkeys(
                    n for e in events for n in e["etf_names"]
                )),
            })

        except requests.RequestException as e:
            code = getattr(getattr(e, "response", None), "status_code", 0)
            et = "ACCESS_BLOCKED" if code in (403, 429) else "CONNECTION_ERROR"
            return ChannelResult(ch, name, False, error=str(e), error_type=et)
        except Exception as e:
            return ChannelResult(ch, name, False, error=str(e), error_type="UNKNOWN")

    # ── 유튜브 RSS 공통 헬퍼 ─────────────────────────────────────────────────

    def _fetch_youtube_rss(self, ch: str, name: str, channel_id: str) -> ChannelResult:
        """유튜브 RSS 공통 수집 로직."""
        # 선택한 주차 기준으로 조회 범위 지정 (없으면 최근 7일) — week_start/week_end 무시하던 버그 수정
        _range_start = self.week_start or (datetime.utcnow() - timedelta(days=7))
        _range_end = self.week_end or datetime.utcnow()
        if self.youtube_api_key:
            try:
                from googleapiclient.discovery import build
                yt = build("youtube", "v3", developerKey=self.youtube_api_key)
                pub_after = _range_start.strftime("%Y-%m-%dT%H:%M:%SZ")
                pub_before = (_range_end + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
                search = yt.search().list(part="id,snippet", channelId=channel_id, type="video",
                                          publishedAfter=pub_after, publishedBefore=pub_before,
                                          maxResults=10, order="date").execute()
                videos = []
                for item in search.get("items", []):
                    vid_id = item["id"].get("videoId", "")
                    snip = item.get("snippet", {})
                    title = snip.get("title", "")
                    pub = snip.get("publishedAt", "")
                    thumb = (snip.get("thumbnails", {}).get("medium", {}).get("url")
                             or f"https://img.youtube.com/vi/{vid_id}/mqdefault.jpg")
                    is_etf = bool(re.search(r"ETF|KODEX|TIGER|코덱스|배당|채권|지수|리츠|반도체|AI", title, re.I))
                    videos.append({"title": title, "published_at": pub, "is_etf_related": is_etf,
                                   "url": f"https://youtu.be/{vid_id}",
                                   "thumbnail": thumb})
                return ChannelResult(ch, name, True, data={"source": "api", "videos": videos})
            except Exception as e:
                logger.warning(f"YouTube API 실패 → RSS: {e}")
        try:
            rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
            r = requests.get(rss_url, headers=BROWSER_HEADERS, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "xml")
            videos = []
            for entry in soup.find_all("entry")[:30]:
                title = entry.find("title").get_text(strip=True) if entry.find("title") else ""
                pub_str = entry.find("published").get_text(strip=True) if entry.find("published") else ""
                vid_url = entry.find("link")["href"] if entry.find("link") else ""
                try:
                    pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00")).replace(tzinfo=None)
                    if not self._in_range(pub_dt):
                        continue
                except Exception:
                    pass
                # 1차: 제목으로 ETF 관련 여부 판단
                is_etf_title = bool(re.search(r"ETF|KODEX|TIGER|코덱스|배당|채권|지수|리츠|반도체|AI|이벤트|프로모션|커버드콜", title, re.I))

                # 2차: 자막 읽기 (ETF 관련 가능성 있는 영상만)
                transcript_text = ""
                if is_etf_title and vid_url:
                    try:
                        video_id = vid_url.split("v=")[-1].split("&")[0] if "v=" in vid_url else vid_url.split("/")[-1]
                        from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled
                        segments = YouTubeTranscriptApi.get_transcript(video_id, languages=["ko", "en"])
                        transcript_text = " ".join(s["text"] for s in segments[:100])  # 처음 100 세그먼트
                    except Exception:
                        pass  # 자막 없으면 제목으로만 판단

                is_etf = is_etf_title
                vid_id_rss = (vid_url.split("v=")[-1].split("&")[0]
                              if "v=" in vid_url else vid_url.split("/")[-1])
                thumb_rss = f"https://img.youtube.com/vi/{vid_id_rss}/mqdefault.jpg" if vid_id_rss else ""
                videos.append({
                    "title": title,
                    "published_at": pub_str,
                    "is_etf_related": is_etf,
                    "url": vid_url,
                    "thumbnail": thumb_rss,
                    "transcript": transcript_text[:500] if transcript_text else "",
                })
            week_info = f"{self.week_start.strftime('%m/%d')}~{self.week_end.strftime('%m/%d')}" if self.week_start else "최근 7일"
            return ChannelResult(ch, name, True, data={"source": "rss", "videos": videos,
                                                        "note": f"RSS ({week_info})"})
        except requests.HTTPError as e:
            code = e.response.status_code if e.response else 0
            return ChannelResult(ch, name, False, error=f"HTTP {code}", error_type="ACCESS_BLOCKED")
        except Exception as e:
            return ChannelResult(ch, name, False, error=str(e), error_type="UNKNOWN")

    # ── CH2: 삼성증권 유튜브 ──────────────────────────────────────────────────

    def _ch_samsung_youtube(self) -> ChannelResult:
        ch, name = "samsung_youtube", CHANNEL_LABELS["samsung_youtube"]
        channel_id = "UCq7h8qFlHN5FL_T6waKZllw"

        # YouTube Data API v3 시도
        if self.youtube_api_key:
            try:
                from googleapiclient.discovery import build

                yt = build("youtube", "v3", developerKey=self.youtube_api_key)
                pub_after = self.week_start.strftime("%Y-%m-%dT%H:%M:%SZ")
                search = (
                    yt.search()
                    .list(
                        part="id,snippet",
                        channelId=channel_id,
                        type="video",
                        publishedAfter=pub_after,
                        maxResults=10,
                        order="date",
                    )
                    .execute()
                )
                videos = []
                for item in search.get("items", []):
                    vid_id = item["id"].get("videoId", "")
                    snippet = item.get("snippet", {})
                    title = snippet.get("title", "")
                    pub = snippet.get("publishedAt", "")
                    stats_r = yt.videos().list(part="statistics", id=vid_id).execute()
                    stats = stats_r["items"][0]["statistics"] if stats_r.get("items") else {}
                    is_etf = bool(re.search(r"ETF|KODEX|코덱스|배당|채권|지수|리츠", title, re.I))
                    videos.append(
                        {"title": title, "published_at": pub, "view_count": int(stats.get("viewCount", 0)),
                         "is_etf_related": is_etf, "url": f"https://youtu.be/{vid_id}"}
                    )
                return ChannelResult(ch, name, True, data={"source": "api", "videos": videos})
            except Exception as e:
                logger.warning(f"YouTube API 실패 → RSS 대체: {e}")

        # RSS 피드 대체
        try:
            rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
            r = requests.get(rss_url, headers=BROWSER_HEADERS, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "xml")
            videos = []
            for entry in soup.find_all("entry")[:50]:
                title = entry.find("title").get_text(strip=True) if entry.find("title") else ""
                pub_str = entry.find("published").get_text(strip=True) if entry.find("published") else ""
                vid_url = entry.find("link")["href"] if entry.find("link") else ""
                try:
                    pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00")).replace(tzinfo=None)
                    if not self._in_range(pub_dt):
                        continue
                except Exception:
                    pass
                is_etf = bool(re.search(r"ETF|KODEX|코덱스|배당|채권|지수|리츠", title, re.I))
                videos.append({"title": title, "published_at": pub_str, "is_etf_related": is_etf, "url": vid_url})
            note = "API 키 없음 — RSS 사용 (조회수 미포함)" if not self.youtube_api_key else "API 실패 — RSS 대체"
            week_info = f"{self.week_start.strftime('%m/%d')}~{self.week_end.strftime('%m/%d')} 기준" if self.week_start else "최근 7일 기준"
            return ChannelResult(ch, name, True, data={"source": "rss", "videos": videos, "note": f"{note} ({week_info})"})
        except requests.HTTPError as e:
            code = e.response.status_code if e.response else 0
            if code in (403, 429):
                return ChannelResult(ch, name, False, error=f"HTTP {code} — RSS 차단", error_type="ACCESS_BLOCKED")
            return ChannelResult(ch, name, False, error=str(e), error_type="CONNECTION_ERROR")
        except Exception as e:
            return ChannelResult(ch, name, False, error=str(e), error_type="UNKNOWN")

    # ── CH3: 삼성증권 인스타그램 ─────────────────────────────────────────────

    def _ch_instagram(self) -> ChannelResult:
        """Instagram — 비로그인 스크래핑 구조적 불가."""
        ch, name = "instagram", CHANNEL_LABELS["instagram"]
        return ChannelResult(
            ch, name, False,
            error="Instagram은 비로그인 스크래핑 차단 — 공식 Graph API 또는 로그인 세션 필요",
            error_type="LOGIN_REQUIRED",
        )

    # ── KODEX 공식 블로그 (samsungfundblog.com) ───────────────────────────────

    def _ch_kodex_blog(self) -> ChannelResult:
        ch, name = "kodex_blog", CHANNEL_LABELS["kodex_blog"]
        try:
            r = requests.get("https://samsungfundblog.com", headers=BROWSER_HEADERS, timeout=10)
            r.raise_for_status()
            from bs4 import BeautifulSoup as _BS
            soup = _BS(r.text, "lxml")

            articles, raw_texts = [], []
            for a in soup.find_all("a", href=True):
                href = a.get("href", "")
                title = a.get_text(strip=True)
                if "/archives/" not in href or len(title) < 10:
                    continue
                # 날짜는 개별 포스트 메타에서 — 목록에서 안 보이면 포스트 직접 확인 생략하고 이번주 판단
                # 일단 제목으로 ETF 관련 여부 판단
                if not any(k in title for k in ["ETF","KODEX","펀드","투자","채권","주식"]):
                    continue
                articles.append({"title": title[:80], "url": href, "thumbnail": "", "description": ""})
                raw_texts.append(title)
                if len(articles) >= 10:
                    break

            # 날짜 필터: 각 포스트 published_time 확인 (최대 5개만)
            week_articles = []
            for art in articles[:5]:
                try:
                    pr = requests.get(art["url"], headers=BROWSER_HEADERS, timeout=8)
                    ps = _BS(pr.text, "lxml")
                    pub_meta = ps.find("meta", property="article:published_time")
                    thumb_meta = ps.find("meta", property="og:image")
                    if thumb_meta:
                        art["thumbnail"] = thumb_meta.get("content", "")
                    if pub_meta:
                        from datetime import timezone
                        pub_str = pub_meta.get("content", "")
                        pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00")).replace(tzinfo=None)
                        pub_dt_local = pub_dt + timedelta(hours=9)
                        if self.week_start <= pub_dt_local <= self.week_end:
                            week_articles.append(art)
                except Exception:
                    pass

            if not week_articles:
                return ChannelResult(ch, name, True,
                    data={"articles": [], "raw_text": ""},
                    error_label=f"이번 주 KODEX 블로그 게시물 없음 (전체 {len(articles)}개)")

            return ChannelResult(ch, name, True,
                data={"articles": week_articles,
                      "raw_text": " / ".join(a["title"] for a in week_articles)[:400]})
        except Exception as e:
            return ChannelResult(ch, name, False, error=str(e), error_type="UNKNOWN")

    # ── CH4: 삼성증권 블로그 (네이버 RSS) ───────────────────────────────────

    def _ch_samsung_blog(self) -> ChannelResult:
        return self._ch_naver_blog_base("samsung_blog", CHANNEL_LABELS["samsung_blog"],
                                        "https://rss.blog.naver.com/samsung_fn.xml")

    def _ch_naver_blog_base(self, ch: str, name: str, rss_url: str) -> ChannelResult:
        """네이버 블로그 RSS 공통 수집 로직."""
        try:
            r = requests.get(rss_url, headers=BROWSER_HEADERS, timeout=15)
            if r.status_code == 403:
                # 모바일 API 폴백: rss.blog.naver.com/ID.xml → m.blog.naver.com/PostList
                blog_id_m = re.search(r"blog\.naver\.com/([^.]+)\.xml", rss_url)
                if blog_id_m:
                    blog_id = blog_id_m.group(1)
                    mobile_url = f"https://m.blog.naver.com/PostList.naver?blogId={blog_id}&widgetTypeCall=true&noTrackingCode=true"
                    try:
                        rm = requests.get(mobile_url, headers={
                            **BROWSER_HEADERS,
                            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
                            "Referer": "https://m.blog.naver.com/",
                        }, timeout=15)
                        if rm.status_code == 200:
                            soup_m = BeautifulSoup(rm.text, "lxml")
                            posts = []
                            for a in soup_m.select("a[href*='PostView'], a.item_subject, .item_text a")[:15]:
                                title = a.get_text(strip=True)
                                href  = a.get("href", "")
                                if not title or not href:
                                    continue
                                link = href if href.startswith("http") else f"https://blog.naver.com{href}"
                                is_etf = bool(re.search(r"ETF|KODEX|펀드|배당|채권|이벤트|프로모션", title, re.I))
                                posts.append({"title": title, "link": link, "description": "", "pub_date": "", "is_etf_related": is_etf})
                            if posts:
                                return ChannelResult(ch, name, True, data={"posts": posts[:10], "source": "mobile_web", "note": "모바일 API 폴백"})
                    except Exception as em:
                        logger.debug(f"Naver blog 모바일 폴백 실패 ({blog_id}): {em}")
                return ChannelResult(ch, name, False, error="HTTP 403 — RSS 접근 차단", error_type="ACCESS_BLOCKED")
            r.raise_for_status()

            soup = BeautifulSoup(r.text, "xml")
            items = soup.find_all("item")
            if not items:
                # lxml fallback
                soup = BeautifulSoup(r.text, "lxml")
                items = soup.find_all("item")

            posts = []

            for item in items[:30]:
                title   = item.find("title").get_text(strip=True)       if item.find("title")       else ""
                link    = item.find("link").get_text(strip=True)         if item.find("link")         else ""
                desc    = item.find("description").get_text(strip=True)  if item.find("description")  else ""
                pub_str = item.find("pubDate").get_text(strip=True)      if item.find("pubDate")      else ""

                pub_dt = self._parse_pub_date(pub_str)
                if pub_dt is not None and not self._in_range(pub_dt):
                    continue

                # 포스트 전문 읽기
                full_text = self._fetch_article_text(link) if link else ""
                content = full_text if full_text else desc

                is_etf = bool(re.search(r"ETF|KODEX|코덱스|펀드|배당|채권|지수|리츠|커버드콜|이벤트|프로모션", content, re.I))

                posts.append({
                    "title": title,
                    "link": link,
                    "description": content[:1000],
                    "pub_date": pub_str,
                    "is_etf_related": is_etf,
                })

            week_info = f"{self.week_start.strftime('%m/%d')}~{self.week_end.strftime('%m/%d')}" if self.week_start else "최근 7일"
            return ChannelResult(ch, name, True, data={
                "posts": posts[:10],
                "source": "rss",
                "note": f"네이버 블로그 RSS ({week_info}) — {len(posts)}건",
            })

        except requests.HTTPError as e:
            return ChannelResult(ch, name, False, error=f"HTTP 오류: {e}", error_type="ACCESS_BLOCKED")
        except Exception as e:
            return ChannelResult(ch, name, False, error=str(e), error_type="UNKNOWN")

    # ── CH5: 삼성증권 홈페이지 이벤트 탭 ────────────────────────────────────

    def _ch_samsung_pop_event(self) -> ChannelResult:
        ch, name = "samsung_pop_event", CHANNEL_LABELS["samsung_pop_event"]
        # 모바일 URL과 이벤트 전용 URL 먼저 시도
        direct_urls = [
            "https://www.samsungpop.com/mobile/event/eventList.do",
            "https://m.samsungpop.com/common/event/eventList.do",
            "https://www.samsungpop.com/common/index.do#event",
            "https://www.samsungpop.com",
        ]
        for url in direct_urls:
            try:
                r = requests.get(url, headers={
                    **BROWSER_HEADERS,
                    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1" if "mobile" in url or url.startswith("https://m.") else BROWSER_HEADERS["User-Agent"],
                }, timeout=12)
                if r.status_code not in (200,):
                    continue
                soup_chk = BeautifulSoup(r.text, "lxml")
                _events_pre = []
                for a in soup_chk.find_all("a", href=re.compile(r"event|Event", re.I)):
                    txt = a.get_text(strip=True)
                    if txt and len(txt) > 5 and re.search(r"이벤트|ETF|KODEX|프로모션", txt, re.I):
                        href = a.get("href", "")
                        full_href = href if href.startswith("http") else "https://www.samsungpop.com" + href
                        _events_pre.append({"title": txt[:200], "url": full_href})
                if _events_pre:
                    return ChannelResult(ch, name, True, data={
                        "events": [e["title"] for e in _events_pre],
                        "event_details": _events_pre,
                        "raw_text": " ".join(e["title"] for e in _events_pre),
                        "source": "mobile_direct",
                    })
            except Exception as e:
                logger.debug(f"samsung_pop_event direct 시도 실패 ({url}): {e}")

        url = "https://www.samsungpop.com"
        try:
            r = requests.get(url, headers=BROWSER_HEADERS, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")

            # SPA 탐지
            scripts = " ".join(s.get("src", "") for s in soup.find_all("script") if s.get("src"))
            is_spa = any(kw in scripts for kw in ["chunk", "bundle", "react", "vue", "app.js"])

            etf_text = soup.get_text()
            has_content = bool(re.search(r"이벤트|event|ETF|KODEX", etf_text, re.I))

            if is_spa or not has_content:
                try:
                    driver = _selenium_driver()
                    driver.get(url)
                    time.sleep(5)
                    # 이벤트 탭 클릭 시도
                    from selenium.webdriver.common.by import By
                    try:
                        els = driver.find_elements(By.XPATH, "//*[contains(text(),'이벤트')]")
                        if els:
                            els[0].click()
                            time.sleep(3)
                    except Exception:
                        pass
                    page_src = driver.page_source
                    driver.quit()

                    soup2 = BeautifulSoup(page_src, "lxml")
                    events = []
                    for tag in soup2.find_all(string=re.compile(r"ETF|KODEX|이벤트", re.I)):
                        p = tag.parent
                        if p and p.name not in ("script", "style"):
                            txt = p.get_text(strip=True)[:300]
                            if txt:
                                events.append(txt)

                    if events:
                        return ChannelResult(ch, name, True, data={"events": list(dict.fromkeys(events))[:10], "source": "selenium"})
                    return ChannelResult(ch, name, False,
                        error="삼성증권 홈페이지 Selenium 렌더링 후에도 이벤트 콘텐츠 없음 (봇 차단)",
                        error_type="BOT_DETECTED")
                except ImportError:
                    return ChannelResult(ch, name, False,
                        error="Selenium 미설치 — 삼성증권 홈페이지는 SPA라 정적 수집 불가",
                        error_type="DEPENDENCY_MISSING")
                except Exception as se:
                    logger.debug(f"samsung_pop_event Selenium 실패: {se}")
                    return ChannelResult(ch, name, False,
                        error=f"삼성증권 홈페이지 Selenium 실패: {se}",
                        error_type="BOT_DETECTED")

            # 정적 콘텐츠 파싱
            events = []
            for tag in soup.find_all(string=re.compile(r"ETF|KODEX|이벤트", re.I)):
                p = tag.parent
                if p and p.name not in ("script", "style"):
                    events.append(p.get_text(strip=True)[:200])
            return ChannelResult(ch, name, True, data={"events": list(dict.fromkeys(events))[:10], "source": "static"})

        except requests.HTTPError as e:
            return ChannelResult(ch, name, False,
                error=f"삼성증권 홈페이지 HTTP 오류: {e}",
                error_type="HTTP_ERROR")
        except Exception as e:
            return ChannelResult(ch, name, False,
                error=f"삼성증권 홈페이지 접근 실패 (SPA 봇 차단): {e}",
                error_type="BOT_DETECTED")

    # ── CH6: 삼성증권 카카오톡 채널 ──────────────────────────────────────────

    def _ch_kakao(self) -> ChannelResult:
        """카카오 Plus 채널 — rocket-web 내부 API로 게시물 직접 수집."""
        ch, name = "kakao", CHANNEL_LABELS["kakao"]
        channel_id = "_UxctLxb"  # 삼성자산운용 카카오 채널
        api_url = f"https://pf.kakao.com/rocket-web/web/profiles/{channel_id}/posts?includePinnedPost=true"
        try:
            r = requests.get(api_url, headers={
                **BROWSER_HEADERS,
                "Referer": f"https://pf.kakao.com/{channel_id}/posts",
                "Accept": "application/json, text/plain, */*",
            }, timeout=10)
            r.raise_for_status()
            data = r.json()
            items = data.get("items", [])
            if not items:
                return ChannelResult(ch, name, False, error="게시물 없음", error_type="NO_DATA")

            # 주차 필터
            cutoff_ts = int(self.week_start.timestamp() * 1000)
            end_ts    = int(self.week_end.timestamp() * 1000)

            articles, raw_texts = [], []
            for item in items:
                pub_ts = item.get("published_at", 0)
                if pub_ts < cutoff_ts or pub_ts > end_ts:
                    continue
                title = item.get("title") or item.get("text", "")[:60] or "카카오 게시물"
                post_url = f"https://pf.kakao.com/{channel_id}/posts/{item.get('id','')}"
                thumbnail = ""
                media = item.get("media", [])
                if media:
                    thumbnail = media[0].get("medium_url") or media[0].get("url", "")
                text_body = item.get("text", "") or item.get("title", "")
                articles.append({
                    "title": title[:80],
                    "url": post_url,
                    "thumbnail": thumbnail,
                    "published_at": str(pub_ts),
                    "description": text_body[:200],
                })
                raw_texts.append(f"{title} {text_body[:100]}")

            if not articles:
                return ChannelResult(ch, name, True,
                    data={"articles": [], "raw_text": ""},
                    error_label=f"이번 주 카카오 게시물 없음 (전체 {len(items)}개 중)")

            raw_text = " / ".join(raw_texts[:5])
            return ChannelResult(ch, name, True,
                data={"articles": articles, "raw_text": raw_text[:500]})

        except Exception as e:
            return ChannelResult(ch, name, False, error=str(e), error_type="UNKNOWN")

    def _ch_kakao_etf(self, channel_id: str, ch_key: str) -> ChannelResult:
        """ETF 운용사 카카오 채널 공통 수집 (rocket-web 내부 API)."""
        name = CHANNEL_LABELS.get(ch_key, ch_key)
        api_url = f"https://pf.kakao.com/rocket-web/web/profiles/{channel_id}/posts?includePinnedPost=true"
        try:
            r = requests.get(api_url, headers={
                **BROWSER_HEADERS,
                "Referer": f"https://pf.kakao.com/{channel_id}/posts",
                "Accept": "application/json, text/plain, */*",
            }, timeout=10)
            r.raise_for_status()
            items = r.json().get("items", [])
            if not items:
                return ChannelResult(ch_key, name, False, error="게시물 없음", error_type="NO_DATA")

            cutoff_ts = int(self.week_start.timestamp() * 1000)
            end_ts    = int(self.week_end.timestamp() * 1000)

            articles, raw_texts = [], []
            for item in items:
                pub_ts = item.get("published_at", 0)
                if pub_ts < cutoff_ts or pub_ts > end_ts:
                    continue
                title = item.get("title") or item.get("text", "")[:60] or "카카오 게시물"
                post_url = f"https://pf.kakao.com/{channel_id}/posts/{item.get('id','')}"
                media = item.get("media", [])
                thumbnail = media[0].get("medium_url", "") if media else ""
                text_body = item.get("text", "") or ""
                articles.append({
                    "title":        title[:80],
                    "url":          post_url,
                    "thumbnail":    thumbnail,
                    "published_at": str(pub_ts),
                    "description":  text_body[:200],
                })
                raw_texts.append(f"{title} {text_body[:80]}")

            if not articles:
                return ChannelResult(ch_key, name, True,
                    data={"articles": [], "raw_text": ""},
                    error_label=f"이번 주 게시물 없음 (전체 {len(items)}개)")

            return ChannelResult(ch_key, name, True,
                data={"articles": articles, "raw_text": " / ".join(raw_texts[:5])[:500]})
        except Exception as e:
            return ChannelResult(ch_key, name, False, error=str(e), error_type="UNKNOWN")

    # ── CH9: 구글 트렌드 ─────────────────────────────────────────────────────

    def _ch_google_trends(self) -> ChannelResult:
        """
        구글 트렌드 대체 수집 (pytrends 봇 차단 우회):
        1) 구글 트렌드 RSS — 한국 실시간 인기 검색어, ETF/KODEX 포함 여부 감지
        2) 네이버 데이터랩 — KODEX/ETF/TIGER 주간 검색량 트렌드 (권한 있을 때)
        """
        ch, name = "google_trends", CHANNEL_LABELS["google_trends"]
        ETF_KEYWORDS = {"ETF", "KODEX", "코덱스", "TIGER", "ACE", "RISE", "상장지수펀드"}

        results = {}
        raw_parts = []

        # ── 1) 구글 트렌드 RSS (실시간 인기 검색어) ─────────────────────────
        try:
            import xml.etree.ElementTree as ET
            r = requests.get(
                "https://trends.google.com/trending/rss?geo=KR",
                headers={**BROWSER_HEADERS, "Accept": "application/rss+xml,application/xml"},
                timeout=10,
            )
            root = ET.fromstring(r.text)
            trending = []
            etf_hits = []
            for item in root.findall(".//item"):
                title = item.findtext("title", "").strip()
                trending.append(title)
                if any(k.lower() in title.lower() for k in ETF_KEYWORDS):
                    etf_hits.append(title)
            results["google_trending"] = {
                "keywords": trending,
                "etf_hits": etf_hits,
                "etf_in_trend": bool(etf_hits),
            }
            if etf_hits:
                raw_parts.append(f"구글 인기검색어 ETF 감지: {', '.join(etf_hits)}")
            else:
                raw_parts.append(f"구글 인기검색어 {len(trending)}개 (ETF 미포함)")
        except Exception as e:
            results["google_trending"] = {"error": str(e)}

        # ── 2) 네이버 데이터랩 (검색어 트렌드) ──────────────────────────────
        naver_id  = self.naver_client_id  if hasattr(self, "naver_client_id")  else os.getenv("NAVER_CLIENT_ID", "")
        naver_sec = self.naver_client_secret if hasattr(self, "naver_client_secret") else os.getenv("NAVER_CLIENT_SECRET", "")
        if naver_id and naver_sec:
            try:
                import json as _json
                from datetime import timedelta as _td
                end_dt   = self.week_end
                start_dt = end_dt - _td(days=28)
                body = _json.dumps({
                    "startDate": start_dt.strftime("%Y-%m-%d"),
                    "endDate":   end_dt.strftime("%Y-%m-%d"),
                    "timeUnit":  "week",
                    "keywordGroups": [
                        {"groupName": "KODEX", "keywords": ["KODEX", "코덱스"]},
                        {"groupName": "ETF",   "keywords": ["ETF", "상장지수펀드"]},
                        {"groupName": "TIGER", "keywords": ["TIGER ETF", "타이거ETF"]},
                        {"groupName": "ACE",   "keywords": ["ACE ETF"]},
                    ],
                }, ensure_ascii=False)
                resp = requests.post(
                    "https://openapi.naver.com/v1/datalab/search",
                    headers={
                        "X-Naver-Client-Id":     naver_id,
                        "X-Naver-Client-Secret": naver_sec,
                        "Content-Type":          "application/json; charset=UTF-8",
                    },
                    data=body.encode("utf-8"),
                    timeout=10,
                )
                if resp.status_code == 200:
                    datalab = resp.json()
                    trend_data = {}
                    for result in datalab.get("results", []):
                        kw_name = result["title"]
                        series  = result.get("data", [])
                        if series:
                            latest = series[-1]
                            prev   = series[-2] if len(series) >= 2 else latest
                            chg    = round(latest["ratio"] - prev["ratio"], 1)
                            trend_data[kw_name] = {
                                "ratio":      latest["ratio"],
                                "prev_ratio": prev["ratio"],
                                "change":     chg,
                                "weekly":     [d["ratio"] for d in series],
                            }
                    results["naver_datalab"] = trend_data
                    summaries = [f"{k} {v['ratio']:.0f}({'+' if v['change']>=0 else ''}{v['change']:.0f})"
                                 for k, v in trend_data.items()]
                    raw_parts.append("네이버 검색트렌드: " + " / ".join(summaries))
                else:
                    results["naver_datalab"] = {"error": f"HTTP {resp.status_code} — 데이터랩 권한 미부여"}
            except Exception as e:
                results["naver_datalab"] = {"error": str(e)}

        if not results:
            return ChannelResult(ch, name, False, error="수집 실패", error_type="UNKNOWN")

        detected = bool(results.get("google_trending", {}).get("etf_hits")) or \
                   bool(results.get("naver_datalab") and "error" not in results["naver_datalab"])
        return ChannelResult(ch, name, detected,
            data={"trends": results, "raw_text": " | ".join(raw_parts)})

    # ── CH10: 퇴직연금 상품가이드 PDF ────────────────────────────────────────

    def _ch_pension_pdf(self) -> ChannelResult:
        """퇴직연금 가이드 — SPA 직접 불가, 대체 엔드포인트 시도."""
        ch, name = "pension_pdf", CHANNEL_LABELS["pension_pdf"]
        # 1. 삼성증권 모바일 퇴직연금 페이지 (SPA보다 가벼운 경우 있음)
        alt_urls = [
            ("https://www.samsungpop.com/mobile/retire.do", "mobile_retire"),
            ("https://www.samsungpop.com/retire/product.do", "retire_product"),
            ("https://www.samsungpop.com/api/v1/retire/product/list", "retire_api"),
        ]
        for url, label in alt_urls:
            try:
                r = requests.get(url, headers=BROWSER_HEADERS, timeout=10)
                if r.status_code == 200 and len(r.text) > 500:
                    # PDF 링크 추출
                    soup = BeautifulSoup(r.text, "lxml")
                    pdfs = []
                    for a in soup.find_all("a", href=re.compile(r"\.pdf|pdf.*download|fileDown", re.I)):
                        href = a.get("href", "")
                        text = a.get_text(strip=True)[:100]
                        if href:
                            full = href if href.startswith("http") else "https://www.samsungpop.com" + href
                            pdfs.append({"title": text or "퇴직연금 가이드", "url": full})
                    if pdfs:
                        return ChannelResult(ch, name, True, data={"pdfs": pdfs, "source": label})
                    # 텍스트에서 ETF/펀드 관련 내용이 있으면 성공
                    if re.search(r"ETF|펀드|퇴직연금|운용상품", r.text, re.I):
                        items = []
                        for tag in BeautifulSoup(r.text, "lxml").find_all(["li", "td", "div"], string=re.compile(r"ETF|펀드|KODEX")):
                            t = tag.get_text(strip=True)[:200]
                            if t:
                                items.append(t)
                        if items:
                            return ChannelResult(ch, name, True, data={"products": items[:10], "source": label, "url": url})
            except Exception as e:
                logger.debug(f"pension_pdf alt 시도 실패 ({label}): {e}")

        # 2. 구글 검색으로 삼성증권 퇴직연금 PDF 찾기
        try:
            q = "삼성증권 퇴직연금 ETF 상품가이드 filetype:pdf"
            r = requests.get(
                f"https://news.google.com/rss/search?q={requests.utils.quote(q)}&hl=ko&gl=KR&ceid=KR:ko",
                headers=BROWSER_HEADERS, timeout=10,
            )
            soup = BeautifulSoup(r.text, "xml")
            items = []
            for item in soup.find_all("item")[:5]:
                title = item.find("title").get_text(strip=True) if item.find("title") else ""
                link  = item.find("link").get_text(strip=True) if item.find("link") else ""
                if title and link:
                    items.append({"title": title, "url": link})
            if items:
                return ChannelResult(ch, name, True, data={"pdfs": items, "source": "google_search", "note": "구글 검색 결과"})
        except Exception as e:
            logger.debug(f"pension_pdf 구글 검색 실패: {e}")

        return ChannelResult(ch, name, False,
            error="퇴직연금 가이드 PDF — 삼성증권 SPA 구조, 대체 엔드포인트 전체 실패 (로그인 필요)",
            error_type="LOGIN_REQUIRED")

    # ── CH11: 네이버/구글 뉴스 ──────────────────────────────────────────────

    def _ch_news(self) -> ChannelResult:
        ch, name = "news", CHANNEL_LABELS["news"]
        articles = []
        source_used = None

        # ── 네이버 뉴스 직접 스크래핑 (API 없이) ──
        naver_keywords = [
            "삼성증권 KODEX 이벤트",
            "삼성증권 ETF 이벤트",
            "삼성증권 ETF 프로모션",
            "삼성자산운용 삼성증권",
        ]
        for kw in naver_keywords:
            try:
                url = f"https://search.naver.com/search.naver?where=news&query={requests.utils.quote(kw)}&sort=1"
                r = requests.get(url, headers=BROWSER_HEADERS, timeout=10)
                if r.status_code == 200:
                    soup = BeautifulSoup(r.text, "lxml")
                    for item in soup.select(".news_area, .list_news .bx")[:5]:
                        title_tag = item.select_one(".news_tit, a.title")
                        link_tag  = item.select_one(".news_tit, a.title")
                        date_tag  = item.select_one(".info_group .info, .sub_txt .info")
                        if not title_tag:
                            continue
                        title = title_tag.get_text(strip=True)
                        link  = title_tag.get("href", "") if title_tag.name == "a" else (link_tag.get("href","") if link_tag else "")
                        pub_str = date_tag.get_text(strip=True) if date_tag else ""
                        pub_dt = self._parse_pub_date(pub_str)
                        if pub_dt and not self._in_range(pub_dt):
                            continue
                        articles.append({
                            "title": title, "description": "",
                            "link": link, "pub_date": pub_str,
                            "keyword": kw, "source": "naver_scrape",
                        })
                source_used = "naver_scrape"
            except Exception as e:
                logger.debug(f"네이버 뉴스 스크래핑 실패 ({kw}): {e}")

        # ── 네이버 뉴스 API (키 있을 때) ──
        if self.naver_client_id and self.naver_client_secret:
            keywords = [
                "삼성증권 KODEX 이벤트",
                "삼성증권 ETF 이벤트",
                "삼성증권 ETF 프로모션",
                "삼성자산운용 삼성증권 이벤트",
            ]
            try:
                for kw in keywords:
                    r = requests.get(
                        "https://openapi.naver.com/v1/search/news.json",
                        headers={**BROWSER_HEADERS,
                                 "X-Naver-Client-Id": self.naver_client_id,
                                 "X-Naver-Client-Secret": self.naver_client_secret},
                        params={"query": kw, "display": 20, "sort": "date"},
                        timeout=10,
                    )
                    r.raise_for_status()
                    for item in r.json().get("items", []):
                        pub_dt = self._parse_pub_date(item.get("pubDate", ""))
                        if pub_dt and not self._in_range(pub_dt):
                            continue
                        articles.append({
                            "title": re.sub(r"<[^>]+>", "", item.get("title", "")),
                            "description": re.sub(r"<[^>]+>", "", item.get("description", ""))[:300],
                            "link": item.get("link", ""),
                            "pub_date": item.get("pubDate", ""),
                            "keyword": kw,
                        })
                source_used = "naver_api"
            except Exception as e:
                logger.warning(f"네이버 뉴스 API 실패: {e}")

        # 구글 뉴스 RSS 대체
        if not articles:
            queries = [
                "KODEX+이벤트+증권",           # 광범위 — 어느 증권사든 KODEX 이벤트
                "KODEX+ETF+이벤트",            # KODEX ETF 전반 이벤트
                "삼성증권+KODEX+이벤트",
                "키움증권+KODEX+이벤트",
                "미래에셋증권+KODEX+이벤트",
                "한국투자증권+KODEX+이벤트",
                "증권+KODEX+앱+이벤트",        # 앱 내 이벤트 기사
                "KODEX+ETF+신규상장",
                "KODEX+순자산+돌파",           # AUM 마일스톤 케이스
                "삼성자산운용+ETF+출시",
            ]
            for q in queries:
                try:
                    r = requests.get(
                        f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko",
                        headers=BROWSER_HEADERS, timeout=15,
                    )
                    r.raise_for_status()
                    soup = BeautifulSoup(r.text, "xml")
                    for item in soup.find_all("item")[:10]:
                        pub_str = item.find("pubDate").get_text(strip=True) if item.find("pubDate") else ""
                        pub_dt = self._parse_pub_date(pub_str)
                        if pub_dt and not self._in_range(pub_dt):
                            continue
                        link = item.find("link").get_text(strip=True) if item.find("link") else ""
                        title = item.find("title").get_text(strip=True) if item.find("title") else ""
                        # 기사 전문 읽기
                        full_text = self._fetch_article_text(link)
                        articles.append({
                            "title": title,
                            "description": full_text if full_text else
                                          (item.find("description").get_text(strip=True)[:500] if item.find("description") else ""),
                            "link": link,
                            "pub_date": pub_str,
                            "keyword": q.replace("+", " "),
                        })
                    source_used = "google_rss"
                    if articles:
                        break  # 충분히 수집되면 다음 쿼리 불필요
                except Exception as e:
                    logger.debug(f"Google RSS 실패 ({q}): {e}")

        note = (
            "네이버 뉴스 API" if source_used == "naver_api"
            else ("구글 뉴스 RSS (네이버 API 키 없음)" if not self.naver_client_id else "구글 뉴스 RSS (네이버 API 실패)")
        )

        if articles:
            return ChannelResult(ch, name, True, data={"articles": articles[:30], "source": source_used, "note": note})

        return ChannelResult(
            ch, name, False,
            error=f"뉴스 수집 실패 ({note}) — 네이버 검색 API 키 설정 권장",
            error_type="API_KEY_REQUIRED" if not self.naver_client_id else "CONNECTION_ERROR",
        )

    # ── 미래에셋증권 채널 ──────────────────────────────────────────────────────

    def _ch_mirae_youtube(self) -> ChannelResult:
        return self._fetch_youtube_rss(
            "mirae_youtube", CHANNEL_LABELS["mirae_youtube"],
            "UCZS9wEZ4itPbBZk_sqccXfw"
        )

    def _ch_mirae_blog(self) -> ChannelResult:
        return self._ch_naver_blog_base(
            "mirae_blog", CHANNEL_LABELS["mirae_blog"],
            "https://rss.blog.naver.com/how2invest.xml"
        )

    # ── 키움증권 채널 ────────────────────────────────────────────────────────

    def _ch_kiwoom_youtube(self) -> ChannelResult:
        return self._fetch_youtube_rss(
            "kiwoom_youtube", CHANNEL_LABELS["kiwoom_youtube"],
            "UCZW1d7B2nYqQUiTiOnkirrQ"
        )

    def _ch_kiwoom_blog(self) -> ChannelResult:
        return self._ch_naver_blog_base(
            "kiwoom_blog", CHANNEL_LABELS["kiwoom_blog"],
            "https://rss.blog.naver.com/kiwoomhero.xml"
        )

    # ── 토스증권 채널 ────────────────────────────────────────────────────────

    def _ch_toss_youtube(self) -> ChannelResult:
        return self._fetch_youtube_rss(
            "toss_youtube", CHANNEL_LABELS["toss_youtube"],
            "UCW_P8DTCnlDcUHRfGFwRRLA"
        )

    # ── 한국투자증권 채널 ────────────────────────────────────────────────────

    def _ch_kis_youtube(self) -> ChannelResult:
        return self._fetch_youtube_rss(
            "kis_youtube", CHANNEL_LABELS["kis_youtube"],
            "UCU6f21g_qaJk6rkX-IF6X2g"
        )

    # ── 신한투자증권 채널 ────────────────────────────────────────────────────

    def _ch_shinhan_youtube(self) -> ChannelResult:
        return self._fetch_youtube_rss(
            "shinhan_youtube", CHANNEL_LABELS["shinhan_youtube"],
            "UCYzZm9_nasRW6npCkjlTjKQ"
        )

    # ── KB증권 채널 ───────────────────────────────────────────────────────────

    def _ch_kb_youtube(self) -> ChannelResult:
        return self._fetch_youtube_rss(
            "kb_youtube", CHANNEL_LABELS["kb_youtube"],
            "UCD0k4Kq7SJROxxV-9N5v8IA"
        )

    # ── ETF 운용사 채널 (개인·경쟁사 모드 공용) ─────────────────────────────────

    def _resolve_youtube_handle(self, handle: str) -> str:
        """YouTube @handle → UC... 채널 ID 자동 추출 (캐시 사용)."""
        if handle in DataCollector._yt_handle_cache:
            return DataCollector._yt_handle_cache[handle]

        _PATTERNS = [
            r'"channelId"\s*:\s*"(UC[^"]+)"',
            r'"externalId"\s*:\s*"(UC[^"]+)"',
            r'"browseId"\s*:\s*"(UC[^"]+)"',
            r'channel/(UC[A-Za-z0-9_\-]{22,})',
            r'"id"\s*:\s*"(UC[A-Za-z0-9_\-]{22,})"',
        ]
        _handle = handle.lstrip('@')
        _try_urls = [
            f"https://www.youtube.com/@{_handle}",
            f"https://www.youtube.com/@{_handle}/about",
            f"https://www.youtube.com/@{_handle}/videos",
            f"https://www.youtube.com/c/{_handle}",
        ]
        for url in _try_urls:
            try:
                r = requests.get(url, headers=BROWSER_HEADERS, timeout=15)
                if r.status_code not in (200, 301, 302):
                    continue
                for pat in _PATTERNS:
                    m = re.search(pat, r.text)
                    if m:
                        channel_id = m.group(1)
                        DataCollector._yt_handle_cache[handle] = channel_id
                        return channel_id
            except Exception as e:
                logger.debug(f"YouTube handle 시도 실패 ({url}): {e}")

        logger.warning(f"YouTube @handle 해석 최종 실패 ({handle})")
        return ""

    def _resolve_youtube_search(self, query: str) -> str:
        """YouTube 검색으로 채널 ID 추출 (handle 해석 실패 시 2차 폴백)."""
        try:
            url = f"https://www.youtube.com/results?search_query={requests.utils.quote(query)}&sp=EgIQAg%3D%3D"
            r = requests.get(url, headers=BROWSER_HEADERS, timeout=15)
            if r.status_code != 200:
                return ""
            for pat in [r'"channelId"\s*:\s*"(UC[^"]+)"', r'"externalId"\s*:\s*"(UC[^"]+)"']:
                m = re.search(pat, r.text)
                if m:
                    cid = m.group(1)
                    logger.info(f"YouTube 검색으로 채널 ID 발견 ({query}): {cid}")
                    return cid
        except Exception as e:
            logger.debug(f"YouTube 검색 채널 ID 실패 ({query}): {e}")
        return ""

    def _ch_kodex_youtube(self) -> ChannelResult:
        return self._fetch_youtube_rss(
            "kodex_youtube", CHANNEL_LABELS["kodex_youtube"],
            "UCohjHDdtYtoKYtiCSVFoHAw"
        )

    def _ch_tiger_youtube(self) -> ChannelResult:
        channel_id = self._resolve_youtube_handle("tiger_etf")
        if not channel_id:
            channel_id = self._resolve_youtube_search("TIGER ETF 미래에셋자산운용")
        if not channel_id:
            return ChannelResult("tiger_youtube", CHANNEL_LABELS["tiger_youtube"], True,
                                 data={"videos": [], "note": "@tiger_etf 채널 ID 추출 실패"},
                                 error_label="TIGER YouTube 채널 ID 추출 실패 — 일시적 YouTube 차단")
        return self._fetch_youtube_rss("tiger_youtube", CHANNEL_LABELS["tiger_youtube"], channel_id)

    def _ch_ace_youtube(self) -> ChannelResult:
        return self._fetch_youtube_rss(
            "ace_youtube", CHANNEL_LABELS["ace_youtube"],
            "UCnuyNitL5SIfBJvTJcdDNLQ"
        )

    def _ch_rise_youtube(self) -> ChannelResult:
        return self._fetch_youtube_rss(
            "rise_youtube", CHANNEL_LABELS["rise_youtube"],
            "UCZ9jozYXT6BXl2TchjNH8hw"
        )

    def _ch_hanaro_youtube(self) -> ChannelResult:
        return self._fetch_youtube_rss(
            "hanaro_youtube", CHANNEL_LABELS["hanaro_youtube"],
            "UCnK3ANYTFZnF8pkEh3_cOgg"
        )

    def _ch_sol_youtube(self) -> ChannelResult:
        channel_id = self._resolve_youtube_handle("SOL_ETF")
        if not channel_id:
            channel_id = self._resolve_youtube_search("SOL ETF 신한자산운용")
        if not channel_id:
            return ChannelResult("sol_youtube", CHANNEL_LABELS["sol_youtube"], True,
                                 data={"videos": [], "note": "@SOL_ETF 채널 ID 추출 실패"},
                                 error_label="SOL YouTube 채널 ID 추출 실패 — 일시적 YouTube 차단")
        return self._fetch_youtube_rss("sol_youtube", CHANNEL_LABELS["sol_youtube"], channel_id)

    def _ch_tiger_event(self) -> ChannelResult:
        """TIGER ETF 이벤트 — 사이트맵에서 이벤트 URL 추출 후 개별 페이지 스크래핑."""
        ch, name = "tiger_event", CHANNEL_LABELS["tiger_event"]
        base = "https://investments.miraeasset.com"
        try:
            sitemap = requests.get(f"{base}/tigeretf/sitemap.xml", headers=BROWSER_HEADERS, timeout=15)
            sitemap.raise_for_status()
            # event/view.do?...detailsKey=XXX URL 추출 (중복 제거)
            raw_urls = re.findall(r'https://investments\.miraeasset\.com/tigeretf/ko/customer/event/view\.do\?[^<\s]+', sitemap.text)
            seen_keys: set = set()
            event_urls = []
            for u in raw_urls:
                key_m = re.search(r"detailsKey=(\d+)", u)
                if key_m and key_m.group(1) not in seen_keys:
                    seen_keys.add(key_m.group(1))
                    event_urls.append(u.replace("&amp;", "&"))

            events = []
            for url in event_urls[:15]:
                try:
                    dr = requests.get(url, headers=BROWSER_HEADERS, timeout=10)
                    dsoup = BeautifulSoup(dr.text, "lxml")
                    # #contents 첫 번째 의미있는 텍스트 블록에서 제목 추출
                    title = ""
                    cont = dsoup.select_one("#contents")
                    if cont:
                        lines = [ln.strip() for ln in cont.get_text("\n").split("\n") if ln.strip()]
                        # "이벤트 종료" / "이벤트 진행중" 같은 상태 텍스트 스킵하고 첫 실제 제목
                        for ln in lines:
                            if len(ln) > 8 and not re.match(r"^(이벤트|공지사항|검색|당첨자|종료|진행중|조회|더보기)$", ln):
                                title = ln
                                break
                    # 기간
                    period_m = re.search(r"\d{4}[.\-]\d{2}[.\-]\d{2}\s*[~\-]\s*\d{4}[.\-]\d{2}[.\-]\d{2}", dr.text)
                    period = period_m.group() if period_m else ""
                    # OG 이미지
                    img_url = ""
                    for attr in [{"property":"og:image"}, {"name":"og:image"}]:
                        tag = dsoup.find("meta", attrs=attr)
                        if tag and tag.get("content","").startswith("http"):
                            img_url = tag["content"]; break
                    if not img_url:
                        for img in dsoup.find_all("img"):
                            src = img.get("src","") or img.get("data-src","")
                            if src and any(k in src.lower() for k in ["event","banner","thumb","visual","poster"]):
                                img_url = src if src.startswith("http") else "https://investments.miraeasset.com" + src
                                break
                    # 종료/당첨자 발표 이벤트 제외 (당점자 오타 포함)
                    if re.search(r"당[첨점]자\s*발표|이벤트\s*종료|\(종료\)", title):
                        continue
                    if title and len(title) > 5:
                        events.append({"title": title, "url": url, "period": period, "image_url": img_url})
                except Exception:
                    continue

            if not events:
                return ChannelResult(ch, name, True,
                    data={"events": [], "event_details": [], "raw_text": ""},
                    error_label="이번 주 진행 중인 TIGER 이벤트 없음")
            raw_text = " ".join(f"{e['title']} {e.get('period','')}" for e in events)
            return ChannelResult(ch, name, True, data={
                "events": [e["title"] for e in events],
                "event_details": events,
                "raw_text": raw_text,
                "url": f"{base}/tigeretf/ko/customer/event/list.do",
            })
        except requests.RequestException as e:
            code = getattr(getattr(e, "response", None), "status_code", 0)
            et = "ACCESS_BLOCKED" if code in (403, 429) else "CONNECTION_ERROR"
            return ChannelResult(ch, name, False, error=str(e), error_type=et)
        except Exception as e:
            return ChannelResult(ch, name, False, error=str(e), error_type="UNKNOWN")

    def _ch_ace_event(self) -> ChannelResult:
        """ACE ETF 이벤트 — 네이버 블로그 RSS ([EVENT] 태그 게시물)."""
        ch, name = "ace_event", CHANNEL_LABELS["ace_event"]
        rss_url = "https://rss.blog.naver.com/aceetf.xml"
        try:
            r = requests.get(rss_url, headers=BROWSER_HEADERS, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "xml")
            events = []
            for item in soup.find_all("item")[:30]:
                title = item.find("title").get_text(strip=True) if item.find("title") else ""
                link  = item.find("link").get_text(strip=True) if item.find("link") else ""
                pub_str = item.find("pubDate").get_text(strip=True) if item.find("pubDate") else ""
                pub_dt = self._parse_pub_date(pub_str)
                if pub_dt and not self._in_range(pub_dt):
                    continue
                is_event = bool(re.search(r"\[EVENT\]|이벤트|EVENT|프로모션|매수.*인증|경품|혜택", title, re.I))
                if is_event:
                    img_url = self._fetch_og_image(link, "https://blog.naver.com") if link else ""
                    events.append({"title": title, "url": link, "pub_date": pub_str, "image_url": img_url})
            if not events:
                return ChannelResult(ch, name, True,
                    data={"events": [], "event_details": [], "raw_text": ""},
                    error_label="이번 주 이벤트 게시물 없음")
            raw_text = " ".join(e["title"] for e in events)
            return ChannelResult(ch, name, True, data={
                "events": [e["title"] for e in events],
                "event_details": events,
                "raw_text": raw_text,
                "url": rss_url,
            })
        except requests.RequestException as e:
            code = getattr(getattr(e, "response", None), "status_code", 0)
            et = "ACCESS_BLOCKED" if code in (403, 429) else "CONNECTION_ERROR"
            return ChannelResult(ch, name, False, error=str(e), error_type=et)
        except Exception as e:
            return ChannelResult(ch, name, False, error=str(e), error_type="UNKNOWN")

    def _ch_rise_event(self) -> ChannelResult:
        """RISE ETF 이벤트 페이지 — KB자산운용 (SSR)."""
        ch, name = "rise_event", CHANNEL_LABELS["rise_event"]
        list_url = "https://www.riseetf.co.kr/cust/event"
        base_url = "https://www.riseetf.co.kr"
        try:
            r = requests.get(list_url, headers=BROWSER_HEADERS, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            events = []
            # 이벤트 목록: li 구조, /cust/event/[ID] 링크
            for a in soup.find_all("a", href=re.compile(r"/cust/event/\d+")):
                title_el = a.find(text=True, recursive=True)
                title = a.get_text(" ", strip=True)
                href = a.get("href", "")
                if not title or len(title) < 5:
                    continue
                url_full = base_url + href if href.startswith("/") else href
                # 기간 추출 (YYYY-MM-DD ~ YYYY-MM-DD)
                period = ""
                parent = a.find_parent()
                if parent:
                    period_m = re.search(r"\d{4}-\d{2}-\d{2}\s*~\s*\d{4}-\d{2}-\d{2}", parent.get_text())
                    if period_m:
                        period = period_m.group()
                img_url = self._fetch_og_image(url_full, base_url)
                events.append({"title": title, "url": url_full, "period": period, "image_url": img_url})
            if not events:
                return ChannelResult(ch, name, True,
                    data={"events": [], "event_details": [], "raw_text": ""},
                    error_label="이번 주 RISE 이벤트 없음")
            raw_text = " ".join(f"{e['title']} {e.get('period','')}" for e in events)
            return ChannelResult(ch, name, True, data={
                "events": [e["title"] for e in events],
                "event_details": events,
                "raw_text": raw_text,
                "url": list_url,
            })
        except requests.RequestException as e:
            code = getattr(getattr(e, "response", None), "status_code", 0)
            et = "ACCESS_BLOCKED" if code in (403, 429) else "CONNECTION_ERROR"
            return ChannelResult(ch, name, False, error=str(e), error_type=et)
        except Exception as e:
            return ChannelResult(ch, name, False, error=str(e), error_type="UNKNOWN")

    def _ch_hanaro_event(self) -> ChannelResult:
        """HANARO ETF 이벤트 — 구글/네이버 뉴스 검색 (공지 페이지 JS 렌더링 불가)."""
        ch, name = "hanaro_event", CHANNEL_LABELS["hanaro_event"]
        keywords = ["HANARO ETF 이벤트", "HANARO ETF 프로모션", "NH아문디 HANARO 이벤트", "하나로ETF 이벤트"]
        events = []
        seen: set = set()

        # 네이버 뉴스 스크래핑
        for kw in keywords:
            try:
                url = f"https://search.naver.com/search.naver?where=news&query={requests.utils.quote(kw)}&sort=1"
                r = requests.get(url, headers=BROWSER_HEADERS, timeout=10)
                soup = BeautifulSoup(r.text, "lxml")
                for item in soup.select(".news_area, .list_news .bx")[:5]:
                    title_tag = item.select_one(".news_tit, a.title")
                    if not title_tag:
                        continue
                    title = title_tag.get_text(strip=True)
                    link  = title_tag.get("href", "") if title_tag.name == "a" else ""
                    if title not in seen and re.search(r"이벤트|프로모션|HANARO|하나로", title, re.I):
                        seen.add(title)
                        events.append({"title": title, "url": link})
            except Exception:
                pass

        # 구글 뉴스 RSS 보완
        for kw in keywords[:2]:
            try:
                r = requests.get(
                    f"https://news.google.com/rss/search?q={requests.utils.quote(kw)}&hl=ko&gl=KR&ceid=KR:ko",
                    headers=BROWSER_HEADERS, timeout=10,
                )
                soup = BeautifulSoup(r.text, "xml")
                for item in soup.find_all("item")[:5]:
                    title = item.find("title").get_text(strip=True) if item.find("title") else ""
                    link  = item.find("link").get_text(strip=True) if item.find("link") else ""
                    pub_str = item.find("pubDate").get_text(strip=True) if item.find("pubDate") else ""
                    pub_dt = self._parse_pub_date(pub_str)
                    if pub_dt and not self._in_range(pub_dt):
                        continue
                    if title and title not in seen:
                        seen.add(title)
                        events.append({"title": title, "url": link, "pub_date": pub_str})
            except Exception:
                pass

        if not events:
            return ChannelResult(ch, name, True,
                data={"events": [], "event_details": [], "raw_text": ""},
                error_label="이번 주 이벤트 뉴스 없음")
        raw_text = " ".join(e["title"] for e in events)
        return ChannelResult(ch, name, True, data={
            "events": [e["title"] for e in events],
            "event_details": events,
            "raw_text": raw_text,
        })

    def _ch_sol_event(self) -> ChannelResult:
        """SOL ETF 공지/이벤트 — JSON API 직접 호출."""
        ch, name = "sol_event", CHANNEL_LABELS["sol_event"]
        api_url = "https://www.soletf.com/api/cs/notice"
        try:
            r = requests.get(api_url, headers=BROWSER_HEADERS, timeout=15)
            r.raise_for_status()
            data = r.json()
            items = data.get("items", [])
            events = []
            seen_titles: set = set()
            for item in items:
                title = item.get("TITLE", "")
                no    = item.get("NO", "")
                if not title or title in seen_titles:
                    continue
                is_event = bool(re.search(r"이벤트|EVENT|프로모션|경품|혜택|매수|기념|팬덤", title, re.I))
                if is_event:
                    seen_titles.add(title)
                    detail_url = f"https://www.soletf.com/ko/cs/noticeView?id={no}"
                    img_url = self._fetch_og_image(detail_url, "https://www.soletf.com")
                    events.append({
                        "title": title,
                        "url": detail_url,
                        "image_url": img_url,
                    })
            if not events:
                return ChannelResult(ch, name, True,
                    data={"events": [], "event_details": [], "raw_text": ""},
                    error_label="이번 주 SOL 이벤트 공지 없음")
            raw_text = " ".join(e["title"] for e in events)
            return ChannelResult(ch, name, True, data={
                "events": [e["title"] for e in events],
                "event_details": events,
                "raw_text": raw_text,
                "url": api_url,
            })
        except requests.RequestException as e:
            code = getattr(getattr(e, "response", None), "status_code", 0)
            et = "ACCESS_BLOCKED" if code in (403, 429) else "CONNECTION_ERROR"
            return ChannelResult(ch, name, False, error=str(e), error_type=et)
        except Exception as e:
            return ChannelResult(ch, name, False, error=str(e), error_type="UNKNOWN")

    def _ch_sol_blog(self) -> ChannelResult:
        """SOL ETF 블로그 — 신한자산운용 네이버 블로그."""
        ch, name = "sol_blog", CHANNEL_LABELS["sol_blog"]
        rss_url = "https://rss.blog.naver.com/soletf.xml"
        try:
            r = requests.get(rss_url, headers=BROWSER_HEADERS, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "xml")
            posts = []
            for item in soup.find_all("item")[:20]:
                title = item.find("title").get_text(strip=True) if item.find("title") else ""
                link = item.find("link").get_text(strip=True) if item.find("link") else ""
                pub_str = item.find("pubDate").get_text(strip=True) if item.find("pubDate") else ""
                pub_dt = self._parse_pub_date(pub_str)
                if pub_dt and not self._in_range(pub_dt):
                    continue
                is_event = bool(re.search(r"이벤트|EVENT|event|프로모션|경품|혜택|매수|기념|출시|상장", title, re.I))
                posts.append({"title": title, "url": link, "published_at": pub_str, "is_event_related": is_event})
            if not posts:
                return ChannelResult(ch, name, True, data={"posts": [], "note": "해당 주차 게시물 없음"})
            return ChannelResult(ch, name, True, data={
                "posts": posts,
                "raw_text": " ".join(p["title"] for p in posts),
                "url": rss_url,
            })
        except requests.RequestException as e:
            code = getattr(getattr(e, "response", None), "status_code", 0)
            et = "ACCESS_BLOCKED" if code in (403, 429) else "CONNECTION_ERROR"
            return ChannelResult(ch, name, False, error=str(e), error_type=et)
        except Exception as e:
            return ChannelResult(ch, name, False, error=str(e), error_type="UNKNOWN")

    def _ch_etf_am_news(self) -> ChannelResult:
        """ETF 운용사 마케팅·이벤트 뉴스 — 네이버 스크래핑 + 구글 뉴스 RSS."""
        ch, name = "etf_am_news", CHANNEL_LABELS["etf_am_news"]
        articles = []
        source_used = None
        seen_links: set = set()

        # ── 네이버 뉴스 스크래핑 ──────────────────────────────────────────────
        naver_keywords = [
            "KODEX ETF 이벤트", "TIGER ETF 이벤트", "ACE ETF 이벤트",
            "RISE ETF 이벤트", "HANARO ETF 이벤트", "SOL ETF 이벤트",
            "삼성자산운용 ETF 출시", "미래에셋자산운용 ETF 출시",
            "한국투자신탁운용 ETF 이벤트", "KB자산운용 ETF 이벤트",
        ]
        for kw in naver_keywords:
            try:
                url = f"https://search.naver.com/search.naver?where=news&query={requests.utils.quote(kw)}&sort=1"
                r = requests.get(url, headers=BROWSER_HEADERS, timeout=10)
                if r.status_code != 200:
                    continue
                soup = BeautifulSoup(r.text, "lxml")
                for item in soup.select(".news_area, .list_news .bx")[:5]:
                    title_tag = item.select_one(".news_tit, a.title")
                    date_tag  = item.select_one(".info_group .info, .sub_txt .info")
                    if not title_tag:
                        continue
                    title = title_tag.get_text(strip=True)
                    link  = title_tag.get("href", "") if title_tag.name == "a" else ""
                    if link in seen_links:
                        continue
                    seen_links.add(link)
                    pub_str = date_tag.get_text(strip=True) if date_tag else ""
                    pub_dt = self._parse_pub_date(pub_str)
                    if pub_dt and not self._in_range(pub_dt):
                        continue
                    articles.append({
                        "title": title, "description": "",
                        "link": link, "pub_date": pub_str,
                        "keyword": kw, "source": "naver_scrape",
                    })
                source_used = source_used or "naver_scrape"
            except Exception as e:
                logger.debug(f"네이버 뉴스 스크래핑 실패 ({kw}): {e}")

        # ── 네이버 뉴스 API (키 있을 때) ─────────────────────────────────────
        if self.naver_client_id and self.naver_client_secret:
            api_keywords = [
                "KODEX ETF 이벤트", "TIGER ETF 이벤트", "ACE ETF 이벤트",
                "RISE ETF 이벤트", "HANARO ETF 이벤트", "SOL ETF 이벤트",
                "삼성자산운용 ETF", "미래에셋자산운용 TIGER ETF",
                "한국투자신탁운용 ACE ETF", "KB자산운용 RISE ETF",
                "NH아문디 HANARO ETF", "신한자산운용 SOL ETF",
            ]
            try:
                for kw in api_keywords:
                    r = requests.get(
                        "https://openapi.naver.com/v1/search/news.json",
                        headers={**BROWSER_HEADERS,
                                 "X-Naver-Client-Id": self.naver_client_id,
                                 "X-Naver-Client-Secret": self.naver_client_secret},
                        params={"query": kw, "display": 10, "sort": "date"},
                        timeout=10,
                    )
                    r.raise_for_status()
                    for item in r.json().get("items", []):
                        link = item.get("originallink") or item.get("link", "")
                        if link in seen_links:
                            continue
                        seen_links.add(link)
                        pub_dt = self._parse_pub_date(item.get("pubDate", ""))
                        if pub_dt and not self._in_range(pub_dt):
                            continue
                        articles.append({
                            "title": re.sub(r"<[^>]+>", "", item.get("title", "")),
                            "description": re.sub(r"<[^>]+>", "", item.get("description", ""))[:300],
                            "link": link,
                            "pub_date": item.get("pubDate", ""),
                            "keyword": kw, "source": "naver_api",
                        })
                source_used = "naver_api"
            except Exception as e:
                logger.warning(f"네이버 뉴스 API 실패 (ETF AM): {e}")

        # ── 구글 뉴스 RSS ─────────────────────────────────────────────────────
        google_queries = [
            "KODEX+ETF+이벤트", "TIGER+ETF+이벤트", "ACE+ETF+이벤트",
            "RISE+ETF+이벤트", "HANARO+ETF+이벤트", "SOL+ETF+이벤트",
            "삼성자산운용+ETF+신규상장", "미래에셋자산운용+TIGER+ETF",
            "한국투자신탁운용+ACE+ETF", "KB자산운용+RISE+ETF",
            "NH아문디+HANARO+ETF", "신한자산운용+SOL+ETF",
        ]
        for q in google_queries:
            try:
                r = requests.get(
                    f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko",
                    headers=BROWSER_HEADERS, timeout=15,
                )
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "xml")
                for item in soup.find_all("item")[:8]:
                    pub_str = item.find("pubDate").get_text(strip=True) if item.find("pubDate") else ""
                    pub_dt = self._parse_pub_date(pub_str)
                    if pub_dt and not self._in_range(pub_dt):
                        continue
                    link = item.find("link").get_text(strip=True) if item.find("link") else ""
                    if link in seen_links:
                        continue
                    seen_links.add(link)
                    title = item.find("title").get_text(strip=True) if item.find("title") else ""
                    articles.append({
                        "title": title, "description": "",
                        "link": link, "pub_date": pub_str,
                        "keyword": q.replace("+", " "), "source": "google_rss",
                    })
                source_used = source_used or "google_rss"
            except Exception as e:
                logger.debug(f"Google RSS 실패 ({q}): {e}")

        note = (
            "네이버 뉴스 API" if source_used == "naver_api"
            else ("네이버 스크래핑 + 구글 RSS" if source_used == "naver_scrape" else "구글 뉴스 RSS")
        )
        if articles:
            return ChannelResult(ch, name, True, data={
                "articles": articles[:40],
                "source": source_used,
                "note": note,
                "raw_text": " ".join(a["title"] for a in articles[:40]),
            })
        return ChannelResult(ch, name, False,
            error="뉴스 수집 실패 — 네트워크 확인 필요",
            error_type="CONNECTION_ERROR")
