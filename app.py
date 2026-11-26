from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from porno365 import DEFAULT_PORNO365_CATEGORIES
from scraper import VALID_SOURCES, UnifiedScraper
from studentki import DEFAULT_CATEGORIES

scraper = UnifiedScraper()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await scraper.startup()
    try:
        yield
    finally:
        await scraper.close()


app = FastAPI(
    title="NoodleScraper",
    description="Triple-aggregator video search, player, stream proxy, and mirror prober",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def _badge_from_snap(snap: dict) -> dict:
    best = snap["best_origin"].replace("https://", "").replace("http://", "")
    best_lat = None
    for m in snap["mirrors"]:
        if m.get("origin") == snap["best_origin"] and m.get("alive"):
            best_lat = m.get("latency_ms")
            break
    return {
        "domain": best,
        "latency_ms": best_lat,
        "alive": snap["alive_count"],
        "total": snap["total_count"],
    }


def setup_jinja_env():
    def url_for_with_params(request: Request, name: str, **params):
        base_url = request.url_for(name)
        filtered = {k: v for k, v in params.items() if v is not None and v != ""}
        if filtered:
            return f"{base_url}?{urlencode(filtered)}"
        return str(base_url)

    def get_mirror_badges() -> dict:
        return {
            "studentki": _badge_from_snap(scraper.studentki_mirrors.snapshot()),
            "porno365": _badge_from_snap(scraper.porno365_mirrors.snapshot()),
        }

    helpers: dict[str, Any] = {
        "max": max,
        "min": min,
        "now": datetime.now,
        "url_for_with_params": url_for_with_params,
        "mirror_badges": get_mirror_badges,
        "studentki_categories": DEFAULT_CATEGORIES,
        "porno365_categories": DEFAULT_PORNO365_CATEGORIES,
    }
    templates.env.globals.update(helpers)


setup_jinja_env()


async def _proxy_stream(
    request: Request,
    session,
    stream_url: str,
    referer_url: str,
    refresh_cb,
    filename_hint: str,
    download: int,
    verify_ssl: bool = True,
) -> StreamingResponse:
    fwd_headers = {"Referer": referer_url}
    range_header = request.headers.get("range")
    if range_header:
        fwd_headers["Range"] = range_header

    upstream = await session.get(
        stream_url,
        headers=fwd_headers,
        stream=True,
        allow_redirects=True,
        verify=verify_ssl,
        timeout=30,
    )

    if upstream.status_code in (403, 404, 410):
        await upstream.aclose()
        refreshed = await refresh_cb()
        if refreshed is None:
            raise HTTPException(status_code=404, detail="Stream expired")
        stream_url, referer_url = refreshed
        fwd_headers["Referer"] = referer_url
        upstream = await session.get(
            stream_url,
            headers=fwd_headers,
            stream=True,
            allow_redirects=True,
            verify=verify_ssl,
            timeout=30,
        )

    if upstream.status_code not in (200, 206):
        status = upstream.status_code
        await upstream.aclose()
        raise HTTPException(status_code=502, detail=f"Upstream returned HTTP {status}")

    resp_headers: dict[str, str] = {
        "Accept-Ranges": upstream.headers.get("accept-ranges") or "bytes",
        "Cache-Control": "no-store",
    }
    for h_name in ("content-length", "content-range", "etag", "last-modified"):
        val = upstream.headers.get(h_name)
        if val:
            resp_headers[h_name.title()] = val

    content_type = upstream.headers.get("content-type") or "video/mp4"
    if download:
        safe_name = "".join(
            ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in filename_hint
        )
        resp_headers["Content-Disposition"] = f'attachment; filename="{safe_name}.mp4"'

    async def body_iter():
        try:
            async for chunk in upstream.aiter_content(chunk_size=65536):
                if chunk:
                    yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        body_iter(),
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=content_type,
    )


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    source: str = Query("all", description="all | noodle | studentki | porno365"),
):
    active_source = source if source in VALID_SOURCES else "all"
    s_home = await scraper.studentki.get_home_sections()
    p_home = await scraper.porno365.get_home_sections()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "active_source": active_source,
            "categories": s_home.get("categories") or DEFAULT_CATEGORIES,
            "studentki_latest": (s_home.get("latest") or [])[:8],
            "porno365_latest": (p_home.get("latest") or [])[:8],
        },
    )


@app.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    q: str | None = Query(None, description="Search query"),
    page: int = Query(1, description="Page number", ge=1),
    source: str = Query("all", description="all | noodle | studentki | porno365"),
):
    active_source = source if source in VALID_SOURCES else "all"
    if not q:
        return await index(request, source=active_source)

    results = await scraper.search(q, page=page, source=active_source)
    return templates.TemplateResponse(
        request,
        "search_results.html",
        {
            "results": results,
            "query": q,
            "page": page,
            "active_source": active_source,
        },
    )


@app.get("/browse", response_class=HTMLResponse)
async def browse(
    request: Request,
    source: str = Query("studentki", description="studentki | porno365"),
    path: str = Query("/videos", description="Category/tag/model path"),
    f: str | None = Query(
        None, description="Filter string (f for studentki, cc for porno365)"
    ),
    cc: str | None = Query(None, description="Porno365 sub-category filter"),
    page: int = Query(1, ge=1),
):
    src = "porno365" if source == "porno365" else "studentki"
    if src == "porno365":
        p_clean = (
            "/"
            if path == "/videos" and request.query_params.get("path") is None
            else path
        )
        effective_cc = cc if cc is not None else f
        data = await scraper.porno365.browse(path=p_clean, cc=effective_cc, page=page)
        active_filter = effective_cc or ""
    else:
        data = await scraper.studentki.browse(path=path, f=f, page=page)
        active_filter = f or ""

    return templates.TemplateResponse(
        request,
        "browse.html",
        {
            "data": data,
            "browse_source": src,
            "path": data.get("path", path),
            "f": active_filter,
            "page": page,
            "active_source": src,
        },
    )


