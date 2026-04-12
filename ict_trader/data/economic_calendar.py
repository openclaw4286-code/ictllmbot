"""
ForexFactory 경제 캘린더 스크래핑.
고영향(High Impact) 이벤트만 수집.
메인 루프에서 경제지표 잠금 판단에 사용.
"""

from __future__ import annotations

import time
import logging
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

from ict_trader.config import ECON_LOCK_MINUTES, ECON_CALENDAR_HIGH_IMPACT_ONLY

logger = logging.getLogger(__name__)

FOREXFACTORY_URL = "https://www.forexfactory.com/calendar"

# 캐시: 1시간
_cache: list[dict] = []
_cache_ts: float = 0.0
_CACHE_TTL = 3600

# 주요 통화
CRYPTO_RELEVANT_CURRENCIES = {"USD", "EUR", "GBP", "JPY", "CNY", "ALL"}


def fetch_economic_events() -> list[dict]:
    """
    ForexFactory에서 오늘의 고영향 경제 이벤트를 스크래핑한다.

    Returns:
        [{"time_utc": datetime, "currency": str, "event": str, "impact": str}, ...]
    """
    global _cache, _cache_ts

    now = time.time()
    if _cache and (now - _cache_ts) < _CACHE_TTL:
        logger.debug("경제 캘린더 캐시 사용")
        return _cache

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        resp = requests.get(FOREXFACTORY_URL, headers=headers, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("ForexFactory 스크래핑 실패: %s", e)
        if _cache:
            logger.warning("이전 캐시 반환 (만료됨)")
            return _cache
        return []

    events = _parse_calendar_html(resp.text)

    _cache = events
    _cache_ts = now
    logger.info("경제 캘린더 수신: %d건 (고영향)", len(events))
    return events


def _parse_calendar_html(html: str) -> list[dict]:
    """ForexFactory HTML에서 고영향 이벤트를 파싱한다."""
    soup = BeautifulSoup(html, "lxml")
    events: list[dict] = []
    today = datetime.now(timezone.utc).date()

    table = soup.find("table", class_="calendar__table")
    if not table:
        logger.warning("ForexFactory 캘린더 테이블을 찾을 수 없음 (구조 변경 가능)")
        return events

    rows = table.find_all("tr", class_="calendar__row")
    current_time_str = ""

    for row in rows:
        # 영향도 확인
        impact_td = row.find("td", class_="calendar__impact")
        if not impact_td:
            continue
        impact_span = impact_td.find("span")
        if not impact_span:
            continue

        impact_classes = impact_span.get("class", [])
        is_high = any("high" in c.lower() for c in impact_classes)

        if ECON_CALENDAR_HIGH_IMPACT_ONLY and not is_high:
            continue

        # 시간
        time_td = row.find("td", class_="calendar__time")
        if time_td and time_td.get_text(strip=True):
            current_time_str = time_td.get_text(strip=True)

        # 통화
        currency_td = row.find("td", class_="calendar__currency")
        currency = currency_td.get_text(strip=True) if currency_td else ""

        if currency and currency not in CRYPTO_RELEVANT_CURRENCIES:
            continue

        # 이벤트명
        event_td = row.find("td", class_="calendar__event")
        event_name = ""
        if event_td:
            event_span = event_td.find("span")
            event_name = event_span.get_text(strip=True) if event_span else event_td.get_text(strip=True)

        if not event_name:
            continue

        # 시간 파싱
        event_time = _parse_event_time(current_time_str, today)

        events.append({
            "time_utc": event_time,
            "currency": currency,
            "event": event_name,
            "impact": "high" if is_high else "medium",
        })

    return events


def _parse_event_time(time_str: str, date: datetime.date) -> datetime | None:
    """
    ForexFactory 시간 문자열을 UTC datetime으로 변환.
    ForexFactory는 EST(UTC-5) 기준이므로 +5시간 보정.
    """
    if not time_str or time_str.lower() in ("", "all day", "tentative"):
        return None

    time_str = time_str.strip().lower()
    try:
        if "am" in time_str or "pm" in time_str:
            parsed = datetime.strptime(time_str, "%I:%M%p")
            est_time = datetime(
                date.year, date.month, date.day,
                parsed.hour, parsed.minute,
                tzinfo=timezone(timedelta(hours=-5)),
            )
            return est_time.astimezone(timezone.utc)
    except ValueError:
        logger.debug("시간 파싱 실패: '%s'", time_str)

    return None


def is_economic_lock_active(events: list[dict] | None = None) -> bool:
    """
    현재 시간이 고영향 경제지표 전후 잠금 시간 이내인지 확인.

    Returns:
        True이면 트레이딩 잠금 상태
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
    """향후 N시간 내 고영향 이벤트 목록을 반환한다."""
    events = fetch_economic_events()
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc + timedelta(hours=hours_ahead)

    upcoming: list[dict] = []
    for event in events:
        event_time = event.get("time_utc")
        if event_time is None:
            continue
        if now_utc <= event_time <= cutoff:
            upcoming.append(event)

    return upcoming
