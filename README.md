# NoodleScraper

Triple-aggregator async video search, player, stream proxy, and mirror health monitor built on FastAPI, `curl-cffi`, and `selectolax` (Lexbor).

![Python](https://img.shields.io/badge/Python-3.10+-blue?style=flat-square&logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-green?style=flat-square&logo=fastapi)
![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)

## Features

- Triple-source search (`all`, `noodle`, `studentki`, `porno365`) with per-source counts and interleaved results
- Automatic mirror discovery via Bing SERP, concurrent latency probing, local JSON caching, and runtime failover:
  - Sex-Studentki: strict HTTPS mirror ranking (`.mirror_cache.json`)
  - Porno365: SSL-verify-disabled probe with HTTP fallback and redirect target resolution (`.porno365_mirror_cache.json`)
- Category, tag, model, and multi-filter browsing (`/browse`) for both Sex-Studentki (`f=`) and Porno365 (`cc=`), plus live model search
- Built-in playback:
  - NoodleMagazine: iframe player + sorted direct `.mp4` links
  - Sex-Studentki & Porno365: Range-capable HTML5 stream proxies (`/stream/studentki/{id}` and `/stream/porno365/{id}?q={idx}`) with quality switching and clickable timecodes
- Server-rendered Jinja2 UI and JSON REST API (`/docs`)

## Installation

```bash
git clone https://github.com/catochin/noodlescraper
cd noodlescraper
pip install -r requirements.txt
python run.py
```

Open `http://127.0.0.1:6969`. Interactive API documentation is available at `http://127.0.0.1:6969/docs`.

## Architecture

| File | Role |
| --- | --- |
| `app.py` | FastAPI routes, Jinja2 template rendering, Range-capable MP4 stream proxies, and JSON API |
| `scraper.py` | `UnifiedScraper` coordinating startup mirror refresh, source dispatch, and merged search |
| `noodle.py` | Async NoodleMagazine scraper (`curl-cffi` Chrome TLS impersonation + `JSONDecoder.raw_decode` playlist extraction) |
| `studentki.py` | Async Sex-Studentki scraper (AJAX `POST` search, category/filter browsing, timecodes, models/tags, and stream resolution) |
| `porno365.py` | Async Porno365 scraper (`GET` search + AJAX model lookup, `cc=` multi-filters, JWPlayer multi-quality extraction, and comment timecodes) |
| `mirrors.py` | Configurable `MirrorManager` for Bing discovery, SSL/redirect probing, latency ranking, and failover |
| `run.py` | Uvicorn development entrypoint (`0.0.0.0:6969`, hot reload) |

## API Endpoints

### Search
```text
GET /api/search?q={query}&page={page}&source={all|noodle|studentki|porno365}&with_sources={0|1}
```
Searches across one or all aggregators. Returns `videos`, `profiles` (matching models/authors), per-source `counts`, and pagination state.

### Browse
```text
GET /api/browse?source={studentki|porno365}&path={path}&f={filters}&cc={filters}&page={page}
```
Loads category, model, or feed listings along with sort links and multi-tag filter chips.

### Video Details
```text
GET /api/video/{source}/{video_id}
GET /api/video/{video_id}
```
Returns metadata, tags, actors, clickable timecodes, related items, and playable/downloadable stream sources.

### Stream Proxies
```text
GET /stream/studentki/{video_id}?download={0|1}
GET /stream/porno365/{video_id}?q={quality_index}&download={0|1}
```
Proxies upstream MP4 streams with `Range` support and required `Referer` headers, automatically refreshing expired stream tokens on `403`/`404`/`410`.

### Mirrors & Health
```text
GET  /api/mirrors
POST /api/mirrors/refresh
GET  /health
```
Inspects NoodleMagazine reachability plus all probed Sex-Studentki and Porno365 mirrors, or triggers an immediate Bing re-scan and latency re-rank.

## Requirements

- Python 3.10+
- `fastapi`, `uvicorn[standard]`, `jinja2`
- `curl-cffi`
- `selectolax` (Lexbor backend)

## License

MIT — see [LICENSE](LICENSE).
