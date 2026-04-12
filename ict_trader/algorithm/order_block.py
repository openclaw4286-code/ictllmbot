"""
Order Block(OB) 감지 및 Active POI 판정.
큰 움직임 직전의 마지막 반대 방향 캔들이 OB.
"""

import logging
from dataclasses import dataclass

import pandas as pd
import numpy as np

from ict_trader.algorithm.market_structure import SwingPoint, StructureBreak

logger = logging.getLogger(__name__)


@dataclass
class OrderBlock:
    """Order Block."""
    index: int
    timestamp: pd.Timestamp
    ob_type: str            # "bullish" or "bearish"
    high: float             # OB 상단
    low: float              # OB 하단
    midpoint: float         # OB 중간값 (CE)
    is_active: bool = True  # 아직 미터치 상태
    is_poi: bool = False    # Active POI로 판정됨


def detect_order_blocks(
    df: pd.DataFrame,
    structure_breaks: list[StructureBreak],
    direction: str,
) -> list[OrderBlock]:
    """
    Order Block을 감지한다.

    Bullish OB: 상승 BOS/CHoCH 직전의 마지막 하락 캔들 (close < open)
    Bearish OB: 하락 BOS/CHoCH 직전의 마지막 상승 캔들 (close > open)

    Args:
        df: OHLCV DataFrame
        structure_breaks: 구조 브레이크 목록
        direction: 필터링할 방향 ("bullish" or "bearish")

    Returns:
        방향 일치 OrderBlock 목록
    """
    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    timestamps = df.index

    order_blocks: list[OrderBlock] = []

    for sb in structure_breaks:
        if sb.direction != direction:
            continue
        if sb.index < 1 or sb.index >= len(df):
            continue

        # BOS/CHoCH 발생 봉 이전에서 반대 방향 캔들 탐색
        ob_index = None
        for i in range(sb.index - 1, max(sb.index - 10, -1), -1):
            if direction == "bullish" and closes[i] < opens[i]:
                # 하락 캔들 = Bullish OB
                ob_index = i
                break
            elif direction == "bearish" and closes[i] > opens[i]:
                # 상승 캔들 = Bearish OB
                ob_index = i
                break

        if ob_index is None:
            continue

        ob = OrderBlock(
            index=ob_index,
            timestamp=timestamps[ob_index],
            ob_type=direction,
            high=float(highs[ob_index]),
            low=float(lows[ob_index]),
            midpoint=float((highs[ob_index] + lows[ob_index]) / 2),
        )

        # 이후 캔들에서 OB가 관통(미티게이트)됐는지 확인
        for j in range(ob_index + 1, len(df)):
            if direction == "bullish" and lows[j] < ob.low:
                ob.is_active = False
                break
            elif direction == "bearish" and highs[j] > ob.high:
                ob.is_active = False
                break

        if ob.is_active:
            order_blocks.append(ob)

    logger.debug("OB 감지: %s %d개 (활성)", direction, len(order_blocks))
    return order_blocks


def mark_active_poi(
    order_blocks: list[OrderBlock],
    current_price: float,
    tolerance: float,
) -> list[OrderBlock]:
    """
    현재 가격에서 tolerance 이내에 있는 OB를 Active POI로 표시한다.

    Args:
        order_blocks: OB 목록
        current_price: 현재 가격
        tolerance: 허용 오차 비율 (예: 0.005 = 0.5%)

    Returns:
        POI 표시가 업데이트된 OB 목록
    """
    for ob in order_blocks:
        if not ob.is_active:
            continue

        # OB 범위와 현재 가격의 거리
        if ob.ob_type == "bullish":
            # Bullish OB는 가격 아래에 있어야 함
            distance = (current_price - ob.high) / current_price
            if 0 <= distance <= tolerance:
                ob.is_poi = True
        elif ob.ob_type == "bearish":
            # Bearish OB는 가격 위에 있어야 함
            distance = (ob.low - current_price) / current_price
            if 0 <= distance <= tolerance:
                ob.is_poi = True

    poi_count = sum(1 for ob in order_blocks if ob.is_poi)
    if poi_count:
        logger.debug("Active POI: %d개", poi_count)
    return order_blocks


def check_poi_overlap(
    htf_obs: list[OrderBlock],
    mtf_obs: list[OrderBlock],
) -> list[tuple[OrderBlock, OrderBlock]]:
    """
    HTF와 MTF의 OB 가격 범위가 겹치는지 확인.
    겹침 = 최강 컨플루언스.

    Returns:
        겹치는 (HTF OB, MTF OB) 쌍 목록
    """
    overlaps: list[tuple[OrderBlock, OrderBlock]] = []

    for htf_ob in htf_obs:
        if not htf_ob.is_active:
            continue
        for mtf_ob in mtf_obs:
            if not mtf_ob.is_active:
                continue
            if htf_ob.ob_type != mtf_ob.ob_type:
                continue
            # 가격 범위 겹침 확인
            if htf_ob.low <= mtf_ob.high and mtf_ob.low <= htf_ob.high:
                overlaps.append((htf_ob, mtf_ob))

    if overlaps:
        logger.debug("HTF+MTF OB 겹침: %d쌍", len(overlaps))
    return overlaps
