"""
CoinGecko 시총 상위 코인 유니버스 관리.
스테이블코인 제외, 1시간 캐시, Gate.io USDT 마켓 매핑.
"""

from __future__ import annotations

import time
import logging
import requests

from ict_trader.config import (
    COINGECKO_API_KEY,
    UNIVERSE_TOP_N,
    UNIVERSE_CACHE_TTL,
    UNIVERSE_EXCLUDE_STABLECOINS,
)

logger = logging.getLogger(__name__)

# 캐시
_cache: list[str] = []
_cache_ts: float = 0.0

# 제외할 스테이블코인 심볼
STABLECOINS = {
    "USDT", "USDC", "DAI", "BUSD", "TUSD", "USDP", "FDUSD",
    "FRAX", "USDD", "PYUSD", "GUSD", "LUSD", "CRVUSD", "GHO",
    "USDE", "USD0",
}

COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/markets"


def fetch_top_coins() -> list[str]:
    """
    CoinGecko에서 시총 상위 코인을 가져와 Gate.io USDT 심볼 리스트로 반환.
    예: ["BTC/USDT", "ETH/USDT", ...]
    캐시 TTL 이내면 캐시된 결과를 반환한다.
    """
    global _cache, _cache_ts

    now = time.time()
    if _cache and (now - _cache_ts) < UNIVERSE_CACHE_TTL:
        logger.debug("유니버스 캐시 사용 (남은 %ds)", int(UNIVERSE_CACHE_TTL - (now - _cache_ts)))
        return _cache

    # 스테이블코인 제외를 위해 여유분 요청
    fetch_count = UNIVERSE_TOP_N + len(STABLECOINS) + 10

    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": fetch_count,
        "page": 1,
        "sparkline": "false",
    }
    headers = {}
    if COINGECKO_API_KEY:
        headers["x-cg-demo-api-key"] = COINGECKO_API_KEY

    try:
        resp = requests.get(COINGECKO_URL, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.error("CoinGecko API 오류: %s", e)
        if _cache:
            logger.warning("이전 캐시 반환 (만료됨)")
            return _cache
        return []

    symbols: list[str] = []
    for coin in data:
        ticker = coin.get("symbol", "").upper()
        if UNIVERSE_EXCLUDE_STABLECOINS and ticker in STABLECOINS:
            continue
        symbols.append(f"{ticker}/USDT")
        if len(symbols) >= UNIVERSE_TOP_N:
            break

    _cache = symbols
    _cache_ts = now
    logger.info("유니버스 갱신: %d개 심볼", len(symbols))
    return symbols


def invalidate_cache() -> None:
    """캐시를 수동으로 무효화한다."""
    global _cache, _cache_ts
    _cache = []
    _cache_ts = 0.0
