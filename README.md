# 🍜 NoodleScraper

NoodleScraper provides Noodlemagazine's video scraper with web interface, video player, sources, previews and custom scraper API if interface not needed.

<p align="center">
<img src="https://img.shields.io/badge/Python-3.7+-blue.svg" alt="Python 3.7+"/>
<img src="https://img.shields.io/badge/License-MIT-brightgreen.svg" alt="License"/>
<img src="https://img.shields.io/badge/Version-1.0-green.svg" alt="Version"/>
</p>

## ✨ Features

- 🌐 Web interface for browsing and searching videos
- 📺 Integrated video player with source selection
- 🖼️ Video preview thumbnails
- 🔌 Custom scraper API for programmatic access
- ⚡ Asynchronous processing for improved performance

## 🚀 Installation

```bash
git clone https://github.com/catochin/noodlescraper
cd noodlescraper
pip install -r requirements.txt
python app.py
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
- 🔍 Provides search functionality
- 📋 Extracts video sources and playlists

### 🖥️ `app.py`

Web interface that:
- 👤 Provides a user-friendly frontend
- 🌍 Exposes the scraper functionality via web UI
- 🔗 Offers RESTful API endpoints for programmatic access

## 🔌 API Endpoints

The following API endpoints are available:

### 🔍 Search Videos
```
GET /api/search?q={query}&page={page}
```
Returns search results for the given query.

### 📹 Get Video Details
```
GET /api/video/{video_id}
```
Returns detailed information about a specific video.

## 📋 Requirements

- Python 3.7+
- Quart (async web framework)
- Cloudscraper (for bypassing web protections)
- Selectolax (HTML parsing)

## ⚖️ License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

<p align="center">
Made with ❤️ for video content enthusiasts
</p>
