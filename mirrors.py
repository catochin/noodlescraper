import asyncio
import base64
import json
import logging
import socket
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from curl_cffi import CurlHttpVersion, CurlOpt
from curl_cffi.requests import AsyncSession
from curl_cffi.requests.errors import RequestsError
from selectolax.lexbor import LexborHTMLParser

logger = logging.getLogger(__name__)

BING_URL = "https://www.bing.com/search"

ApiValidator = Callable[[AsyncSession, str, str], Awaitable[tuple[bool, str | None]]]


def _decode_bing_href(raw_href: str | None) -> str | None:
    if not raw_href:
        return None

    parsed = urlparse(raw_href)
    if parsed.path == "/ck/a" and (
        not parsed.netloc or parsed.netloc.lower().endswith("bing.com")
    ):
        qs = parse_qs(parsed.query.lstrip("!"))
        u_val = qs.get("u", [None])[0]
        if not u_val:
            return None

        if u_val.startswith("a1"):
            b64 = u_val[2:]
            b64 += "=" * (-len(b64) % 4)
            try:
                raw_href = base64.urlsafe_b64decode(b64).decode(
                    "utf-8", errors="ignore"
                )
            except (ValueError, UnicodeDecodeError):
                return None
        elif u_val.startswith(("http://", "https://")):
            raw_href = unquote(u_val)
        else:
            return None

        parsed = urlparse(raw_href)

    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None

    return raw_href


def _resolve_ipv4(host: str) -> str:
    addr = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    return str(addr)


def _origin(url: str) -> str:
    parts = urlparse(url)
    return f"{parts.scheme}://{parts.netloc}"


def is_valid_studentki(status: int, html: str) -> bool:
    return (
        status == 200
        and ("videos-page" in html or "/front/css/" in html)
        and "Just a moment..." not in html
    )


def is_valid_porno365(status: int, html: str) -> bool:
    low = html.lower()
    return (
        status == 200
        and ("video_block" in low or "videos_ul" in low or "item_model" in low)
        and "just a moment..." not in low
    )


