import os
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from noodle import NoodleScraper
from datetime import datetime
import json
from typing import Optional
from urllib.parse import urlencode

app = FastAPI(title="NoodleScraper", description="Modern Video Search with FastAPI")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Setup templates
templates = Jinja2Templates(directory="templates")

# Initialize scraper
scraper = NoodleScraper()

# Add custom filters and globals to templates
def setup_jinja_env():
    """Setup Jinja2 environment with custom filters and globals"""

    def url_for_with_params(request, name, **params):
        """Helper function to generate URLs with query parameters"""
        base_url = request.url_for(name)
        if params:
            query_string = urlencode(params)
            return f"{base_url}?{query_string}"
        return base_url

    templates.env.globals.update(
        max=max,
        min=min,
        int=int,
        str=str,
        len=len,
        zip=zip,
        now=datetime.now,
        datetime=datetime,
        url_for_with_params=url_for_with_params
    )

    # Custom filter for JSON serialization
    templates.env.filters['tojson'] = lambda obj: json.dumps(obj)

setup_jinja_env()

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Home page with search interface"""
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    q: Optional[str] = Query(None, description="Search query"),
    page: int = Query(1, description="Page number", ge=1)
):
    """Search for videos and display results"""
    if not q:
        return templates.TemplateResponse("index.html", {"request": request})

    # Convert page to 0-based for the scraper
    scraper_page = page - 1
    results = await scraper.search_videos(q, scraper_page)

    # Ensure videos maintain their original order from the source site
    if 'videos' in results and results['videos']:
        # The scraper already maintains order, but let's ensure it explicitly
        # Videos are processed in the order they appear on the source site
        pass  # Order is already preserved from the scraper

    return templates.TemplateResponse(
        "search_results.html",
        {
            "request": request,
            "results": results,
            "query": q,
            "page": page
        }
    )

@app.get("/video/{video_id}", response_class=HTMLResponse)
async def video_detail(request: Request, video_id: str):
    """Video detail page - data loaded via client-side JavaScript"""
    return templates.TemplateResponse(
        "video_detail.html",
        {
            "request": request,
            "video_id": video_id
        }
    )

# API Endpoints
@app.get("/api/search")
async def api_search(
    q: Optional[str] = Query(None, description="Search query"),
    page: int = Query(1, description="Page number", ge=1)
):
    """API endpoint for searching videos"""
    if not q:
        raise HTTPException(status_code=400, detail="No search query provided")

    # Convert page to 0-based for the scraper
    scraper_page = page - 1
    results = await scraper.search_videos(q, scraper_page)

    # Ensure videos maintain their original order from the source site
    if 'videos' in results and results['videos']:
        # Videos are already in the correct order from the scraper
        # But we can add additional metadata to confirm ordering
        for i, video in enumerate(results['videos']):
            video['source_order'] = i  # Add explicit ordering index

    return JSONResponse(content=results)

@app.get("/api/video/{video_id}")
async def api_video(video_id: str):
    """API endpoint for getting video details"""
    video_data = scraper.get_video_by_id(video_id)
    if not video_data:
        raise HTTPException(status_code=404, detail="Video not found")

    return JSONResponse(content=video_data)

# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=6969, reload=True)