# 🍜 NoodleScraper

**NoodleScraper** provides Noodlemagazine's video scraper with web interface, video player, sources, previews and custom scraper API if interface not needed.

![Python](https://img.shields.io/badge/Python-3.7+-blue?style=flat-square&logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.68+-green?style=flat-square&logo=fastapi)
![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)

## ✨ Features

- 🌐 Web interface for browsing and searching videos
- 📺 Integrated video player with source selection
- 🖼️ Video preview thumbnails
- 🔌 Custom scraper API for programmatic access
- ⚡ Asynchronous processing for improved performance
- 🔄 **Proper video ordering** - Videos maintain their order as they appear on the source site
- 🚀 **FastAPI powered** - Modern, fast async web framework

## 🚀 Installation

```bash
git clone https://github.com/catochin/noodlescraper
cd noodlescraper
pip install -r requirements.txt
python run.py
```

## 🔧 Usage

After installation, the web interface will be available at:
```
http://127.0.0.1:6969
```

## 🏗️ Architecture

The project consists of two main components:

### 🧠 `noodle.py`

Core scraper functionality that:
- 🕸️ Handles web scraping from noodlemagazine.com
- 🔄 Processes video data and extracts metadata
- 🔍 Provides search functionality with **preserved ordering**
- 📋 Extracts video sources and playlists
- 📊 Maintains original source site ordering

### 🖥️ `app.py`

FastAPI web interface that:
- 👤 Provides a user-friendly frontend
- 🌍 Exposes the scraper functionality via web UI
- 🔗 Offers RESTful API endpoints for programmatic access
- ⚡ Modern async framework for better performance

## 🔌 API Endpoints

The following API endpoints are available:

### 🔍 Search Videos
```
GET /api/search?q={query}&page={page}
```
Returns search results for the given query with **preserved ordering**.

### 📹 Get Video Details
```
GET /api/video/{video_id}
```
Returns detailed information about a specific video.

### 💚 Health Check
```
GET /health
```
Returns application health status.

## 📋 Requirements

- Python 3.7+
- FastAPI (modern async web framework)
- Uvicorn (ASGI server)
- Cloudscraper (for bypassing web protections)
- Selectolax (HTML parsing)
- Jinja2 (templating)

## 🆕 Migration from Quart

This version has been **completely migrated from Quart to FastAPI** for:
- Better performance
- More modern async handling
- Improved API documentation
- Better type hints support
- Enhanced development experience

## 🔄 Video Ordering

Videos are now **properly sorted** to maintain their original order as they appear on the source website:
- Search results preserve source site ordering
- API responses include `source_order` field
- Quality sources are sorted from highest to lowest quality
- No manual reordering needed

## ⚖️ License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

<p align="center">
Made with ❤️ for video content enthusiasts
</p>