@app.get("/video/{source}/{video_id}", response_class=HTMLResponse)
async def video_detail_source(request: Request, source: str, video_id: str):
    if source not in ("noodle", "studentki", "porno365"):
        return templates.TemplateResponse(
            request,
            "error.html",
            {"error": f"Unknown source '{source}'."},
            status_code=404,
        )

    video = await scraper.get_video(video_id, source=source)
    if not video:
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "error": "Video not found. The link may be expired -- try searching again."
            },
            status_code=404,
        )

    return templates.TemplateResponse(
        request,
        "video_detail.html",
        {"video": video, "active_source": source},
    )


@app.get("/video/{video_id}", response_class=HTMLResponse)
async def video_detail(request: Request, video_id: str):
    video = await scraper.get_video(video_id, source=None)
    if not video:
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "error": "Video not found. The link may be expired -- try searching again."
            },
            status_code=404,
        )

    return templates.TemplateResponse(
        request,
        "video_detail.html",
        {"video": video, "active_source": video.get("source", "noodle")},
    )


@app.get("/stream/studentki/{video_id}")
async def stream_studentki(
    request: Request,
    video_id: str,
    download: int = Query(0),
):
    target = await scraper.studentki.get_stream_target(video_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Stream not found")

    return await _proxy_stream(
        request=request,
        session=scraper.studentki.session,
        stream_url=target[0],
        referer_url=target[1],
        refresh_cb=lambda: scraper.studentki.get_stream_target(
            video_id, force_refresh=True
        ),
        filename_hint=video_id,
        download=download,
        verify_ssl=True,
    )


@app.get("/stream/porno365/{video_id}")
async def stream_porno365(
    request: Request,
    video_id: str,
    q: int = Query(0, ge=0),
    download: int = Query(0),
):
    target = await scraper.porno365.get_stream_target(video_id, quality_idx=q)
    if target is None:
        raise HTTPException(status_code=404, detail="Stream not found")

    return await _proxy_stream(
        request=request,
        session=scraper.porno365.session,
        stream_url=target[0],
        referer_url=target[1],
        refresh_cb=lambda: scraper.porno365.get_stream_target(
            video_id, quality_idx=q, force_refresh=True
        ),
        filename_hint=f"porno365_{video_id}",
        download=download,
        verify_ssl=False,
    )


@app.get("/mirrors", response_class=HTMLResponse)
async def mirrors_page(request: Request):
    noodle_ping = await scraper.noodle.ping()
    return templates.TemplateResponse(
        request,
        "mirrors.html",
        {
            "studentki_snap": scraper.studentki_mirrors.snapshot(),
            "porno365_snap": scraper.porno365_mirrors.snapshot(),
            "noodle_ping": noodle_ping,
            "active_source": "all",
        },
    )


@app.get("/api/search")
async def api_search(
    q: str | None = Query(None, description="Search query"),
    page: int = Query(1, description="Page number", ge=1),
    source: str = Query("all", description="all | noodle | studentki | porno365"),
    with_sources: bool = Query(
        False,
        description="Merge direct links into NoodleMagazine results",
    ),
):
    if not q:
        raise HTTPException(status_code=400, detail="No search query provided")
    return JSONResponse(
        content=await scraper.search(
            q, page=page, source=source, with_sources=with_sources
        )
    )


@app.get("/api/browse")
async def api_browse(
    source: str = Query("studentki"),
    path: str = Query("/videos"),
    f: str | None = Query(None),
    cc: str | None = Query(None),
    page: int = Query(1, ge=1),
):
    if source == "porno365":
        return JSONResponse(
            content=await scraper.porno365.browse(
                path=path, cc=cc if cc is not None else f, page=page
            )
        )
    return JSONResponse(
        content=await scraper.studentki.browse(path=path, f=f, page=page)
    )


@app.get("/api/video/{source}/{video_id}")
async def api_video_source(source: str, video_id: str):
    if source not in ("noodle", "studentki", "porno365"):
        raise HTTPException(status_code=400, detail="Invalid source")
    video = await scraper.get_video(video_id, source=source)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    return JSONResponse(content=video)


@app.get("/api/video/{video_id}")
async def api_video(video_id: str):
    video = await scraper.get_video(video_id, source=None)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    return JSONResponse(content=video)


@app.get("/api/mirrors")
async def api_mirrors():
    return JSONResponse(
        content={
            "studentki": scraper.studentki_mirrors.snapshot(),
            "porno365": scraper.porno365_mirrors.snapshot(),
            "noodle": await scraper.noodle.ping(),
        }
    )


@app.post("/api/mirrors/refresh")
async def api_mirrors_refresh():
    return JSONResponse(content=await scraper.refresh_all_mirrors())


@app.get("/health")
async def health_check():
    s_snap = scraper.studentki_mirrors.snapshot()
    p_snap = scraper.porno365_mirrors.snapshot()
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "studentki_mirror": s_snap["best_origin"],
        "studentki_alive_mirrors": s_snap["alive_count"],
        "porno365_mirror": p_snap["best_origin"],
        "porno365_alive_mirrors": p_snap["alive_count"],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=6969, reload=True)
