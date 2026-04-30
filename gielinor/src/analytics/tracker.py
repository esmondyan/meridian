"""
Analytics event tracker for the Streamlit dashboard.
Sends events to the FastAPI backend via HTTP (non-blocking).
"""
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

import requests

from config.settings import API_HOST, API_PORT


def _api_url(path: str) -> str:
    return f"http://127.0.0.1:{API_PORT}{path}"


def track_event(
    event_type: str,
    event_data: Optional[dict] = None,
    user_id: Optional[int] = None,
    session_id: Optional[str] = None,
    ip: Optional[str] = None,
    ua: Optional[str] = None,
):
    """Fire-and-forget event tracking. Never blocks the dashboard."""
    try:
        payload = {
            "event_type": event_type,
            "event_data": event_data or {},
            "user_id": user_id,
            "session_id": session_id,
        }
        requests.post(
            _api_url("/api/analytics/event"),
            json=payload,
            timeout=2,
        )
    except Exception:
        pass  # Silently fail — analytics should never break the app


def page_view(page: str, **kwargs):
    track_event("page_view", {"page": page, **kwargs})


def item_search(query: str, results_count: int, **kwargs):
    track_event("search", {"query": query, "results_count": results_count, **kwargs})


def score_view(min_score: float, items_shown: int, **kwargs):
    track_event("score_view", {"min_score": min_score, "items_shown": items_shown, **kwargs})


def price_trend_view(item_name: str, **kwargs):
    track_event("price_trend_view", {"item_name": item_name, **kwargs})
