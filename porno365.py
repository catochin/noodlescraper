import asyncio
import re
import time
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

from curl_cffi import CurlOpt
from curl_cffi.requests import AsyncSession
from curl_cffi.requests.errors import RequestsError
from selectolax.lexbor import LexborHTMLParser

from mirrors import MirrorManager
from noodle import _format_duration, _parse_duration

DEFAULT_PORNO365_CATEGORIES = [
    {"name": "Русское", "path": "/russkoe"},
    {"name": "Анал", "path": "/anal"},
    {"name": "Большие сиськи", "path": "/bolshie-siski"},
    {"name": "Домашнее", "path": "/domashnee"},
    {"name": "Измена", "path": "/izmena"},
    {"name": "Мамки", "path": "/mamki"},
    {"name": "Молодые", "path": "/molodye"},
    {"name": "Минет", "path": "/minet"},
    {"name": "Азиатки", "path": "/asian"},
    {"name": "Групповое", "path": "/groupsex"},
    {"name": "Кастинг", "path": "/kasting"},
    {"name": "Премиум", "path": "/premium"},
]

PORNO365_SHORTCUTS = {
    "trending": "/popular/week",
    "popular": "/popular/week",
    "toprated": "/toprated/week",
    "new": "/",
    "latest": "/",
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


def _parse_cc_onclick(attr: str | None) -> tuple[str, str]:
    if not attr:
        return "", ""
    m = re.search(r"getVideosCC\(\s*['\"]([^'\"]+)['\"]\s*\)", attr)
    if not m:
        return "", ""
    p = urlparse(m.group(1))
    return unquote(p.path), parse_qs(p.query).get("cc", [""])[0]


class Porno365Scraper:
    """Async scraper for Porno365 mirrors with SSL-bypass redirect resolution."""

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
            verify=False,
            curl_options={CurlOpt.IPRESOLVE: 1},
        )
        self._stream_cache: dict[str, tuple[list[dict], str, float]] = {}
        self._home_cache: tuple[dict, float] | None = None
        self._search_next_cache: dict[tuple[str, int], str] = {}

    async def close(self):
        await self.session.close()

    @staticmethod
    def _origin(url: str) -> str:
        parts = urlparse(url)
        return f"{parts.scheme}://{parts.netloc}"

    async def _get(self, path_or_url: str, referer: str | None = None):
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

            headers = None
            if referer:
                ref = (
                    referer
                    if referer.startswith("http")
                    else urljoin(current_origin + "/", referer.lstrip("/"))
                )
                headers = {"Referer": ref}

            try:
                resp = await self.session.get(
                    url,
                    timeout=self.timeout,
                    headers=headers,
                    allow_redirects=True,
                    verify=False,
                )
                if resp.status_code == 200 and "Just a moment..." not in resp.text:
                    return resp
                if resp.status_code in (403, 404):
                    return resp
            except RequestsError as exc:
                print(f"[Porno365] Error loading {url}: {exc}")

            nxt = self.mirrors.promote_next(current_origin)
            if not nxt or is_abs:
                break

        return None

    @staticmethod
    def _parse_video_card(node, base_url: str) -> dict | None:
        link = node.css_first("a.image[href]") or node.css_first("a[href]")
        if link is None:
            return None
        href = link.attributes.get("href") or ""
        path = urlparse(href).path.strip("/")
        if not path.startswith("movie/"):
            return None
        video_id = path[len("movie/") :].split("/", 1)[0]
        if not video_id:
            return None

        img = node.css_first("img")
        raw_img = ""
        if img is not None:
            raw_img = img.attributes.get("data-src") or img.attributes.get("src") or ""
        image = urljoin(base_url + "/", raw_img) if raw_img else ""

        title = "Untitled"
        p_node = link.css_first("p")
        if p_node is not None:
            title = p_node.text(strip=True) or title
        elif img is not None:
            title = img.attributes.get("alt") or title

        dur_node = node.css_first("span.duration")
        duration_text = dur_node.text(strip=True) if dur_node else None
        duration_seconds = _parse_duration(duration_text) if duration_text else None

        views_node = node.css_first("span.video_views")
        views = views_node.text(strip=True) if views_node else None

        rating_node = node.css_first("span.mini-rating")
        rating = rating_node.text(strip=True) if rating_node else None

        return {
            "id": video_id,
            "source": "porno365",
            "title": title,
            "image": image,
            "duration": duration_seconds,
            "duration_text": _format_duration(duration_seconds) or duration_text,
            "views": views,
            "rating": rating,
            "models": [],
            "url": urljoin(base_url + "/", href),
        }

    @staticmethod
    def _parse_model_card(node, base_url: str) -> dict | None:
        link = node.css_first("a[href]")
        if link is None:
            return None
        href = link.attributes.get("href") or ""
        if not href:
            return None
        path = unquote(urlparse(href).path)

        rus = node.css_first(".model_rus_name")
        eng = node.css_first(".model_eng_name")
        img = node.css_first("img")
        name = (
            (rus.text(strip=True) if rus else "")
            or (eng.text(strip=True) if eng else "")
            or (img.attributes.get("alt") if img else "")
            or ""
        )
        if not name:
            return None

        raw_img = img.attributes.get("src") if img is not None else ""
        image = urljoin(base_url + "/", raw_img) if raw_img else ""
        cnt_node = node.css_first(".cnt_span")
        count = cnt_node.text(strip=True) if cnt_node else None

        return {
            "source": "porno365",
            "name": name,
            "eng_name": eng.text(strip=True) if eng else "",
            "path": path,
            "count": count,
            "image": image,
        }

    @staticmethod
    def _parse_listing_meta(tree: LexborHTMLParser, current_page: int) -> dict:
        sort_links: list[dict] = []
        for div in tree.css(".stext_wrapper .div_sort"):
            is_selected = "active_sort" in (div.attributes.get("class") or "")
            sp = div.css_first("span[onclick]")
            if sp is not None:
                p, cc = _parse_cc_onclick(sp.attributes.get("onclick"))
                if p:
                    sort_links.append(
                        {
                            "label": sp.text(strip=True),
                            "path": p,
                            "f": cc,
                            "selected": is_selected,
                        }
                    )
                continue

            sub_links = div.css("ul.sort_ul a[href]")
            if sub_links:
                group_title = (div.css_first("span") or div).text(strip=True)
                for a in sub_links:
                    parsed = urlparse(a.attributes.get("href") or "")
                    lbl = a.text(strip=True)
                    sort_links.append(
                        {
                            "label": f"{group_title}: {lbl}" if group_title else lbl,
                            "path": unquote(parsed.path) or "/",
                            "f": parse_qs(parsed.query).get("cc", [""])[0],
                            "selected": False,
                        }
                    )
            else:
                direct_a = div.css_first("a[href]")
                if direct_a is not None:
                    parsed = urlparse(direct_a.attributes.get("href") or "")
                    sort_links.append(
                        {
                            "label": direct_a.text(strip=True),
                            "path": unquote(parsed.path) or "/",
                            "f": parse_qs(parsed.query).get("cc", [""])[0],
                            "selected": is_selected,
                        }
                    )

        filters: list[dict] = []
        for act in tree.css(".video-cats-active span[onclick]"):
            p, cc = _parse_cc_onclick(act.attributes.get("onclick"))
            lbl = act.text(strip=True)
            if lbl:
                filters.append(
                    {
                        "label": lbl,
                        "path": p or "/",
                        "f": cc,
                        "count": None,
                        "selected": True,
                        "is_category": False,
                    }
                )

        for sl in tree.css(".horizontal-scroll span.slide"):
            onclick = sl.attributes.get("data-click") or sl.attributes.get("onclick")
            p, cc = _parse_cc_onclick(onclick)
            if not p:
                continue
            cnt_node = sl.css_first(".dopcat_cnt")
            count = cnt_node.text(strip=True) if cnt_node else None

            parts: list[str] = []
            for child in sl.iter(include_text=True):
                if child.tag == "-text":
                    txt = child.text(deep=True).strip()
                    if txt:
                        parts.append(txt)
            label = " ".join(parts).strip() or sl.text(strip=True)

            filters.append(
                {
                    "label": label,
                    "path": p,
                    "f": cc,
                    "count": count,
                    "selected": False,
                    "is_category": False,
                }
            )

        next_node = tree.css_first(".navigation .next_link")
        next_href = ""
        if next_node is not None:
            next_href = (
                next_node.attributes.get("data-href")
                or next_node.attributes.get("href")
                or ""
            )

        desc_node = tree.css_first(".cat_description")
        description = desc_node.text(strip=True) if desc_node else ""
        h1_node = tree.css_first("#content h1")
        title_node = tree.css_first("title")
        page_title = (
            (h1_node.text(strip=True) if h1_node else "")
            or (title_node.text(strip=True) if title_node else "")
            or "Porno365"
        )

        return {
            "sort_links": sort_links,
            "filters": filters,
            "has_next": bool(next_node),
            "next_href": next_href,
            "max_page": current_page + (1 if next_node else 0),
            "description": description,
            "page_title": page_title,
        }

    @staticmethod
    def _quality_key(source: dict) -> int:
        m = re.search(r"\d+", str(source.get("label", "")))
        return int(m.group()) if m else 0

    @classmethod
    def extract_stream_sources(
        cls, html: str, tree: LexborHTMLParser | None = None
    ) -> list[dict]:
        upstream_sources: list[dict] = []
        m_sources = re.search(r"sources\s*:\s*\[(.*?)\]\s*,", html, re.DOTALL)
        if m_sources:
            for block_m in re.finditer(r"\{([^}]+)\}", m_sources.group(1)):
                block = block_m.group(1)
                m_file = re.search(r"""file\s*:\s*["']([^"']+)["']""", block)
                m_label = re.search(r"""label\s*:\s*["']([^"']+)["']""", block)
                if m_file:
                    upstream_sources.append(
                        {
                            "label": m_label.group(1) if m_label else "MP4",
                            "upstream_url": m_file.group(1),
                        }
                    )

        if not upstream_sources:
            parsed_tree = tree or LexborHTMLParser(html)
            og_vid = parsed_tree.css_first(
                'meta[property="og:video"]'
            ) or parsed_tree.css_first('meta[itemprop="contentUrl"]')
            og_vid_url = og_vid.attributes.get("content") if og_vid else None
            if og_vid_url:
                upstream_sources.append(
                    {
                        "label": "720p HD",
                        "upstream_url": og_vid_url,
                    }
                )

        upstream_sources.sort(key=cls._quality_key, reverse=True)
        return upstream_sources

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
                "categories": DEFAULT_PORNO365_CATEGORIES,
                "latest": [],
            }

        base_url = self._origin(str(resp.url))
        tree = LexborHTMLParser(resp.text)

        categories: list[dict] = []
        seen_paths: set[str] = set()
        for a in tree.css("#menu ul.top-menu li a"):
            href = a.attributes.get("href") or ""
            path = unquote(urlparse(href).path)
            name = a.text(strip=True)
            if not path or path == "/categories" or path in seen_paths or not name:
                continue
            seen_paths.add(path)
            categories.append({"name": name, "path": path, "count": None, "image": ""})

        latest: list[dict] = []
        for node in tree.css("#video-content li.video_block"):
            item = self._parse_video_card(node, base_url)
            if item:
                latest.append(item)

        data = {
            "base_url": base_url,
            "categories": categories or DEFAULT_PORNO365_CATEGORIES,
            "latest": latest,
        }
        self._home_cache = (data, now)
        return data

    async def browse(
        self,
        path: str = "/",
        cc: str | None = None,
        page: int = 1,
    ) -> dict:
        clean_path = "/" + path.strip("/") if path.strip("/") else "/"
        if page > 1:
            target_path = (
                f"/{page}" if clean_path == "/" else f"{clean_path.rstrip('/')}/{page}"
            )
        else:
            target_path = clean_path

        encoded_path = quote(target_path, safe="/")
        if cc:
            encoded_path += f"?cc={quote(cc, safe=',')}"

        resp = await self._get(encoded_path)
        if resp is None or resp.status_code != 200:
            return {
                "source": "porno365",
                "error": "Failed to load Porno365 listing",
                "path": clean_path,
                "f": cc or "",
                "page": page,
                "videos": [],
                "models_grid": [],
                "filters": [],
                "sort_links": [],
                "has_next": False,
                "max_page": page,
            }

        base_url = self._origin(str(resp.url))
        tree = LexborHTMLParser(resp.text)

        videos = []
        for node in tree.css("#video-content li.video_block"):
            item = self._parse_video_card(node, base_url)
            if item:
                videos.append(item)

        models_grid = []
        if not videos:
            for node in tree.css(".list_of_models .item_model"):
                m_item = self._parse_model_card(node, base_url)
                if m_item:
                    models_grid.append(m_item)

        meta = self._parse_listing_meta(tree, page)
        return {
            "source": "porno365",
            "base_url": base_url,
            "path": clean_path,
            "f": cc or "",
            "page": page,
            "videos": videos,
            "models_grid": models_grid,
            **meta,
        }

    async def search_models(self, query: str) -> list[dict]:
        q_clean = query.strip()
        if not q_clean:
            return []
        resp = await self._get(
            f"/?do=ajax&action=searchModel&model={quote(q_clean)}",
            referer="/models",
        )
        if resp is None or resp.status_code != 200:
            return []

        base_url = self._origin(str(resp.url))
        tree = LexborHTMLParser(resp.text)
        models: list[dict] = []
        for node in tree.css("div.item_model"):
            item = self._parse_model_card(node, base_url)
            if item:
                models.append(item)
        return models

    async def search_videos(self, query: str, page: int = 1) -> dict:
        q_clean = query.strip()
        shortcut = PORNO365_SHORTCUTS.get(q_clean.lower())
        if shortcut:
            res = await self.browse(path=shortcut, page=page)
            res["query"] = query
            res["profiles"] = []
            return res

        if page == 1:
            search_path = f"/search/?q={quote(q_clean)}"
            v_resp, profiles = await asyncio.gather(
                self._get(search_path),
                self.search_models(q_clean),
            )
        else:
            cached_next = self._search_next_cache.get((q_clean.lower(), page))
            if not cached_next:
                return {
                    "source": "porno365",
                    "query": query,
                    "page": page,
                    "videos": [],
                    "profiles": [],
                    "has_next": False,
                    "max_page": page - 1,
                }
            v_resp = await self._get(cached_next)
            profiles = []

        if v_resp is None or v_resp.status_code != 200:
            return {
                "source": "porno365",
                "error": "Failed to search Porno365",
                "query": query,
                "page": page,
                "videos": [],
                "profiles": profiles,
                "has_next": False,
            }

        base_url = self._origin(str(v_resp.url))
        tree = LexborHTMLParser(v_resp.text)

        videos = []
        for node in tree.css("#video-content li.video_block"):
            item = self._parse_video_card(node, base_url)
            if item:
                videos.append(item)

        meta = self._parse_listing_meta(tree, page)
        if meta["next_href"]:
            next_p = urlparse(meta["next_href"])
            rel = next_p.path + (f"?{next_p.query}" if next_p.query else "")
            self._search_next_cache[(q_clean.lower(), page + 1)] = rel

        return {
            "source": "porno365",
            "base_url": base_url,
            "query": query,
            "page": page,
            "videos": videos,
            "profiles": profiles,
            "has_next": meta["has_next"],
            "max_page": meta["max_page"],
        }

    async def get_video_by_id(self, video_id: str) -> dict | None:
        clean_id = video_id.strip("/").removeprefix("movie/")
        resp = await self._get(f"/movie/{clean_id}")
        if resp is None or resp.status_code != 200:
            return None

        video_page_url = str(resp.url)
        base_url = self._origin(video_page_url)
        html = resp.text
        tree = LexborHTMLParser(html)

        h1 = tree.css_first('h1[itemprop="name"]')
        og_title = tree.css_first('meta[property="og:title"]')
        title = (
            (h1.text(strip=True) if h1 else None)
            or (og_title.attributes.get("content") if og_title else None)
            or "Untitled"
        )

        og_img = tree.css_first('meta[property="og:image"]')
        raw_img = og_img.attributes.get("content") if og_img else ""
        m_jw_img = re.search(
            r'jwplayer\("player_div"\)\.setup\(\{\s*image\s*:\s*[\'"]([^\'"]+)[\'"]',
            html,
        )
        if m_jw_img and not raw_img:
            raw_img = m_jw_img.group(1)
        image = urljoin(base_url + "/", raw_img) if raw_img else ""

        duration: int | None = None
        dur_tag = tree.css_first('meta[property="video:duration"]')
        dur_raw = dur_tag.attributes.get("content") if dur_tag else None
        if dur_raw and dur_raw.isdigit():
            duration = int(dur_raw)
        else:
            iso_tag = tree.css_first('meta[itemprop="duration"]')
            if iso_tag:
                duration = _parse_iso_duration(iso_tag.attributes.get("content"))

        views_el = tree.css_first('.video-views span[itemprop="interactionCount"]')
        views = views_el.text(strip=True) if views_el else None

        desc_el = tree.css_first(".story_desription")
        og_desc = tree.css_first('meta[property="og:description"]')
        description = (
            (desc_el.text(strip=True) if desc_el else None)
            or (og_desc.attributes.get("content") if og_desc else None)
            or ""
        )

        actors: list[dict] = []
        for a in tree.css(".video-categories.video-models a.model_link"):
            name = a.text(strip=True)
            href = a.attributes.get("href") or ""
            if name:
                actors.append(
                    {
                        "name": name,
                        "kind": "model",
                        "path": unquote(urlparse(href).path),
                    }
                )

        categories: list[dict] = []
        for block in tree.css(".video-categories"):
            if "video-models" in (block.attributes.get("class") or ""):
                continue
            for a in block.css("a"):
                name = a.text(strip=True)
                href = a.attributes.get("href") or ""
                if name:
                    categories.append(
                        {"name": name, "path": unquote(urlparse(href).path)}
                    )

        tags: list[dict] = []
        for a in tree.css(".video-tags a"):
            name = a.text(strip=True).lstrip("#").strip()
            href = a.attributes.get("href") or ""
            if name:
                tags.append({"name": name, "path": unquote(urlparse(href).path)})

        timecodes: list[dict] = []
        seen_tc: set[int] = set()
        for tc in tree.css(".comment_text span.seekVideoTo[onclick]"):
            m_sec = re.search(r"peremotka\((\d+)\)", tc.attributes.get("onclick") or "")
            if not m_sec:
                continue
            sec = int(m_sec.group(1))
            if sec in seen_tc:
                continue
            seen_tc.add(sec)
            parent_txt = tc.parent.text(strip=True) if tc.parent else ""
            snippet = parent_txt[:70] + ("..." if len(parent_txt) > 70 else "")
            timecodes.append(
                {
                    "seconds": sec,
                    "time_text": tc.text(strip=True) or _format_duration(sec),
                    "label": snippet,
                }
            )

        related: list[dict] = []
        for node in tree.css(".related .related_video"):
            item = self._parse_video_card(node, base_url)
            if item:
                related.append(item)

        upstream_sources = self.extract_stream_sources(html, tree=tree)
        if not upstream_sources:
            return None

        self._stream_cache[clean_id] = (upstream_sources, video_page_url, time.time())

        proxy_sources = []
        for idx, src in enumerate(upstream_sources):
            proxy_url = f"/stream/porno365/{clean_id}?q={idx}"
            proxy_sources.append(
                {
                    "label": src["label"],
                    "file": proxy_url,
                    "download_file": f"{proxy_url}&download=1",
                }
            )

        default_stream = proxy_sources[0]["file"]
        file_name = f"porno365_{clean_id}"

        return {
            "id": clean_id,
            "source": "porno365",
            "title": title,
            "image": image,
            "description": description,
            "views": views,
            "duration": duration,
            "duration_text": _format_duration(duration),
            "actors": actors,
            "categories": categories,
            "tags": [t["name"] for t in tags],
            "tag_links": tags,
            "timecodes": timecodes,
            "related": related,
            "player_url": None,
            "stream_url": default_stream,
            "file_name": file_name,
            "sources": proxy_sources,
        }

    async def get_stream_target(
        self,
        video_id: str,
        quality_idx: int = 0,
        force_refresh: bool = False,
    ) -> tuple[str, str] | None:
        clean_id = video_id.strip("/")
        cached = self._stream_cache.get(clean_id)
        if not force_refresh and cached and (time.time() - cached[2]) < 900:
            sources, ref, _ = cached
        else:
            details = await self.get_video_by_id(clean_id)
            if not details:
                return None
            cached = self._stream_cache.get(clean_id)
            if not cached:
                return None
            sources, ref, _ = cached

        if not sources:
            return None
        idx = max(0, min(quality_idx, len(sources) - 1))
        return sources[idx]["upstream_url"], ref


