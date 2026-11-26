import asyncio
import logging
from pathlib import Path

from curl_cffi.requests.errors import RequestsError

from mirrors import MirrorManager, is_valid_porno365, is_valid_studentki
from noodle import NoodleScraper
from porno365 import Porno365Scraper, test_porno365_origin
from studentki import SexStudentkiScraper, test_studentki_origin

logger = logging.getLogger(__name__)

VALID_SOURCES = ("all", "noodle", "studentki", "porno365")


class UnifiedScraper:
    """Coordinates NoodleMagazine, Sex-Studentki, and Porno365 + non-blocking mirror health."""

    def __init__(self):
        self.studentki_mirrors = MirrorManager(
            site_name="Sex-Studentki",
            bing_query="sex-studentki",
            domain_needle="sex-studentki",
            fallback_domains=["ru.sex-studentki.guru", "sex-studentki.guru"],
            cache_file=Path(".mirror_cache.json"),
            verify_ssl=True,
            fallback_http=False,
            validator=is_valid_studentki,
            api_validator=test_studentki_origin,
        )
        self.porno365_mirrors = MirrorManager(
            site_name="Porno365",
            bing_query="porno365",
            domain_needle="porno365",
            fallback_domains=["porno365.plus", "7porno365.fun"],
            cache_file=Path(".porno365_mirror_cache.json"),
            verify_ssl=False,
            fallback_http=True,
            validator=is_valid_porno365,
            api_validator=test_porno365_origin,
        )
        self.mirrors = self.studentki_mirrors

        self.noodle = NoodleScraper()
        self.studentki = SexStudentkiScraper(self.studentki_mirrors)
        self.porno365 = Porno365Scraper(self.porno365_mirrors)
        self._startup_task: asyncio.Task[None] | None = None

    async def _background_startup(self) -> None:
        await self.refresh_all_mirrors()

        for mgr in (self.studentki_mirrors, self.porno365_mirrors):
            alive = [m for m in mgr.mirrors if m.get("alive")]
            print(
                f"[{mgr.site_name}] Discovery={mgr.discovery_source} | "
                f"Alive={len(alive)}/{len(mgr.mirrors)} | Best={mgr.best_origin}"
            )
            for m in mgr.mirrors:
                state = "OK (API)" if m["alive"] else f"DEAD ({m.get('error')})"
                print(
                    f"  - {m['domain']} -> {m['origin']} [{m['latency_ms']} ms] {state}"
                )

        async def _warm(coro, label: str) -> None:
            try:
                await coro
            except (RequestsError, TimeoutError, OSError, ValueError) as exc:
                logger.warning("%s homepage warmup skipped: %s", label, exc)

        await asyncio.gather(
            _warm(self.studentki.get_home_sections(use_cache=False), "Studentki"),
            _warm(self.porno365.get_home_sections(use_cache=False), "Porno365"),
        )

    async def startup(self) -> None:
        # Instant start: cache is already hydrated in __init__; run Bing + API checks in background.
        print(
            f"[Startup] Fast boot | Studentki={self.studentki_mirrors.best_origin} | "
            f"Porno365={self.porno365_mirrors.best_origin} (refreshing in background)"
        )
        self._startup_task = asyncio.create_task(self._background_startup())

    async def close(self) -> None:
        for task in (
            self._startup_task,
            self.studentki_mirrors._refresh_task,
            self.porno365_mirrors._refresh_task,
        ):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            self.noodle.close(),
            self.studentki.close(),
            self.porno365.close(),
            return_exceptions=True,
        )

    async def refresh_all_mirrors(self) -> dict:
        await asyncio.gather(
            self.studentki_mirrors.trigger_refresh(force=True),
            self.porno365_mirrors.trigger_refresh(force=True),
        )
        return {
            "studentki": self.studentki_mirrors.snapshot(),
            "porno365": self.porno365_mirrors.snapshot(),
        }

    @staticmethod
    def _unpack_result(
        res: dict | BaseException,
        name: str,
        errors: dict[str, str],
    ) -> tuple[list[dict], list[dict], bool]:
        if isinstance(res, asyncio.CancelledError):
            raise res
        if isinstance(res, BaseException):
            errors[name] = str(res)
            return [], [], False
        if res.get("error"):
            errors[name] = str(res["error"])
            return [], [], False
        return (
            res.get("videos") or [],
            res.get("profiles") or [],
            bool(res.get("has_next")),
        )

    async def search(
        self,
        query: str,
        page: int = 1,
        source: str = "all",
        with_sources: bool = False,
    ) -> dict:
        src = source if source in VALID_SOURCES else "all"

        if src == "noodle":
            res = await self.noodle.search_videos(
                query, page - 1, with_sources=with_sources
            )
            videos = res.get("videos") or []
            return {
                "source": "noodle",
                "query": query,
                "page": page,
                "videos": videos,
                "profiles": [],
                "counts": {"noodle": len(videos), "studentki": 0, "porno365": 0},
                "errors": {"noodle": res["error"]} if res.get("error") else {},
                "error": res.get("error"),
                "has_next": bool(res.get("has_next", len(videos) > 0)),
            }

        if src == "studentki":
            res = await self.studentki.search_videos(query, page)
            videos = res.get("videos") or []
            profiles = res.get("profiles") or []
            return {
                "source": "studentki",
                "query": query,
                "page": page,
                "videos": videos,
                "profiles": profiles,
                "counts": {"noodle": 0, "studentki": len(videos), "porno365": 0},
                "errors": {"studentki": res["error"]} if res.get("error") else {},
                "error": res.get("error"),
                "has_next": bool(res.get("has_next", False)),
            }

        if src == "porno365":
            res = await self.porno365.search_videos(query, page)
            videos = res.get("videos") or []
            profiles = res.get("profiles") or []
            return {
                "source": "porno365",
                "query": query,
                "page": page,
                "videos": videos,
                "profiles": profiles,
                "counts": {"noodle": 0, "studentki": 0, "porno365": len(videos)},
                "errors": {"porno365": res["error"]} if res.get("error") else {},
                "error": res.get("error"),
                "has_next": bool(res.get("has_next", False)),
            }

        n_res, s_res, p_res = await asyncio.gather(
            self.noodle.search_videos(query, page - 1, with_sources=with_sources),
            self.studentki.search_videos(query, page),
            self.porno365.search_videos(query, page),
            return_exceptions=True,
        )

        errors: dict[str, str] = {}
        n_videos, _, n_next = self._unpack_result(n_res, "noodle", errors)
        s_videos, s_profiles, s_next = self._unpack_result(s_res, "studentki", errors)
        p_videos, p_profiles, p_next = self._unpack_result(p_res, "porno365", errors)

        for p in s_profiles:
            p.setdefault("source", "studentki")
        for p in p_profiles:
            p.setdefault("source", "porno365")

        merged: list[dict] = []
        max_len = max(len(n_videos), len(s_videos), len(p_videos))
        for idx in range(max_len):
            if idx < len(n_videos):
                merged.append(n_videos[idx])
            if idx < len(s_videos):
                merged.append(s_videos[idx])
            if idx < len(p_videos):
                merged.append(p_videos[idx])

        all_profiles = s_profiles + p_profiles
        fatal_error = (
            "All sources failed"
            if (not merged and not all_profiles and len(errors) == 3)
            else None
        )

        return {
            "source": "all",
            "query": query,
            "page": page,
            "videos": merged,
            "profiles": all_profiles,
            "counts": {
                "noodle": len(n_videos),
                "studentki": len(s_videos),
                "porno365": len(p_videos),
            },
            "errors": errors,
            "error": fatal_error,
            "has_next": n_next or s_next or p_next,
        }

    async def get_video(self, video_id: str, source: str | None = None) -> dict | None:
        if source == "studentki":
            return await self.studentki.get_video_by_id(video_id)
        if source == "porno365":
            return await self.porno365.get_video_by_id(video_id)
        if source == "noodle":
            return await self.noodle.get_video_by_id(video_id)

        video = await self.noodle.get_video_by_id(video_id)
        if video is not None:
            return video
        if video_id.isdigit():
            video = await self.porno365.get_video_by_id(video_id)
            if video is not None:
                return video
        return await self.studentki.get_video_by_id(video_id)
