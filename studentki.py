import asyncio
import base64
import re
import time
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

from curl_cffi import CurlOpt
from curl_cffi.requests import AsyncSession
from curl_cffi.requests.errors import RequestsError
from selectolax.lexbor import LexborHTMLParser

from mirrors import MirrorManager
from noodle import _format_duration, _parse_duration

DEFAULT_CATEGORIES = [
    {"name": "Красивый секс", "path": "/hq-porn"},
    {"name": "Русское", "path": "/rus"},
    {"name": "Анал", "path": "/anal"},
    {"name": "Русское домашнее", "path": "/home-rus"},
    {"name": "Альтушки", "path": "/alt-girl"},
    {"name": "Измена", "path": "/cheating"},
    {"name": "Жёсткое", "path": "/rough"},
    {"name": "Мамочки", "path": "/milfs"},
    {"name": "Большие сиськи", "path": "/big-tits"},
    {"name": "Женские оргазмы", "path": "/woman-orgasms"},
    {"name": "Кончают внутрь", "path": "/creampie"},
    {"name": "Русское и разговоры", "path": "/talking-rus"},
]

FEED_SHORTCUTS = {
    "trending": "/videos/trending",
    "popular": "/videos/feed",
    "feed": "/videos/feed",
    "new": "/videos",
    "latest": "/videos",
}


def _parse_iso_duration(val: str | None) -> int | None:
    if not val:
        return None
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", val.strip(), re.IGNORECASE)
    if not m:
        return None
    h, mins, s = (int(x or 0) for x in m.groups())
    total = h * 3600 + mins * 60 + s
    return total or None