class MirrorManager:
    """Discovers mirrors via Bing, resolves redirects, runs live API checks, and refreshes non-blockingly."""

    def __init__(
        self,
        site_name: str,
        bing_query: str,
        domain_needle: str,
        fallback_domains: list[str],
        cache_file: Path,
        verify_ssl: bool = True,
        fallback_http: bool = False,
        bing_pages: int = 2,
        cache_ttl_seconds: int = 600,
        impersonate: str = "chrome",
        validator: Callable[[int, str], bool] | None = None,
        api_validator: ApiValidator | None = None,
    ):
        self.site_name = site_name
        self.bing_query = bing_query
        self.domain_needle = domain_needle
        self.fallback_domains = fallback_domains
        self.cache_file = cache_file
        self.verify_ssl = verify_ssl
        self.fallback_http = fallback_http
        self.bing_pages = bing_pages
        self.cache_ttl_seconds = cache_ttl_seconds
        self.impersonate = impersonate
        self.validator = validator or is_valid_studentki
        self.api_validator = api_validator

        self.refresh_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task[list[dict]] | None = None
        self._first_ready = asyncio.Event()

        self.mirrors: list[dict] = []
        default_scheme = "https" if verify_ssl else "http"
        self.best_origin: str = f"{default_scheme}://{fallback_domains[0]}"
        self.last_checked: float = 0.0
        self.discovery_source: str = "default"
        self.last_error: str | None = None

        self.hydrate_from_cache()

    def hydrate_from_cache(self) -> bool:
        cached = self._load_cache()
        if not cached:
            return False
        mirrors = cached.get("mirrors")
        best_origin = cached.get("best_origin")
        if isinstance(mirrors, list) and mirrors:
            self.mirrors = mirrors
            self.last_checked = float(cached.get("ts") or 0.0)
            self.discovery_source = str(cached.get("discovery_source") or "cache")
            if isinstance(best_origin, str) and best_origin.startswith(
                ("http://", "https://")
            ):
                self.best_origin = best_origin
            else:
                alive = [m for m in mirrors if m.get("alive") and m.get("origin")]
                if alive:
                    self.best_origin = str(alive[0]["origin"])
            self._first_ready.set()
            return True
        return False

    async def discover_from_bing(self) -> list[str]:
        bing_ip = await asyncio.to_thread(_resolve_ipv4, "www.bing.com")
        cookies = {"SRCHHPGUSR": "ADLT=OFF"}

        domains: list[str] = []
        seen_domains: set[str] = set()

        async with AsyncSession(
            impersonate=self.impersonate,
            timeout=8,
            http_version=CurlHttpVersion.V2_0,
            curl_options={
                CurlOpt.IPRESOLVE: 1,
                CurlOpt.RESOLVE: [f"www.bing.com:443:{bing_ip}"],
            },
        ) as session:

            async def _fetch_page(page_idx: int) -> str | None:
                params = {
                    "q": self.bing_query,
                    "form": "SAFESH",
                    "adlt": "off",
                }
                if page_idx > 0:
                    params["first"] = str(page_idx * 10 + 1)

                resp = await asyncio.wait_for(
                    session.get(
                        BING_URL,
                        params=params,
                        cookies=cookies,
                        allow_redirects=True,
                    ),
                    timeout=9,
                )
                return resp.text

            pages_html = await asyncio.gather(
                *(_fetch_page(i) for i in range(self.bing_pages)),
                return_exceptions=True,
            )

            for item in pages_html:
                if isinstance(item, asyncio.CancelledError):
                    raise item
                if isinstance(item, BaseException) or not item:
                    continue

                tree = LexborHTMLParser(item)
                links = tree.css("li.b_algo a[href]") or tree.css(
                    "#b_results h2 a[href]"
                )
                for node in links:
                    clean_url = _decode_bing_href(node.attributes.get("href"))
                    if not clean_url:
                        continue
                    domain = (
                        urlparse(clean_url)
                        .netloc.lower()
                        .split(":")[0]
                        .removeprefix("www.")
                    )
                    if self.domain_needle not in domain:
                        continue
                    if domain not in seen_domains:
                        seen_domains.add(domain)
                        domains.append(domain)

        return domains

    async def _probe_domain(
        self, session: AsyncSession, sem: asyncio.Semaphore, domain: str
    ) -> tuple[dict, str]:
        schemes = ["https", "http"] if self.fallback_http else ["https"]
        async with sem:
            last_exc: Exception | None = None
            for scheme in schemes:
                url = f"{scheme}://{domain}/"
                t0 = time.perf_counter()
                try:
                    resp = await asyncio.wait_for(
                        session.get(
                            url,
                            allow_redirects=True,
                            verify=self.verify_ssl,
                            timeout=3.5,
                        ),
                        timeout=4.5,
                    )
                    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
                    html = resp.text or ""
                    is_real = self.validator(resp.status_code, html)
                    final_origin = (
                        _origin(str(resp.url)) if resp.url else url.rstrip("/")
                    )
                    if is_real or scheme == schemes[-1]:
                        return (
                            {
                                "domain": domain,
                                "origin": final_origin,
                                "alive": is_real,
                                "api_verified": False,
                                "status": resp.status_code,
                                "latency_ms": elapsed_ms,
                                "error": None
                                if is_real
                                else f"Unexpected HTTP {resp.status_code}",
                            },
                            html if is_real else "",
                        )
                except (RequestsError, TimeoutError, OSError, ValueError) as exc:
                    last_exc = exc
                    if scheme == schemes[-1]:
                        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
                        return (
                            {
                                "domain": domain,
                                "origin": url.rstrip("/"),
                                "alive": False,
                                "api_verified": False,
                                "status": None,
                                "latency_ms": elapsed_ms,
                                "error": f"{type(exc).__name__}: {exc}",
                            },
                            "",
                        )

            return (
                {
                    "domain": domain,
                    "origin": f"https://{domain}",
                    "alive": False,
                    "api_verified": False,
                    "status": None,
                    "latency_ms": 9999.0,
                    "error": str(last_exc) if last_exc else "Unknown probe error",
                },
                "",
            )

    def _load_cache(self) -> dict | None:
        if not self.cache_file.exists():
            return None
        try:
            return json.loads(self.cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def _save_cache(self) -> None:
        try:
            payload = {
                "ts": self.last_checked,
                "best_origin": self.best_origin,
                "discovery_source": self.discovery_source,
                "mirrors": self.mirrors,
            }
            self.cache_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("[%s] Failed to write mirror cache: %s", self.site_name, exc)

    async def refresh(self, force: bool = False) -> list[dict]:
        async with self.refresh_lock:
            now = time.time()
            if (
                not force
                and self.mirrors
                and self.last_checked > 0
                and (now - self.last_checked) < self.cache_ttl_seconds
            ):
                self._first_ready.set()
                return self.mirrors

            candidates: list[str] = []
            self.last_error = None
            source_used = "fallback"
            try:
                candidates = await self.discover_from_bing()
                if candidates:
                    source_used = "bing"
            except (RequestsError, TimeoutError, OSError, ValueError) as exc:
                self.last_error = f"Bing discovery failed: {type(exc).__name__}: {exc}"

            if not candidates:
                cached = self._load_cache()
                if cached and cached.get("mirrors"):
                    candidates = [
                        m["domain"] for m in cached["mirrors"] if m.get("domain")
                    ]
                    source_used = "cache"

            if not candidates:
                candidates = list(self.fallback_domains)
                source_used = "fallback"

            for fb in self.fallback_domains:
                if fb not in candidates:
                    candidates.append(fb)

            sem = asyncio.Semaphore(12)
            async with AsyncSession(
                impersonate=self.impersonate,
                timeout=4,
                verify=self.verify_ssl,
                curl_options={CurlOpt.IPRESOLVE: 1},
            ) as probe_session:
                probe_raw = await asyncio.gather(
                    *(self._probe_domain(probe_session, sem, d) for d in candidates)
                )

                # Deduplicate by resolved origin BEFORE running API checks
                by_origin: dict[str, tuple[dict, str]] = {}
                for item, html in probe_raw:
                    key = item["origin"] if item["alive"] else f"dead:{item['domain']}"
                    existing = by_origin.get(key)
                    if existing is None or (
                        item["alive"] and item["latency_ms"] < existing[0]["latency_ms"]
                    ):
                        by_origin[key] = (item, html)

                if self.api_validator is not None:
                    api_sem = asyncio.Semaphore(6)
                    api_validator = self.api_validator

                    async def _run_api_check(entry: tuple[dict, str]) -> dict:
                        item, html = entry
                        if not item["alive"]:
                            return item
                        async with api_sem:
                            try:
                                ok, err_msg = await asyncio.wait_for(
                                    api_validator(probe_session, item["origin"], html),
                                    timeout=7,
                                )
                            except (
                                RequestsError,
                                TimeoutError,
                                OSError,
                                ValueError,
                            ) as exc:
                                ok, err_msg = (
                                    False,
                                    f"API check error: {type(exc).__name__}",
                                )
                        item["api_verified"] = ok
                        if not ok:
                            item["alive"] = False
                            item["error"] = err_msg or "API check failed"
                        return item

                    verified_items = await asyncio.gather(
                        *(_run_api_check(entry) for entry in by_origin.values())
                    )
                else:
                    verified_items = [entry[0] for entry in by_origin.values()]

            ranked = sorted(
                verified_items,
                key=lambda m: (not m["alive"], m["latency_ms"]),
            )

            self.mirrors = ranked
            self.discovery_source = source_used
            self.last_checked = time.time()

            alive = [m for m in ranked if m["alive"]]
            if alive:
                self.best_origin = alive[0]["origin"]
            self._save_cache()
            self._first_ready.set()
            return self.mirrors

    def trigger_refresh(self, force: bool = False) -> asyncio.Task[list[dict]]:
        if self._refresh_task is not None and not self._refresh_task.done():
            return self._refresh_task
        self._refresh_task = asyncio.create_task(self.refresh(force=force))
        return self._refresh_task

    async def ensure_ready(self) -> str:
        now = time.time()
        if self.mirrors:
            if (now - self.last_checked) >= self.cache_ttl_seconds:
                self.trigger_refresh(force=False)
            return self.best_origin

        task = self.trigger_refresh(force=True)
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
        except TimeoutError:
            pass
        return self.best_origin

    def promote_next(self, failed_origin: str) -> str | None:
        for m in self.mirrors:
            if m.get("origin") == failed_origin:
                m["alive"] = False
                m["error"] = "Marked down after runtime failure"
        alive = [m for m in self.mirrors if m.get("alive")]
        if alive:
            self.best_origin = alive[0]["origin"]
            return self.best_origin
        return None

    def is_refreshing(self) -> bool:
        return self._refresh_task is not None and not self._refresh_task.done()

    def snapshot(self) -> dict:
        alive_count = sum(1 for m in self.mirrors if m.get("alive"))
        return {
            "site": self.site_name,
            "best_origin": self.best_origin,
            "discovery_source": self.discovery_source,
            "last_checked": self.last_checked,
            "last_error": self.last_error,
            "refreshing": self.is_refreshing(),
            "alive_count": alive_count,
            "total_count": len(self.mirrors),
            "mirrors": self.mirrors,
        }
