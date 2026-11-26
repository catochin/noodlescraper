import asyncio
import json
import re
import time
from urllib.parse import quote_plus, urlparse

from curl_cffi import CurlOpt
from curl_cffi.requests import AsyncSession
from curl_cffi.requests.errors import RequestsError
from selectolax.lexbor import LexborHTMLParser


def _parse_duration(text: str) -> int | None:
    """Seconds from a listing label like 32:20 or 1:30:00."""
    parts = text.strip().split(":")
    if not parts or len(parts) > 3 or not all(part.isdigit() for part in parts):
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def _format_duration(seconds: int | None) -> str | None:
    """M:SS, or H:MM:SS once the video passes an hour."""
    if not seconds:
        return None
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class NoodleScraper:
    """Async scraper for noodlemagazine.com built on curl-cffi."""

    def __init__(self, impersonate: str = "chrome", timeout: int = 15):
        self.base_url = "https://noodlemagazine.com"
        self.timeout = timeout
        self.session = AsyncSession(
            impersonate=impersonate,
            timeout=timeout,
            max_clients=20,
            cookies={"age_verification": "1"},
            curl_options={CurlOpt.IPRESOLVE: 1},
        )

    async def close(self):
        """Release the underlying curl handles."""
        await self.session.close()

    async def ping(self) -> dict:
        t0 = time.perf_counter()
        try:
            resp = await asyncio.wait_for(
                self.session.get(self.base_url, timeout=6),
                timeout=7,
            )
            ms = round((time.perf_counter() - t0) * 1000, 1)
            return {
                "origin": str(resp.url).rstrip("/"),
                "alive": resp.status_code == 200,
                "status": resp.status_code,
                "latency_ms": ms,
            }
        except (RequestsError, TimeoutError, OSError, ValueError) as exc:
            ms = round((time.perf_counter() - t0) * 1000, 1)
            return {
                "origin": self.base_url,
                "alive": False,
                "status": None,
                "latency_ms": ms,
                "error": f"{type(exc).__name__}: {exc}",
            }

    async def _get(self, url: str, referer: str | None = None):
        """GET a URL, returning the response or None on any failure."""
        try:
            return await self.session.get(
                url,
                timeout=self.timeout,
                headers={"Referer": referer} if referer else None,
            )
        except RequestsError as e:
            print(f"[Noodle] Error loading {url}: {e}")
            return None

    @staticmethod
    def _origin(url: str) -> str:
        parts = urlparse(url)
        return f"{parts.scheme}://{parts.netloc}"

    async def search_videos(
        self, query: str, page: int = 0, with_sources: bool = False
    ) -> dict:
        """Search for videos on NoodleMagazine."""
        response = await self._get(
            f"{self.base_url}/video/{quote_plus(query)}?p={page}"
        )
        if response is None or response.status_code != 200:
            return {
                "source": "noodle",
                "error": "Failed to load NoodleMagazine page",
                "videos": [],
                "has_next": False,
            }

        base_url = self._origin(str(response.url))
        tree = LexborHTMLParser(response.text)
        videos = []
        for node in tree.css(".list_videos .item_link"):
            item = self._parse_listing_item(node)
            if item:
                videos.append(item)

        if with_sources and videos:
            semaphore = asyncio.Semaphore(5)

            async def enrich(item: dict) -> None:
                async with semaphore:
                    details = await self.get_video_by_id(item["id"])
                if details is None:
                    return
                item.update(
                    {
                        "sources": details["sources"],
                        "tags": details["tags"],
                        "duration": details["duration"],
                        "duration_text": details["duration_text"],
                        "player_url": details["player_url"],
                    }
                )

            await asyncio.gather(*(enrich(item) for item in videos))

        has_next = bool(
            tree.css_first(".pagination .next, .more_btn") or len(videos) >= 20
        )
        return {
            "source": "noodle",
            "base_url": base_url,
            "query": query,
            "page": page,
            "has_next": has_next,
            "videos": videos,
        }

    @staticmethod
    def _parse_listing_item(node) -> dict | None:
        href = node.attributes.get("href")
        if not href:
            return None

        parts = urlparse(href)
        video_id = parts.path.rstrip("/").rsplit("/", 1)[-1]
        if not video_id:
            return None

        image = ""
        img = node.css_first("img")
        if img is not None:
            image = img.attributes.get("data-src") or img.attributes.get("src") or ""

        title = "Untitled"
        title_node = node.css_first(".i_info .title")
        if title_node is not None:
            title = title_node.text(strip=True) or title
        elif img is not None:
            title = img.attributes.get("alt") or title

        time_node = node.css_first(".m_time")
        duration_seconds = (
            _parse_duration(time_node.text(strip=True))
            if time_node is not None
            else None
        )

        return {
            "id": video_id,
            "source": "noodle",
            "title": title,
            "image": image,
            "duration": duration_seconds,
            "duration_text": _format_duration(duration_seconds),
            "views": None,
            "models": [],
        }

    async def get_video_by_id(self, video_id: str) -> dict | None:
        video_id = video_id.strip("/")
        response = await self._get(f"{self.base_url}/watch/{video_id}")
        if response is None or response.status_code != 200:
            return None

        video_page_url = str(response.url)
        base_url = self._origin(video_page_url)
        tree = LexborHTMLParser(response.text)

        og_video_tag = tree.css_first('meta[property="og:video"]')
        if og_video_tag is None:
            return None

        player_url = urlparse(og_video_tag.attributes.get("content") or "")
        player_url = player_url._replace(
            scheme=urlparse(base_url).scheme, netloc=urlparse(base_url).netloc
        ).geturl()

        og_title_tag = tree.css_first('meta[property="og:title"]')
        title = og_title_tag.attributes.get("content") if og_title_tag else None
        title = title or "Untitled"

        og_image_tag = tree.css_first('meta[property="og:image"]')
        image = og_image_tag.attributes.get("content") if og_image_tag else ""

        tags = [
            tag.strip()
            for node in tree.css('meta[property="video:tag"]')
            for tag in (node.attributes.get("content") or "").split(",")
            if tag.strip()
        ]

        duration: int | None = None
        duration_tag = tree.css_first('meta[property="video:duration"]')
        if duration_tag is not None:
            raw_duration = duration_tag.attributes.get("content") or ""
            if raw_duration.isdigit():
                duration = int(raw_duration)

        player_response = await self._get(player_url, referer=video_page_url)
        if player_response is None:
            return None

        playlist = self.parse_playlist(player_response.text, video_id)
        if playlist is None:
            return None

        sources = sorted(
            playlist.get("sources") or [], key=self._quality_key, reverse=True
        )
        playlist.update(
            {
                "source": "noodle",
                "title": title,
                "image": image,
                "tags": tags,
                "duration": duration,
                "duration_text": _format_duration(duration),
                "player_url": player_url,
                "file_name": self._slug(title),
                "sources": sources,
                "actors": [],
                "timecodes": [],
                "related": [],
                "comments": [],
            }
        )
        return playlist

    @staticmethod
    def parse_playlist(html: str, video_id: str) -> dict | None:
        tree = LexborHTMLParser(html)
        for script in tree.css("script"):
            text = script.text(deep=True)
            match = re.search(r"window\.playlist\s*=\s*", text)
            if not match:
                continue
            try:
                data, _ = json.JSONDecoder().raw_decode(text[match.end() :])
            except json.JSONDecodeError:
                continue
            return {"id": video_id, **data}
        return None

    @staticmethod
    def _quality_key(source: dict) -> int:
        match = re.search(r"\d+", str(source.get("label", "")))
        return int(match.group()) if match else 0

    @staticmethod
    def _slug(title: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
        return slug or "video"