async def test_porno365_origin(
    session: AsyncSession, origin: str, home_html: str
) -> tuple[bool, str | None]:
    """Live functional API check on resolved origin to filter out fake clones."""
    tree = LexborHTMLParser(home_html)
    first_id: str | None = None
    for node in tree.css("#video-content li.video_block"):
        card = Porno365Scraper._parse_video_card(node, origin)
        if card:
            first_id = card["id"]
            break

    if not first_id:
        return False, "API test: no /movie/ cards parsed on homepage"

    search_req = session.get(
        f"{origin}/search/?q=%D1%81%D0%B5%D0%BA%D1%81",
        allow_redirects=True,
        verify=False,
        timeout=5,
    )
    movie_req = session.get(
        f"{origin}/movie/{first_id}",
        allow_redirects=True,
        verify=False,
        timeout=5,
    )
    s_resp, m_resp = await asyncio.gather(search_req, movie_req, return_exceptions=True)

    if isinstance(s_resp, asyncio.CancelledError):
        raise s_resp
    if isinstance(m_resp, asyncio.CancelledError):
        raise m_resp

    if isinstance(s_resp, BaseException) or s_resp.status_code != 200:
        return False, "API test: search endpoint failed"
    s_tree = LexborHTMLParser(s_resp.text)
    search_hits = [
        c
        for n in s_tree.css("#video-content li.video_block")
        if (c := Porno365Scraper._parse_video_card(n, origin)) is not None
    ]
    if not search_hits:
        return False, "API test: search returned 0 Porno365 cards"

    if isinstance(m_resp, BaseException) or m_resp.status_code != 200:
        return False, "API test: video page failed"
    streams = Porno365Scraper.extract_stream_sources(m_resp.text)
    if not streams:
        return False, "API test: no playable MP4 sources extracted"

    return True, None
