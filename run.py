#!/usr/bin/env python3
"""
Startup script for NoodleScraper FastAPI application
"""
import uvicorn
from app import app

if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=6969,
        reload=True,
        log_level="info"
    )