class SexStudentkiScraper:
    """Async scraper for sex-studentki mirrors with automatic mirror failover."""

    def __init__(
        self,
        mirror_manager: MirrorManager,
        impersonate: str = "chrome",
        timeout: int = 15,
    ):
        self.mirrors = mirror_manager
        self.timeout = timeout
        self.session = AsyncSession(
            impersonate=impersonate,
            timeout=timeout,
            max_clients=30,
            curl_options={CurlOpt.IPRESOLVE: 1},
        )
        self._stream_cache: dict[str, tuple[str, str, float]] = {}
        self._home_cache: tuple[dict, float] | None = None

    async def close(self):
        await self.session.close()

    @staticmethod
    def _origin(url: str) -> str:
        parts = urlparse(url)
        return f"{parts.scheme}://{parts.netloc}"

    async def _request(
        self,
        method: str,
        path_or_url: str,
        referer: str | None = None,
        data: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
    ):
        await self.mirrors.ensure_ready()
        tried: set[str] = set()
        is_abs = path_or_url.startswith(("http://", "https://"))

        for _ in range(3):
            origin = self.mirrors.best_origin
            url = (
                path_or_url
                if is_abs
                else urljoin(origin + "/", path_or_url.lstrip("/"))
            )
            current_origin = self._origin(url)
            if current_origin in tried and not is_abs:
                break
            tried.add(current_origin)

            headers: dict[str, str] = {}
            if referer:
                headers["Referer"] = (
                    referer
                    if referer.startswith("http")
                    else urljoin(current_origin + "/", referer.lstrip("/"))
                )
            if method == "POST":
                headers["Origin"] = current_origin
            if extra_headers:
                headers.update(extra_headers)

            try:
                if method == "POST":
                    resp = await self.session.post(
                        url,
                        data=data,
                        timeout=self.timeout,
                        headers=headers or None,
                        allow_redirects=True,
                    )
                else:
                    resp = await self.session.get(
                        url,
                        timeout=self.timeout,
                        headers=headers or None,
                        allow_redirects=True,
                    )
                if resp.status_code == 200 and "Just a moment..." not in resp.text:
                    return resp
                if resp.status_code in (403, 404):
                    return resp
            except RequestsError as exc:
                print(f"[Studentki] Error {method} {url}: {exc}")

            nxt = self.mirrors.promote_next(current_origin)
            if not nxt or is_abs:
                break

        return None

    async def _get(self, path_or_url: str, referer: str | None = None):
        return await self._request("GET", path_or_url, referer=referer)

    async def _post(
        self,
        path_or_url: str,
        data: dict[str, str],
        referer: str | None = None,
    ):
        return await self._request("POST", path_or_url, referer=referer, data=data)

    @staticmethod
    def _parse_video_card(node, base_url: str) -> dict | None:
        link = node.css_first("a[href]")
        if link is None:
            return None
        href = link.attributes.get("href") or ""
        parsed = urlparse(href)
        path = parsed.path.strip("/")
        if not path.startswith("video/"):
            return None
        video_id = path[len("video/") :]
        if not video_id:
            return None

        img = node.css_first("img.image") or node.css_first("img")
        raw_img = ""
        if img is not None:
            raw_img = img.attributes.get("data-src") or img.attributes.get("src") or ""
        image = urljoin(base_url + "/", raw_img) if raw_img else ""

        title = "Untitled"
        title_node = node.css_first("h3 .title") or node.css_first(".title")
        if title_node is not None:
            title = title_node.text(strip=True) or title
        elif img is not None:
            title = img.attributes.get("alt") or title

        duration_text = None
        views = None
        for col in node.css(".info-column"):
            if col.css_first(".icon-clock") is not None:
                for sp in col.css("span"):
                    t = sp.text(strip=True)
                    if ":" in t:
                        duration_text = t
                        break
            elif col.css_first(".icon-eye") is not None:
                for sp in col.css("span"):
                    t = sp.text(strip=True)
                    if t:
                        views = t
                        break

        duration_seconds = _parse_duration(duration_text) if duration_text else None
        models = [
            m.text(strip=True)
            for m in node.css(".column-model .model-name")
            if m.text(strip=True)
        ]

        return {
            "id": video_id,
            "numeric_id": node.attributes.get("id"),
            "source": "studentki",
            "title": title,
            "image": image,
            "duration": duration_seconds,
            "duration_text": _format_duration(duration_seconds) or duration_text,
            "views": views,
            "models": models,
            "url": urljoin(base_url + "/", href),
        }

    @staticmethod
    def _parse_profile_card(node, base_url: str) -> dict | None:
        link = node.css_first("a[href]")
        if link is None:
            return None
        href = link.attributes.get("href") or ""
        if not href:
            return None
        path = unquote(urlparse(href).path)

        title_node = node.css_first(".title")
        name = title_node.text(strip=True) if title_node else ""
        if not name:
            return None

        img = node.css_first("img.image") or node.css_first("img")
        raw_img = img.attributes.get("src") if img is not None else ""
        image = urljoin(base_url + "/", raw_img) if raw_img else ""

        count = None
        views = None
        for col in node.css(".info-column"):
            if col.css_first(".icon-videocam") is not None:
                sp = col.css_first("span")
                if sp:
                    count = sp.text(strip=True)
            elif col.css_first(".icon-eye") is not None:
                sp = col.css_first("span")
                if sp:
                    views = sp.text(strip=True)

        return {
            "name": name,
            "path": path,
            "count": count,
            "views": views,
            "image": image,
        }

    @staticmethod
    def _parse_listing_meta(tree: LexborHTMLParser, current_page: int) -> dict:
        sort_links = []
        for a in tree.css(".sort-links a.sort-link"):
            href = a.attributes.get("href") or ""
            label = a.text(strip=True) or ("Лента" if "feed" in href else "Сортировка")
            p = urlparse(href)
            sort_links.append(
                {
                    "label": label,
                    "path": unquote(p.path),
                    "f": parse_qs(p.query).get("f", [""])[0],
                    "selected": "selected" in (a.attributes.get("class") or ""),
                }
            )

        filters = []
        for el in tree.css(".filters-block a.filter, .filters-block button.filter"):
            cls = el.attributes.get("class") or ""
            is_selected = "filter-selected" in cls
            href = el.attributes.get("href") or ""
            badge = el.css_first(".badge")
            count = badge.text(strip=True) if badge else None

            label_parts = []
            for child in el.iter(include_text=True):
                if child.tag == "-text":
                    txt = child.text(deep=True).strip()
                    if txt:
                        label_parts.append(txt)
            label = " ".join(label_parts).strip()
            if not label:
                label = el.text(strip=True)

            target_path = ""
            target_f = ""
            if href:
                p = urlparse(href)
                target_path = unquote(p.path)
                target_f = parse_qs(p.query).get("f", [""])[0]

            filters.append(
                {
                    "label": label,
                    "path": target_path,
                    "f": target_f,
                    "count": count,
                    "selected": is_selected,
                    "is_category": "filter-category" in cls,
                }
            )

        max_page = 1
        for a in tree.css(".pagination a.pagenav"):
            txt = a.text(strip=True)
            if txt.isdigit():
                max_page = max(max_page, int(txt))

        desc_node = tree.css_first(".videos-page .description.block")
        description = desc_node.text(strip=True) if desc_node else ""
        title_node = tree.css_first("title")
        page_title = title_node.text(strip=True) if title_node else "Sex-Studentki"

        return {
            "sort_links": sort_links,
            "filters": filters,
            "max_page": max(max_page, current_page) if max_page > 1 else 1,
            "has_next": max_page > current_page,
            "description": description,
            "page_title": page_title,
        }

    @staticmethod
    def extract_stream_url(
        html: str, tree: LexborHTMLParser | None = None
    ) -> str | None:
        parsed_tree = tree or LexborHTMLParser(html)
        src_el = parsed_tree.css_first("video#player source[src]")
        if src_el is not None:
            raw = src_el.attributes.get("src")
            if raw:
                return raw
        m_link = re.search(r"vps_player_link_url\s*=\s*['\"]([^'\"]+)['\"]", html)
        if m_link:
            return m_link.group(1)
        return None

    async def get_home_sections(self, use_cache: bool = True) -> dict:
        now = time.time()
        if (
            use_cache
            and self._home_cache is not None
            and (now - self._home_cache[1]) < 300
        ):
            return self._home_cache[0]

        resp = await self._get("/")
        if resp is None or resp.status_code != 200:
            return {
                "base_url": self.mirrors.best_origin,
                "categories": DEFAULT_CATEGORIES,
                "feed": [],
                "latest": [],
                "trending": [],
            }

        base_url = self._origin(str(resp.url))
        tree = LexborHTMLParser(resp.text)

        categories = []
        seen_cats: set[str] = set()
        for a in tree.css(".categories-page .category a"):
            href = a.attributes.get("href") or ""
            label = a.css_first(".label")
            name = label.text(strip=True) if label else a.text(strip=True)
            cnt = a.css_first(".badge-count")
            img = a.css_first("img")
            raw_img = img.attributes.get("src") if img else ""
            if href and href not in seen_cats:
                seen_cats.add(href)
                categories.append(
                    {
                        "name": name,
                        "path": unquote(href),
                        "count": cnt.text(strip=True) if cnt else None,
                        "image": urljoin(base_url + "/", raw_img) if raw_img else "",
                    }
                )

        for a in tree.css(".submenu a.submenu-item"):
            href = a.attributes.get("href") or ""
            name = a.text(strip=True)
            if not href or href in ("/videos", "/categories", "/models"):
                continue
            if href not in seen_cats and "mobile-button" not in (
                a.attributes.get("class") or ""
            ):
                seen_cats.add(href)
                categories.append(
                    {
                        "name": name,
                        "path": unquote(href),
                        "count": None,
                        "image": "",
                    }
                )

        sections: dict[str, list[dict]] = {"feed": [], "latest": [], "trending": []}
        for sec_id in ("feed", "latest", "trending"):
            container = tree.css_first(f"#{sec_id}")
            if container is None:
                continue
            for node in container.css("div.video.trailer"):
                item = self._parse_video_card(node, base_url)
                if item:
                    sections[sec_id].append(item)

        data = {
            "base_url": base_url,
            "categories": categories or DEFAULT_CATEGORIES,
            **sections,
        }
        self._home_cache = (data, now)
        return data

    async def browse(
        self,
        path: str = "/videos",
        f: str | None = None,
        page: int = 1,
    ) -> dict:
        clean_path = "/" + path.strip("/")
        if clean_path == "/":
            clean_path = "/videos"

        encoded_path = quote(clean_path, safe="/")
        query_parts = []
        if f:
            query_parts.append(f"f={quote(f, safe=',')}")
        if page > 1:
            query_parts.append(f"page={page}")
        full_path = encoded_path + (f"?{'&'.join(query_parts)}" if query_parts else "")

        resp = await self._get(full_path)
        if resp is None or resp.status_code != 200:
            return {
                "source": "studentki",
                "error": "Failed to load Sex-Studentki listing",
                "path": clean_path,
                "f": f or "",
                "page": page,
                "videos": [],
                "filters": [],
                "sort_links": [],
                "has_next": False,
                "max_page": page,
            }

        base_url = self._origin(str(resp.url))
        tree = LexborHTMLParser(resp.text)
        videos = []
        for node in tree.css(".videos-page div.video.trailer"):
            item = self._parse_video_card(node, base_url)
            if item:
                videos.append(item)

        meta = self._parse_listing_meta(tree, page)
        return {
            "source": "studentki",
            "base_url": base_url,
            "path": clean_path,
            "f": f or "",
            "page": page,
            "videos": videos,
            **meta,
        }

    async def search_videos(self, query: str, page: int = 1) -> dict:
        q_clean = query.strip()
        shortcut = FEED_SHORTCUTS.get(q_clean.lower())
        if shortcut:
            res = await self.browse(path=shortcut, page=page)
            res["query"] = query
            res["profiles"] = []
            return res

        referer_path = f"/search?q={quote(q_clean)}"
        endpoint = "/search/?is_ajax=1&action=load_data"
        if page > 1:
            endpoint += f"&page={page}"

        resp = await self._post(
            endpoint,
            data={"query": q_clean},
            referer=referer_path,
        )
        if resp is None or resp.status_code != 200:
            return {
                "source": "studentki",
                "error": "Failed to search Sex-Studentki",
                "query": query,
                "page": page,
                "videos": [],
                "profiles": [],
                "has_next": False,
            }

        base_url = self._origin(str(resp.url))
        tree = LexborHTMLParser(resp.text)
        meta = self._parse_listing_meta(tree, page)

        if page > 1 and not tree.css(".pagination a.pagenav"):
            return {
                "source": "studentki",
                "base_url": base_url,
                "query": query,
                "page": page,
                "videos": [],
                "profiles": [],
                "has_next": False,
                "max_page": 1,
            }

        videos = []
        for node in tree.css("div.video.trailer"):
            item = self._parse_video_card(node, base_url)
            if item:
                videos.append(item)

        profiles = []
        for node in tree.css("div.video.category-styled"):
            prof = self._parse_profile_card(node, base_url)
            if prof:
                profiles.append(prof)

        return {
            "source": "studentki",
            "base_url": base_url,
            "query": query,
            "page": page,
            "videos": videos,
            "profiles": profiles,
            "has_next": meta["has_next"],
            "max_page": meta["max_page"],
        }

    async def get_video_by_id(self, video_id: str) -> dict | None:
        clean_id = video_id.strip("/").removeprefix("video/")

        resp = await self._get(f"/video/{clean_id}")
        if resp is None or resp.status_code != 200:
            return None

        video_page_url = str(resp.url)
        base_url = self._origin(video_page_url)
        html = resp.text
        tree = LexborHTMLParser(html)

        og_title = tree.css_first('meta[property="og:title"]')
        h1 = tree.css_first('h1[itemprop="name"]')
        title = (
            (og_title.attributes.get("content") if og_title else None)
            or (h1.text(strip=True) if h1 else None)
            or "Untitled"
        )

        player_el = tree.css_first("video#player")
        poster_attr = player_el.attributes.get("poster") if player_el else None
        og_img = tree.css_first('meta[property="og:image"]')
        raw_img = (
            poster_attr or (og_img.attributes.get("content") if og_img else "") or ""
        )
        image = urljoin(base_url + "/", raw_img) if raw_img else ""

        duration: int | None = None
        m_dur = re.search(r"VIDEO_DURATION\s*=\s*['\"](\d+)['\"]", html)
        if m_dur:
            duration = int(m_dur.group(1))
        else:
            dur_meta = tree.css_first('meta[itemprop="duration"]')
            if dur_meta:
                duration = _parse_iso_duration(dur_meta.attributes.get("content"))

        views_el = tree.css_first(".views-block")
        views = views_el.text(strip=True) if views_el else None

        desc_el = tree.css_first(".description-text")
        description = desc_el.text(strip=True) if desc_el else ""

        actors = []
        for a in tree.css(".parameters a.tag-modifier"):
            href = a.attributes.get("href") or ""
            name_node = a.css_first("[itemprop='name']") or a.css_first(".label")
            name = name_node.text(strip=True) if name_node else a.text(strip=True)
            kind = "model"
            if "/автор/" in unquote(href):
                kind = "author"
            elif "/студия/" in unquote(href):
                kind = "studio"
            if name:
                actors.append(
                    {
                        "name": name,
                        "kind": kind,
                        "path": unquote(href),
                    }
                )

        tags: list[dict] = []
        seen_tags: set[str] = set()
        tag_nodes = tree.css(".tags-alt a.button") or tree.css(".tags a.button")
        for a in tag_nodes:
            name = a.text(strip=True)
            href = a.attributes.get("href") or ""
            if name and name not in seen_tags:
                seen_tags.add(name)
                tags.append({"name": name, "path": unquote(href)})

        timecodes = []
        for tc in tree.css(".timecode-line .timecode-line-image"):
            sec_raw = tc.attributes.get("data-time") or ""
            if not sec_raw.isdigit():
                continue
            t_el = tc.css_first(".timecode-time")
            l_el = tc.css_first(".timecode-label")
            timecodes.append(
                {
                    "seconds": int(sec_raw),
                    "time_text": t_el.text(strip=True)
                    if t_el
                    else _format_duration(int(sec_raw)),
                    "label": l_el.text(strip=True) if l_el else "",
                }
            )

        related = []
        for node in tree.css(".videos-page.mini div.video.mini"):
            item = self._parse_video_card(node, base_url)
            if item:
                related.append(item)

        raw_stream = self.extract_stream_url(html, tree=tree)
        if not raw_stream:
            raw_stream = await self._fetch_download_link(clean_id, video_page_url)

        if not raw_stream:
            return None

        abs_stream = urljoin(base_url + "/", raw_stream)
        self._stream_cache[clean_id] = (abs_stream, video_page_url, time.time())

        proxy_url = f"/stream/studentki/{clean_id}"
        file_name = re.sub(r"[^a-z0-9]+", "_", clean_id.lower()).strip("_") or "video"

        return {
            "id": clean_id,
            "source": "studentki",
            "title": title,
            "image": image,
            "description": description,
            "views": views,
            "duration": duration,
            "duration_text": _format_duration(duration),
            "actors": actors,
            "tags": [t["name"] for t in tags],
            "tag_links": tags,
            "timecodes": timecodes,
            "related": related,
            "player_url": None,
            "stream_url": proxy_url,
            "upstream_stream_url": abs_stream,
            "file_name": file_name,
            "sources": [
                {
                    "label": "MP4",
                    "file": proxy_url,
                    "download_file": f"{proxy_url}?download=1",
                }
            ],
        }

    async def _fetch_download_link(self, clean_id: str, referer: str) -> str | None:
        resp = await self._get(f"/download/{clean_id}", referer=referer)
        if resp is None or resp.status_code != 200:
            return None
        tree = LexborHTMLParser(resp.text)
        btn = tree.css_first("a.download-button[href]")
        if btn is not None:
            return btn.attributes.get("href")
        m_dl = re.search(r"initDownloader\(['\"]([A-Za-z0-9+/=_-]+)['\"]\)", resp.text)
        if m_dl:
            try:
                return base64.b64decode(m_dl.group(1)).decode("utf-8", errors="ignore")
            except (ValueError, UnicodeDecodeError):
                return None
        return None

    async def get_stream_target(
        self, video_id: str, force_refresh: bool = False
    ) -> tuple[str, str] | None:
        clean_id = video_id.strip("/")
        cached = self._stream_cache.get(clean_id)
        if not force_refresh and cached and (time.time() - cached[2]) < 900:
            return cached[0], cached[1]
        details = await self.get_video_by_id(clean_id)
        if not details:
            return None
        cached = self._stream_cache.get(clean_id)
        if not cached:
            return None
        return cached[0], cached[1]


