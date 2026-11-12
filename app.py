from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from noodle import NoodleScraper

scraper = NoodleScraper()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Session opens lazily; release curl handles on shutdown.
    yield
    await scraper.close()


app = FastAPI(
    title="NoodleScraper",
    description="Video search, player and direct links",
    lifespan=lifespan,
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Setup templates
templates = Jinja2Templates(directory="templates")


def setup_jinja_env():
    """Register the custom helpers used by the templates"""

    def url_for_with_params(request: Request, name: str, **params):
        """Generate a URL with extra query parameters (url_for only takes path params)"""
        base_url = request.url_for(name)
        if params:
            return f"{base_url}?{urlencode(params)}"
        return base_url

    helpers: dict[str, Any] = {
        "max": max,
        "now": datetime.now,
        "url_for_with_params": url_for_with_params,
    }
    templates.env.globals.update(helpers)


setup_jinja_env()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Home page with search interface"""
    return templates.TemplateResponse(request, "index.html")


@app.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    q: str | None = Query(None, description="Search query"),
    page: int = Query(1, description="Page number", ge=1),
):
    """Search for videos and display results"""
    if not q:
        return templates.TemplateResponse(request, "index.html")

    results = await scraper.search_videos(q, page - 1)
    return templates.TemplateResponse(
        request,
        "search_results.html",
        {"results": results, "query": q, "page": page},
    )


@app.get("/video/{video_id}", response_class=HTMLResponse)
async def video_detail(request: Request, video_id: str):
    """Video detail page, rendered server-side so links can be shared directly"""
    video = await scraper.get_video_by_id(video_id)
    if not video:
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "error": "Video not found. The link may be expired -- try searching again."
            },
            status_code=404,
        )

    return templates.TemplateResponse(request, "video_detail.html", {"video": video})


# API Endpoints
@app.get("/api/search")
async def api_search(
    q: str | None = Query(None, description="Search query"),
    page: int = Query(1, description="Page number", ge=1),
    with_sources: bool = Query(
        False,
        description="Merge direct links into every result (two upstream requests per video)",
    ),
):
    """API endpoint for searching videos"""
    if not q:
        raise HTTPException(status_code=400, detail="No search query provided")

    return JSONResponse(
        content=await scraper.search_videos(q, page - 1, with_sources=with_sources)
    )


@app.get("/api/video/{video_id}")
async def api_video(video_id: str):
    """API endpoint for getting video details with direct links"""
    video = await scraper.get_video_by_id(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    return JSONResponse(content=video)


# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=6969, reload=True)
