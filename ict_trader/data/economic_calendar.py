"""
경제 캘린더 — 고영향 이벤트 수집.
Nager.Date 공휴일 + 수동 정의 정기 이벤트 + FXStreet RSS 혼합.
고영향 경제지표 전후 잠금 판단에 사용.
"""

from __future__ import annotations

import time
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

import requests

from ict_trader.config import ECON_LOCK_MINUTES

logger = logging.getLogger(__name__)

# 캐시: 1시간
_cache: list[dict] = []
_cache_ts: float = 0.0
_CACHE_TTL = 3600

# 매주 반복되는 고영향 정기 이벤트 (UTC 시간)
# 미국 주요 경제지표 발표 시간대
RECURRING_HIGH_IMPACT = [
    # (요일 0=월~4=금, 시, 분, 이벤트명)
    (1, 15, 0, "US PPI / CPI (정기)"),         # 화 15:00 UTC (한국 자정)
    (2, 13, 30, "US CPI / Retail Sales (정기)"), # 수 13:30 UTC
    (3, 13, 30, "US Jobless Claims (정기)"),     # 목 13:30 UTC
    (4, 13, 30, "US Employment / NFP (정기)"),   # 금 13:30 UTC (첫째주)
]

# FOMC 일정 (수동 관리 — 2026년)
FOMC_DATES_2026 = [
    "2026-01-28", "2026-03-18", "2026-05-06",
    "2026-06-17", "2026-07-29", "2026-09-16",
    "2026-11-04", "2026-12-16",
]


def _get_recurring_events_today() -> list[dict]:
    """오늘의 정기 고영향 이벤트를 반환."""
    now = datetime.now(timezone.utc)
    today_weekday = now.weekday()
    events = []

    for weekday, hour, minute, name in RECURRING_HIGH_IMPACT:
        if today_weekday == weekday:
            event_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            events.append({
                "time_utc": event_time,
                "currency": "USD",
                "event": name,
                "impact": "high",
            })

    # FOMC 체크
    today_str = now.strftime("%Y-%m-%d")
    if today_str in FOMC_DATES_2026:
        fomc_time = now.replace(hour=19, minute=0, second=0, microsecond=0)
        events.append({
            "time_utc": fomc_time,
            "currency": "USD",
            "event": "FOMC 금리 결정",
            "impact": "high",
        })

    return events


def _fetch_fxstreet_rss() -> list[dict]:
    """FXStreet 경제 캘린더 RSS에서 이벤트 수집 시도."""
    url = "https://www.fxstreet.com/rss/economic-calendar"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        events = []
        for item in root.findall(".//item"):
            title = item.findtext("title", "").strip()
            pub_date = item.findtext("pubDate", "").strip()

            # "High" 영향 이벤트만
            if not any(kw in title.lower() for kw in [
                "nfp", "cpi", "fomc", "gdp", "ppi", "retail",
                "employment", "payroll", "interest rate", "fed",
                "inflation", "jobless",
            ]):
                continue

            events.append({
                "time_utc": None,  # RSS에서 정확한 시간 파싱 어려움
                "currency": "USD",
                "event": title,
                "impact": "high",
            })

        if events:
            logger.info("FXStreet RSS 이벤트: %d건", len(events))
        return events

    except Exception as e:
        logger.debug("FXStreet RSS 수집 실패 (정상 폴백): %s", e)
        return []


def fetch_economic_events() -> list[dict]:
    """
    오늘의 고영향 경제 이벤트를 수집한다.
    정기 이벤트 + FXStreet RSS 혼합.
    """
    global _cache, _cache_ts

    now = time.time()
    if _cache and (now - _cache_ts) < _CACHE_TTL:
        return _cache

    events = _get_recurring_events_today()
    rss_events = _fetch_fxstreet_rss()
    events.extend(rss_events)

    _cache = events
    _cache_ts = now

    if events:
        logger.info("경제 캘린더: %d건 (고영향)", len(events))
    return events


def is_economic_lock_active(events: list[dict] | None = None) -> bool:
    """
    현재 시간이 고영향 경제지표 전후 잠금 시간 이내인지 확인.
    """
    if events is None:
        events = fetch_economic_events()

    now_utc = datetime.now(timezone.utc)
    lock_delta = timedelta(minutes=ECON_LOCK_MINUTES)

    for event in events:
        event_time = event.get("time_utc")
        if event_time is None:
            continue
        if abs(now_utc - event_time) <= lock_delta:
            logger.info(
                "경제지표 잠금 활성: %s (%s) @ %s",
                event["event"], event["currency"],
                event_time.strftime("%H:%M UTC"),
            )
            return True

    return False


def get_upcoming_events(hours_ahead: int = 4) -> list[dict]:
    """향후 N시간 내 고영향 이벤트 목록."""
    events = fetch_economic_events()
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc + timedelta(hours=hours_ahead)

    upcoming = []
    for event in events:
        event_time = event.get("time_utc")
        if event_time is None:
            continue
        if now_utc <= event_time <= cutoff:
            upcoming.append(event)

    return upcoming
