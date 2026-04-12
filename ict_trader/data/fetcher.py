"""
ccxt를 이용한 Gate.io OHLCV 데이터 수집.
"""

import logging
import asyncio

import ccxt.async_support as ccxt_async
import pandas as pd

from ict_trader.config import (
    EXCHANGE_ID,
    GATE_API_KEY,
    GATE_API_SECRET,
    HTF, MTF, LTF,
    HTF_CANDLE_LIMIT, MTF_CANDLE_LIMIT, LTF_CANDLE_LIMIT,
)

logger = logging.getLogger(__name__)

# 싱글턴 거래소 인스턴스
_exchange: ccxt_async.Exchange | None = None


def _create_exchange() -> ccxt_async.Exchange:
    """Gate.io ccxt 비동기 인스턴스 생성."""
    exchange_class = getattr(ccxt_async, EXCHANGE_ID)
    return exchange_class({
        "apiKey": GATE_API_KEY,
        "secret": GATE_API_SECRET,
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    })


async def get_exchange() -> ccxt_async.Exchange:
    """싱글턴 거래소 인스턴스를 반환한다."""
    global _exchange
    if _exchange is None:
        _exchange = _create_exchange()
    return _exchange


async def close_exchange() -> None:
    """거래소 연결을 종료한다."""
    global _exchange
    if _exchange is not None:
        await _exchange.close()
        _exchange = None


def _ohlcv_to_dataframe(ohlcv: list) -> pd.DataFrame:
    """ccxt OHLCV 리스트를 DataFrame으로 변환."""
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df = df.astype(float)
    return df


async def fetch_ohlcv(
    symbol: str,
    timeframe: str,
    limit: int,
    since: int | None = None,
) -> pd.DataFrame:
    """
    단일 심볼/타임프레임의 OHLCV를 가져온다.

    Args:
        symbol: 거래 쌍 (예: "BTC/USDT")
        timeframe: 타임프레임 (예: "4h", "15m", "5m")
        limit: 캔들 수
        since: 시작 타임스탬프 (ms), None이면 최근

    Returns:
        OHLCV DataFrame (index=timestamp UTC)
    """
    exchange = await get_exchange()
    try:
        ohlcv = await exchange.fetch_ohlcv(
            symbol, timeframe=timeframe, limit=limit, since=since
        )
        if not ohlcv:
            logger.warning("OHLCV 비어있음: %s %s", symbol, timeframe)
            return pd.DataFrame()
        df = _ohlcv_to_dataframe(ohlcv)
        logger.debug("OHLCV 수신: %s %s %d봉", symbol, timeframe, len(df))
        return df
    except Exception as e:
        logger.error("OHLCV 수신 실패: %s %s — %s", symbol, timeframe, e)
        return pd.DataFrame()


async def fetch_multi_timeframe(symbol: str) -> dict[str, pd.DataFrame]:
    """
    HTF/MTF/LTF 3개 타임프레임의 OHLCV를 동시에 가져온다.

    Returns:
        {"4h": df, "15m": df, "5m": df}
    """
    tasks = [
        fetch_ohlcv(symbol, HTF, HTF_CANDLE_LIMIT),
        fetch_ohlcv(symbol, MTF, MTF_CANDLE_LIMIT),
        fetch_ohlcv(symbol, LTF, LTF_CANDLE_LIMIT),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    frames: dict[str, pd.DataFrame] = {}
    for tf, result in zip([HTF, MTF, LTF], results):
        if isinstance(result, Exception):
            logger.error("타임프레임 %s 수신 실패: %s %s", tf, symbol, result)
            frames[tf] = pd.DataFrame()
        else:
            frames[tf] = result

    return frames


async def fetch_ohlcv_range(
    symbol: str,
    timeframe: str,
    since_ms: int,
    until_ms: int,
) -> pd.DataFrame:
    """
    백테스트용: 특정 기간의 OHLCV를 페이징하여 전부 수집한다.

    Args:
        symbol: 거래 쌍
        timeframe: 타임프레임
        since_ms: 시작 타임스탬프 (ms)
        until_ms: 종료 타임스탬프 (ms)

    Returns:
        전체 기간 OHLCV DataFrame
    """
    exchange = await get_exchange()
    all_ohlcv: list = []
    current_since = since_ms
    page_limit = 500

    while current_since < until_ms:
        try:
            ohlcv = await exchange.fetch_ohlcv(
                symbol, timeframe=timeframe, limit=page_limit, since=current_since
            )
        except Exception as e:
            logger.error("OHLCV 범위 수신 실패: %s %s since=%d — %s", symbol, timeframe, current_since, e)
            break

        if not ohlcv:
            break

        # 중복 제거: current_since 이전 데이터 무시
        for candle in ohlcv:
            if candle[0] >= current_since and candle[0] < until_ms:
                all_ohlcv.append(candle)

        last_ts = ohlcv[-1][0]
        if last_ts <= current_since:
            break
        current_since = last_ts + 1

        # rate limit 준수
        await asyncio.sleep(exchange.rateLimit / 1000)

    if not all_ohlcv:
        return pd.DataFrame()

    # 타임스탬프 기준 중복 제거
    seen = set()
    unique: list = []
    for candle in all_ohlcv:
        if candle[0] not in seen:
            seen.add(candle[0])
            unique.append(candle)

    df = _ohlcv_to_dataframe(unique)
    logger.info("OHLCV 범위 수신: %s %s %d봉", symbol, timeframe, len(df))
    return df


async def get_tick_size(symbol: str) -> dict:
    """
    심볼의 tick_size, amount precision 등 마켓 정보를 반환한다.
    gate_executor.py에서 정밀도 처리에 사용.

    Returns:
        {"price_precision": float, "amount_precision": float, "tick_size": float, "min_amount": float}
    """
    exchange = await get_exchange()
    if not exchange.markets:
        await exchange.load_markets()

    market = exchange.market(symbol)
    precision = market.get("precision", {})
    limits = market.get("limits", {})

    return {
        "price_precision": precision.get("price"),
        "amount_precision": precision.get("amount"),
        "tick_size": market.get("info", {}).get("tick_size"),
        "min_amount": limits.get("amount", {}).get("min"),
    }
