"""
암호화폐 뉴스 수집.
무료 RSS 피드 (CoinDesk, CoinTelegraph) 사용. API 키 불필요.
심볼당 5분 캐시, LLM에 전달할 뉴스 최대 N개.
"""

from __future__ import annotations

import re
import time
import logging
import xml.etree.ElementTree as ET

import requests

from ict_trader.config import NEWS_CACHE_TTL, NEWS_MAX_ITEMS

logger = logging.getLogger(__name__)

# 무료 RSS 피드 소스
RSS_FEEDS = [
    {
        "name": "CoinDesk",
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    },
    {
        "name": "CoinTelegraph",
        "url": "https://cointelegraph.com/rss",
    },
]

# 캐시: {currency: {"data": [...], "ts": float}}
_cache: dict[str, dict] = {}

# 전체 뉴스 캐시 (피드 단위)
_feed_cache: list[dict] = []
_feed_cache_ts: float = 0.0
_FEED_CACHE_TTL = 300  # 5분


def _symbol_to_currency(symbol: str) -> str:
    """'BTC/USDT' → 'BTC'"""
    return symbol.split("/")[0].upper()


def _fetch_rss_feeds() -> list[dict]:
    """RSS 피드에서 최근 뉴스를 수집한다."""
    global _feed_cache, _feed_cache_ts

    now = time.time()
    if _feed_cache and (now - _feed_cache_ts) < _FEED_CACHE_TTL:
        return _feed_cache

    all_news: list[dict] = []
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    for feed in RSS_FEEDS:
        try:
            resp = requests.get(feed["url"], headers=headers, timeout=10)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)

            # RSS 2.0 파싱
            items = root.findall(".//item")
            for item in items[:20]:  # 피드당 최대 20개
                title = item.findtext("title", "").strip()
                link = item.findtext("link", "").strip()
                pub_date = item.findtext("pubDate", "").strip()

                if title:
                    all_news.append({
                        "title": title,
                        "source": feed["name"],
                        "url": link,
                        "published_at": pub_date,
                        "kind": "news",
                    })

        except Exception as e:
            logger.warning("RSS 수신 실패 (%s): %s", feed["name"], e)

    _feed_cache = all_news
    _feed_cache_ts = now
    logger.info("RSS 뉴스 수신: %d건", len(all_news))
    return all_news


def _filter_by_currency(news_list: list[dict], currency: str) -> list[dict]:
    """뉴스 제목에서 통화명/심볼이 포함된 것만 필터링."""
    # 통화명 매핑 (주요 코인)
    aliases = {
        "BTC": ["bitcoin", "btc"],
        "ETH": ["ethereum", "eth", "ether"],
        "XRP": ["ripple", "xrp"],
        "SOL": ["solana", "sol"],
        "BNB": ["binance", "bnb"],
        "ADA": ["cardano", "ada"],
        "DOGE": ["dogecoin", "doge"],
        "DOT": ["polkadot", "dot"],
        "AVAX": ["avalanche", "avax"],
        "LINK": ["chainlink", "link"],
        "MATIC": ["polygon", "matic"],
        "UNI": ["uniswap", "uni"],
        "ATOM": ["cosmos", "atom"],
        "LTC": ["litecoin", "ltc"],
        "TRX": ["tron", "trx"],
        "NEAR": ["near"],
        "APT": ["aptos", "apt"],
        "SUI": ["sui"],
        "PEPE": ["pepe"],
        "SHIB": ["shiba", "shib"],
    }

    keywords = aliases.get(currency, [currency.lower()])
    # "crypto", "bitcoin" 같은 일반 키워드도 BTC에 포함
    if currency == "BTC":
        keywords.extend(["crypto", "market", "fed", "rate"])

    filtered: list[dict] = []
    for news in news_list:
        title_lower = news["title"].lower()
        if any(kw in title_lower for kw in keywords):
            filtered.append(news)

    return filtered


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

    # 심볼별 캐시 확인
    if currency in _cache:
        cached = _cache[currency]
        if (now - cached["ts"]) < NEWS_CACHE_TTL:
            return cached["data"]

    # RSS 피드에서 전체 뉴스 수집
    all_news = _fetch_rss_feeds()

    # 통화별 필터링
    filtered = _filter_by_currency(all_news, currency)[:NEWS_MAX_ITEMS]

    # 필터 결과가 없으면 일반 크립토 뉴스 반환
    if not filtered:
        filtered = all_news[:NEWS_MAX_ITEMS]

    _cache[currency] = {"data": filtered, "ts": now}
    logger.debug("뉴스 조회: %s %d건", currency, len(filtered))
    return filtered


def fetch_news_batch(symbols: list[str]) -> dict[str, list[dict]]:
    """여러 심볼의 뉴스를 한 번에 수집한다."""
    result: dict[str, list[dict]] = {}
    fetched_currencies: dict[str, list[dict]] = {}

    for symbol in symbols:
        currency = _symbol_to_currency(symbol)
        if currency not in fetched_currencies:
            fetched_currencies[currency] = fetch_news(symbol)
        result[symbol] = fetched_currencies[currency]

    return result


def invalidate_cache(symbol: str | None = None) -> None:
    """캐시를 무효화한다."""
    global _cache, _feed_cache, _feed_cache_ts
    if symbol is None:
        _cache = {}
        _feed_cache = []
        _feed_cache_ts = 0.0
    else:
        currency = _symbol_to_currency(symbol)
        _cache.pop(currency, None)
