"""
CryptoPanic 뉴스 수집.
심볼당 5분 캐시, LLM에 전달할 뉴스 최대 N개.
"""

from __future__ import annotations

import time
import logging
import requests

from ict_trader.config import CRYPTOPANIC_API_KEY, NEWS_CACHE_TTL, NEWS_MAX_ITEMS

logger = logging.getLogger(__name__)

CRYPTOPANIC_URL = "https://cryptopanic.com/api/v1/posts/"

# 캐시: {symbol: {"data": [...], "ts": float}}
_cache: dict[str, dict] = {}


def _symbol_to_currency(symbol: str) -> str:
    """'BTC/USDT' → 'BTC'"""
    return symbol.split("/")[0].upper()


def fetch_news(symbol: str) -> list[dict]:
    """
    심볼에 대한 최근 뉴스를 가져온다.

    Args:
        symbol: 거래 쌍 (예: "BTC/USDT")

    Returns:
        [{"title": str, "source": str, "url": str, "published_at": str, "kind": str}, ...]
    """
    currency = _symbol_to_currency(symbol)
    now = time.time()

    # 캐시 확인
    if currency in _cache:
        cached = _cache[currency]
        if (now - cached["ts"]) < NEWS_CACHE_TTL:
            logger.debug("뉴스 캐시 사용: %s", currency)
            return cached["data"]

    if not CRYPTOPANIC_API_KEY:
        logger.warning("CRYPTOPANIC_API_KEY 미설정, 뉴스 수집 건너뜀")
        return []

    params = {
        "auth_token": CRYPTOPANIC_API_KEY,
        "currencies": currency,
        "kind": "news",
        "filter": "important",
        "public": "true",
    }

    try:
        resp = requests.get(CRYPTOPANIC_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.error("CryptoPanic API 오류: %s — %s", currency, e)
        if currency in _cache:
            return _cache[currency]["data"]
        return []

    results = data.get("results", [])
    news_list: list[dict] = []
    for item in results[:NEWS_MAX_ITEMS]:
        news_list.append({
            "title": item.get("title", ""),
            "source": item.get("source", {}).get("title", "unknown"),
            "url": item.get("url", ""),
            "published_at": item.get("published_at", ""),
            "kind": item.get("kind", "news"),
        })

    _cache[currency] = {"data": news_list, "ts": now}
    logger.info("뉴스 수신: %s %d건", currency, len(news_list))
    return news_list


def fetch_news_batch(symbols: list[str]) -> dict[str, list[dict]]:
    """
    여러 심볼의 뉴스를 한 번에 수집한다.
    동일 currency는 중복 요청하지 않는다.

    Returns:
        {"BTC/USDT": [...], "ETH/USDT": [...], ...}
    """
    result: dict[str, list[dict]] = {}
    fetched_currencies: dict[str, list[dict]] = {}

    for symbol in symbols:
        currency = _symbol_to_currency(symbol)
        if currency not in fetched_currencies:
            fetched_currencies[currency] = fetch_news(symbol)
        result[symbol] = fetched_currencies[currency]

    return result


def invalidate_cache(symbol: str | None = None) -> None:
    """캐시를 무효화한다. symbol이 None이면 전체 초기화."""
    global _cache
    if symbol is None:
        _cache = {}
    else:
        currency = _symbol_to_currency(symbol)
        _cache.pop(currency, None)
