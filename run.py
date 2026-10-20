#!/usr/bin/env python3
"""Arranque local: `python run.py` -> http://localhost:8000"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "backend.app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=["backend", "content"],
    )
