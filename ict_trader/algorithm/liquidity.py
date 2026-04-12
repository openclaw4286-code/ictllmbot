"""
Liquidity Sweep (BSL/SSL) 감지.
Buy-Side Liquidity (BSL): Swing High 위 스탑로스 모음 → 상향 스윕
Sell-Side Liquidity (SSL): Swing Low 아래 스탑로스 모음 → 하향 스윕
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd
import numpy as np

from ict_trader.algorithm.market_structure import SwingPoint

logger = logging.getLogger(__name__)


@dataclass
class LiquiditySweep:
    """Liquidity Sweep 이벤트."""
    index: int
    timestamp: pd.Timestamp
    sweep_type: str         # "bsl" (buy-side) or "ssl" (sell-side)
    sweep_price: float      # 스윕된 가격 (Swing High/Low)
    wick_high: float        # 스윕 봉의 high
    wick_low: float         # 스윕 봉의 low
    close_price: float      # 스윕 봉의 close
    reclaimed: bool         # close가 레벨 안으로 되돌아왔는지


def detect_liquidity_sweeps(
    df: pd.DataFrame,
    swing_highs: list[SwingPoint],
    swing_lows: list[SwingPoint],
    lookback: int,
    direction: str | None = None,
) -> list[LiquiditySweep]:
    """
    Liquidity Sweep을 감지한다.

    BSL Sweep: 봉의 high가 Swing High를 넘었다가 close는 아래로 되돌아옴
    SSL Sweep: 봉의 low가 Swing Low를 밑돌았다가 close는 위로 되돌아옴

    Args:
        df: OHLCV DataFrame
        swing_highs: Swing High 목록
        swing_lows: Swing Low 목록
        lookback: 최근 N봉만 탐색
        direction: "bullish"(SSL 감지) / "bearish"(BSL 감지) / None(둘 다)

    Returns:
        LiquiditySweep 목록
    """
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    timestamps = df.index
    n = len(df)
    start_idx = max(0, n - lookback)

    sweeps: list[LiquiditySweep] = []

    # BSL Sweep (bearish 컨플루언스 또는 bullish 방향의 선행 스윕)
    if direction is None or direction == "bearish" or direction == "bullish":
        for sh in swing_highs:
            if sh.index >= start_idx:
                continue  # swing 자체가 lookback 범위 내면 스킵
            for i in range(max(sh.index + 1, start_idx), n):
                if highs[i] > sh.price:
                    reclaimed = closes[i] < sh.price
                    sweep = LiquiditySweep(
                        index=i, timestamp=timestamps[i],
                        sweep_type="bsl",
                        sweep_price=sh.price,
                        wick_high=float(highs[i]),
                        wick_low=float(lows[i]),
                        close_price=float(closes[i]),
                        reclaimed=reclaimed,
                    )
                    sweeps.append(sweep)
                    break  # 해당 Swing High에 대해 첫 스윕만

    # SSL Sweep (bullish 컨플루언스 또는 bearish 방향의 선행 스윕)
    if direction is None or direction == "bullish" or direction == "bearish":
        for sl in swing_lows:
            if sl.index >= start_idx:
                continue
            for i in range(max(sl.index + 1, start_idx), n):
                if lows[i] < sl.price:
                    reclaimed = closes[i] > sl.price
                    sweep = LiquiditySweep(
                        index=i, timestamp=timestamps[i],
                        sweep_type="ssl",
                        sweep_price=sl.price,
                        wick_high=float(highs[i]),
                        wick_low=float(lows[i]),
                        close_price=float(closes[i]),
                        reclaimed=reclaimed,
                    )
                    sweeps.append(sweep)
                    break

    logger.debug("Liquidity Sweep 감지: %d건", len(sweeps))
    return sweeps


def filter_direction_sweeps(
    sweeps: list[LiquiditySweep],
    direction: str,
) -> list[LiquiditySweep]:
    """
    방향에 맞는 Sweep만 필터링한다.

    Bullish 방향: SSL Sweep (매도 유동성 스윕 후 반전 상승)
    Bearish 방향: BSL Sweep (매수 유동성 스윕 후 반전 하락)
    """
    if direction == "bullish":
        return [s for s in sweeps if s.sweep_type == "ssl" and s.reclaimed]
    elif direction == "bearish":
        return [s for s in sweeps if s.sweep_type == "bsl" and s.reclaimed]
    return sweeps


def find_opposing_liquidity(
    swing_highs: list[SwingPoint],
    swing_lows: list[SwingPoint],
    entry_price: float,
    direction: str,
) -> list[float]:
    """
    TP 타겟 후보: 반대편 유동성 레벨을 찾는다.

    Bullish: Swing High들 (위쪽 BSL 타겟)
    Bearish: Swing Low들 (아래쪽 SSL 타겟)

    Returns:
        가격순 정렬된 타겟 리스트
    """
    targets: list[float] = []

    if direction == "bullish":
        for sh in swing_highs:
            if sh.price > entry_price:
                targets.append(sh.price)
        targets.sort()  # 가까운 것부터
    elif direction == "bearish":
        for sl in swing_lows:
            if sl.price < entry_price:
                targets.append(sl.price)
        targets.sort(reverse=True)  # 가까운 것부터 (높은 가격부터)

    return targets