async def test_studentki_origin(
    session: AsyncSession, origin: str, home_html: str
) -> tuple[bool, str | None]:
    """Live functional API check on resolved origin for Sex-Studentki."""
    tree = LexborHTMLParser(home_html)
    first_id: str | None = None
    for node in tree.css("div.video.trailer"):
        card = SexStudentkiScraper._parse_video_card(node, origin)
        if card:
            first_id = card["id"]
            break

    if not first_id:
        return False, "API test: no /video/ cards parsed on homepage"

    search_req = session.post(
        f"{origin}/search/?is_ajax=1&action=load_data",
        data={"query": "секс"},
        headers={
            "Origin": origin,
            "Referer": f"{origin}/search?q=%D1%81%D0%B5%D0%BA%D1%81",
        },
        allow_redirects=True,
        timeout=5,
    )
    video_req = session.get(
        f"{origin}/video/{first_id}",
        allow_redirects=True,
        timeout=5,
    )
    s_resp, v_resp = await asyncio.gather(search_req, video_req, return_exceptions=True)

    if isinstance(s_resp, asyncio.CancelledError):
        raise s_resp
    if isinstance(v_resp, asyncio.CancelledError):
        raise v_resp

    if isinstance(s_resp, BaseException) or s_resp.status_code != 200:
        return False, "API test: AJAX search failed"
    s_tree = LexborHTMLParser(s_resp.text)
    search_hits = [
        c
        for n in s_tree.css("div.video.trailer")
        if (c := SexStudentkiScraper._parse_video_card(n, origin)) is not None
    ]
    if not search_hits:
        return False, "API test: AJAX search returned 0 cards"

    if isinstance(v_resp, BaseException) or v_resp.status_code != 200:
        return False, "API test: video page failed"
    v_tree = LexborHTMLParser(v_resp.text)
    if (
        SexStudentkiScraper.extract_stream_url(v_resp.text, tree=v_tree) is None
        and v_tree.css_first("video#player") is None
    ):
        return False, "API test: video player not found"

    return True, None
