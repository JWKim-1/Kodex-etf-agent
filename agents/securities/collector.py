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
            # 공통 (KRX 데이터는 pykrx 자동 수집으로 대체됨)
            ("news",               self._ch_news),
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
                try:
                    detail_r = requests.get(url_full, headers=BROWSER_HEADERS, timeout=10)
                    detail_soup = BeautifulSoup(detail_r.text, "lxml")
                    detail_text = detail_soup.get_text(" ", strip=True)
                    alt_texts = " ".join(
                        img.get("alt", "") for img in detail_soup.find_all("img")
                        if img.get("alt", "").strip()
                    )
                    page_full_text = detail_text + " " + alt_texts

                    # ── "이벤트 참여 >" 증권사 링크 따라가서 ETF명 추가 수집 ──
                    # ── 분석 기준일: 현재 주차면 오늘, 과거 주차면 해당 주의 끝날 ──
                    from datetime import date as _dt
                    _ref_date = self.week_end.date() if self.week_end else _dt.today()

                    for sec_a in detail_soup.find_all("a", href=True):
                        sec_txt = sec_a.get_text(strip=True)
                        sec_href = sec_a.get("href", "")
                        if ("이벤트 참여" in sec_txt or "참여하기" in sec_txt) and sec_href.startswith("http"):
                            try:
                                sec_r = requests.get(sec_href, headers=BROWSER_HEADERS, timeout=8)
                                sec_soup = BeautifulSoup(sec_r.text, "lxml")
                                sec_text = sec_soup.get_text(" ", strip=True)
                                # 이벤트 기간 추출 → 종료일 기준으로 유효한지 체크
                                # 날짜 여러 개 중 가장 마지막 날짜 = 종료일로 판단
                                # 4자리 연도 or 2자리 연도(26.05.15 형식) 모두 캐치
                                date_matches = re.findall(r"(\d{2,4})[.\-년]?\s*(\d{1,2})[.\-월]?\s*(\d{1,2})", sec_text)
                                if date_matches:
                                    try:
                                        # 마지막 날짜를 종료일로 사용 (2자리 연도 → 2000+로 변환)
                                        raw_y, end_mo, end_d = int(date_matches[-1][0]), int(date_matches[-1][1]), int(date_matches[-1][2])
                                        end_y = raw_y + 2000 if raw_y < 100 else raw_y
                                        if _dt(end_y, end_mo, end_d) < _ref_date:
                                            continue  # 분석 기준일 이전에 종료된 이벤트
                                    except Exception:
                                        pass
                                page_full_text += " " + sec_text[:2000]
                            except Exception:
                                pass

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
                    "full_text": page_full_text[:1500],  # 전체 텍스트 저장 (키워드 매칭용)
                })
                raw_combined += " " + page_full_text[:500]  # 제목 대신 본문도 포함

            if not events:
                return ChannelResult(ch, name, False,
                    error="진행 중인 이벤트를 찾지 못했습니다", error_type="PARSE_ERROR")

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
        one_week_ago = datetime.utcnow() - timedelta(days=7)
        if self.youtube_api_key:
            try:
                from googleapiclient.discovery import build
                yt = build("youtube", "v3", developerKey=self.youtube_api_key)
                pub_after = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
                search = yt.search().list(part="id,snippet", channelId=channel_id, type="video",
                                          publishedAfter=pub_after, maxResults=10, order="date").execute()
                videos = []
                for item in search.get("items", []):
                    vid_id = item["id"].get("videoId", "")
                    title = item.get("snippet", {}).get("title", "")
                    pub = item.get("snippet", {}).get("publishedAt", "")
                    is_etf = bool(re.search(r"ETF|KODEX|TIGER|코덱스|배당|채권|지수|리츠|반도체|AI", title, re.I))
                    videos.append({"title": title, "published_at": pub, "is_etf_related": is_etf,
                                   "url": f"https://youtu.be/{vid_id}"})
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
                videos.append({
                    "title": title,
                    "published_at": pub_str,
                    "is_etf_related": is_etf,
                    "url": vid_url,
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
        one_week_ago = datetime.utcnow() - timedelta(days=7)

        # YouTube Data API v3 시도
        if self.youtube_api_key:
            try:
                from googleapiclient.discovery import build

                yt = build("youtube", "v3", developerKey=self.youtube_api_key)
                pub_after = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
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
                    if self.week_start is not None:
                        continue
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
        ch, name = "instagram", CHANNEL_LABELS["instagram"]
        url = "https://www.instagram.com/samsung.securities/"
        try:
            r = requests.get(url, headers=BROWSER_HEADERS, timeout=15, allow_redirects=True)
            if "login" in r.url or "accounts/login" in r.text[:500]:
                return ChannelResult(
                    ch, name, False,
                    error="로그인 페이지 리다이렉트 — Instagram 비로그인 공개 스크래핑 차단",
                    error_type="LOGIN_REQUIRED",
                )
            if r.status_code == 403:
                return ChannelResult(ch, name, False, error="HTTP 403 — 봇 탐지", error_type="BOT_DETECTED")

            # Selenium 시도
            try:
                driver = _selenium_driver()
                driver.get(url)
                time.sleep(5)
                cur_url = driver.current_url
                driver.quit()
                if "login" in cur_url:
                    return ChannelResult(
                        ch, name, False,
                        error="Selenium으로도 로그인 페이지 리다이렉트 — Instagram 봇 탐지",
                        error_type="BOT_DETECTED",
                    )
                return ChannelResult(
                    ch, name, False,
                    error="Instagram 봇 탐지 또는 로그인 필요 — 공개 게시물 스크래핑 불가",
                    error_type="BOT_DETECTED",
                )
            except Exception as se:
                return ChannelResult(
                    ch, name, False,
                    error=f"Instagram 봇 탐지로 실패. Selenium 오류: {se}",
                    error_type="BOT_DETECTED",
                )
        except Exception as e:
            return ChannelResult(ch, name, False, error=f"Instagram 접근 실패: {e}", error_type="BOT_DETECTED")

    # ── CH4: 삼성증권 블로그 (네이버 RSS) ───────────────────────────────────

    def _ch_samsung_blog(self) -> ChannelResult:
        return self._ch_naver_blog_base("samsung_blog", CHANNEL_LABELS["samsung_blog"],
                                        "https://rss.blog.naver.com/samsung_fn.xml")

    def _ch_naver_blog_base(self, ch: str, name: str, rss_url: str) -> ChannelResult:
        """네이버 블로그 RSS 공통 수집 로직."""
        try:
            r = requests.get(rss_url, headers=BROWSER_HEADERS, timeout=15)
            if r.status_code == 403:
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
                # 날짜 파싱 실패해도 week_start 설정됐으면 제외 (오늘 글 혼입 방지)
                if pub_dt is None and self.week_start is not None:
                    continue
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
                    return ChannelResult(
                        ch, name, False,
                        error="SPA + 봇 탐지 — Selenium으로도 ETF 이벤트 콘텐츠 추출 불가",
                        error_type="BOT_DETECTED",
                    )
                except ImportError:
                    return ChannelResult(ch, name, False, error="SPA 구조 + Selenium 미설치", error_type="SPA_STRUCTURE")
                except Exception as se:
                    return ChannelResult(ch, name, False, error=f"SPA + Selenium 실패: {se}", error_type="BOT_DETECTED")

            # 정적 콘텐츠 파싱
            events = []
            for tag in soup.find_all(string=re.compile(r"ETF|KODEX|이벤트", re.I)):
                p = tag.parent
                if p and p.name not in ("script", "style"):
                    events.append(p.get_text(strip=True)[:200])
            return ChannelResult(ch, name, True, data={"events": list(dict.fromkeys(events))[:10], "source": "static"})

        except requests.HTTPError as e:
            code = e.response.status_code if e.response else 0
            et = "ACCESS_BLOCKED" if code in (403, 429) else "UNKNOWN"
            return ChannelResult(ch, name, False, error=str(e), error_type=et)
        except Exception as e:
            return ChannelResult(ch, name, False, error=str(e), error_type="UNKNOWN")

    # ── CH6: 삼성증권 카카오톡 채널 ──────────────────────────────────────────

    def _ch_kakao(self) -> ChannelResult:
        ch, name = "kakao", CHANNEL_LABELS["kakao"]
        return ChannelResult(
            ch, name, False,
            error=(
                "구독자만 수신 가능한 구조 — 카카오 Open API는 채널 공개 콘텐츠 조회 미지원. "
                "채널 관리자 계정 권한 필요"
            ),
            error_type="SUBSCRIBER_ONLY",
        )

    # ── CH7: KRX 보도자료 ────────────────────────────────────────────────────

    def _ch_krx_news(self) -> ChannelResult:
        ch, name = "krx_news", CHANNEL_LABELS["krx_news"]
        # KRX 보도자료 RSS / API 시도
        try_urls = [
            "https://www.krx.co.kr/contents/COM/GenerateFile.jspx?filetype=rss&filename=krx_press",
            "https://www.krx.co.kr/main/main.jsp",
        ]
        for url in try_urls:
            try:
                r = requests.get(url, headers=BROWSER_HEADERS, timeout=15)
                if r.status_code == 403:
                    continue
                r.raise_for_status()
                # RSS 파싱 시도
                try:
                    soup = BeautifulSoup(r.text, "xml")
                    items = soup.find_all("item")
                    if items:
                        one_week_ago = datetime.now() - timedelta(days=7)
                        news = []
                        for item in items[:20]:
                            title = item.find("title").get_text(strip=True) if item.find("title") else ""
                            pub = item.find("pubDate").get_text(strip=True) if item.find("pubDate") else ""
                            link = item.find("link").get_text(strip=True) if item.find("link") else ""
                            if title:
                                news.append({"title": title, "date": pub, "url": link})
                        if news:
                            return ChannelResult(ch, name, True, data={"news": news, "source": "rss"})
                except Exception:
                    pass
                # HTML 파싱 시도
                soup = BeautifulSoup(r.text, "lxml")
                news = []
                for row in soup.find_all("tr")[:30]:
                    tds = row.find_all("td")
                    if tds:
                        title = tds[0].get_text(strip=True)
                        if title and len(title) > 5:
                            news.append({"title": title})
                if news:
                    return ChannelResult(ch, name, True, data={"news": news[:10], "source": "html"})
            except Exception as e:
                logger.debug(f"KRX URL 실패 ({url}): {e}")
                continue

        return ChannelResult(
            ch, name, False,
            error="KRX 보도자료 접근 실패 — SPA 또는 접속 차단 (403). 직접 브라우저 접속 필요",
            error_type="ACCESS_BLOCKED",
        )

    # ── CH8: KRX 투자자별 거래실적 ───────────────────────────────────────────

    def _ch_krx_trading(self) -> ChannelResult:
        ch, name = "krx_trading", CHANNEL_LABELS["krx_trading"]
        return ChannelResult(
            ch, name, False,
            error=(
                "data.krx.co.kr는 SPA 구조 — 정적 요청으로 데이터 테이블 접근 불가. "
                "엑셀 파일 수동 업로드로 대체 (현재 선택된 방식)"
            ),
            error_type="SPA_STRUCTURE",
        )

    # ── CH9: 구글 트렌드 ─────────────────────────────────────────────────────

    def _ch_google_trends(self) -> ChannelResult:
        ch, name = "google_trends", CHANNEL_LABELS["google_trends"]
        keywords = ["KODEX", "삼성증권 ETF", "KODEX 200"]
        try:
            from pytrends.request import TrendReq

            pt = TrendReq(hl="ko", tz=540, timeout=(10, 25), retries=2, backoff_factor=0.5)
            pt.build_payload(keywords, cat=0, timeframe="today 4-w", geo="KR")
            df = pt.interest_over_time()

            if df.empty:
                return ChannelResult(ch, name, False, error="구글 트렌드 데이터 비어있음", error_type="UNKNOWN")

            trends = {}
            for kw in keywords:
                if kw in df.columns:
                    s = df[kw]
                    cur = float(s.iloc[-1])
                    avg = float(s.mean())
                    chg = round((cur - avg) / avg * 100, 1) if avg > 0 else 0.0
                    trends[kw] = {"current": int(cur), "avg_4w": round(avg, 1), "change_pct": chg,
                                  "weekly": [int(v) for v in s.tolist()]}
            return ChannelResult(ch, name, True, data={"trends": trends})
        except ImportError:
            return ChannelResult(ch, name, False, error="pytrends 미설치 — pip install pytrends", error_type="UNKNOWN")
        except Exception as e:
            err = str(e)
            if "429" in err or "Too Many Requests" in err:
                return ChannelResult(ch, name, False,
                    error="구글 트렌드 요청 제한 (429) — 잠시 후 재시도 필요", error_type="ACCESS_BLOCKED")
            if "400" in err:
                return ChannelResult(ch, name, False,
                    error="구글 트렌드 봇 감지 차단 (400) — pytrends는 비공식 라이브러리로 구글이 주기적으로 차단. 해결 불가 (구글 공식 API 없음)",
                    error_type="BOT_DETECTED")
            return ChannelResult(ch, name, False, error=err, error_type="UNKNOWN")

    # ── CH10: 퇴직연금 상품가이드 PDF ────────────────────────────────────────

    def _ch_pension_pdf(self) -> ChannelResult:
        ch, name = "pension_pdf", CHANNEL_LABELS["pension_pdf"]
        return ChannelResult(
            ch, name, False,
            error=(
                "삼성증권 퇴직연금 상품가이드는 SPA + 로그인 구조 — "
                "비로그인 Selenium으로 PDF 링크 접근 불가"
            ),
            error_type="SPA_STRUCTURE",
        )

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
            # 케이스4: AUM 마일스톤
            "KODEX 순자산 돌파",
            "KODEX AUM",
            # 케이스5: 계절성
            "삼성증권 ETF 설날",
            "삼성증권 ETF 추석",
            "삼성증권 ETF 가정의달",
            # 케이스6: 경쟁사 대응
            "KODEX 최저보수",
            "삼성자산운용 보수 인하",
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
                        if not pub_dt and self.week_start is not None:
                            continue
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
                # 케이스4: AUM 마일스톤
                "KODEX 순자산 돌파",
                # 케이스5: 계절성
                "삼성증권 ETF 설날", "삼성증권 ETF 추석",
                # 케이스6: 경쟁사 대응
                "KODEX 최저보수", "삼성자산운용 보수 인하",
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
                        if not pub_dt and self.week_start is not None:
                            continue
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
                        if not pub_dt and self.week_start is not None:
                            continue
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
