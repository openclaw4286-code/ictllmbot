"""
Market Structure 분석.
Swing High/Low 감지, BOS(Break of Structure), CHoCH(Change of Character), 추세 판단.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class SwingPoint:
    """Swing High 또는 Swing Low 지점."""
    index: int              # DataFrame 내 위치
    timestamp: pd.Timestamp
    price: float
    swing_type: str         # "high" or "low"


@dataclass
class StructureBreak:
    """BOS 또는 CHoCH 이벤트."""
    index: int
    timestamp: pd.Timestamp
    price: float
    break_type: str         # "bos" or "choch"
    direction: str          # "bullish" or "bearish"
    broken_swing: SwingPoint


@dataclass
class MarketStructureResult:
    """Market Structure 분석 결과."""
    swing_highs: list[SwingPoint] = field(default_factory=list)
    swing_lows: list[SwingPoint] = field(default_factory=list)
    structure_breaks: list[StructureBreak] = field(default_factory=list)
    trend: str = "neutral"  # "bullish", "bearish", "neutral"


def detect_swing_points(df: pd.DataFrame, swing_bars: int) -> tuple[list[SwingPoint], list[SwingPoint]]:
    """
    Swing High/Low를 감지한다.
    swing_bars 좌우로 현재 봉이 최고/최저이면 Swing Point.

    Args:
        df: OHLCV DataFrame
        swing_bars: 좌우 비교 봉 수

    Returns:
        (swing_highs, swing_lows)
    """
    highs = df["high"].values
    lows = df["low"].values
    timestamps = df.index
    n = len(df)

    swing_highs: list[SwingPoint] = []
    swing_lows: list[SwingPoint] = []

    for i in range(swing_bars, n - swing_bars):
        # Swing High: 좌우 swing_bars개 봉보다 high가 높음
        left_highs = highs[i - swing_bars:i]
        right_highs = highs[i + 1:i + 1 + swing_bars]
        if highs[i] > np.max(left_highs) and highs[i] > np.max(right_highs):
            swing_highs.append(SwingPoint(
                index=i, timestamp=timestamps[i],
                price=float(highs[i]), swing_type="high",
            ))

        # Swing Low: 좌우 swing_bars개 봉보다 low가 낮음
        left_lows = lows[i - swing_bars:i]
        right_lows = lows[i + 1:i + 1 + swing_bars]
        if lows[i] < np.min(left_lows) and lows[i] < np.min(right_lows):
            swing_lows.append(SwingPoint(
                index=i, timestamp=timestamps[i],
                price=float(lows[i]), swing_type="low",
            ))

    return swing_highs, swing_lows


def detect_structure_breaks(
    swing_highs: list[SwingPoint],
    swing_lows: list[SwingPoint],
    df: pd.DataFrame,
) -> list[StructureBreak]:
    """
    BOS/CHoCH를 감지한다.

    - BOS (Break of Structure): 추세 방향 유지하며 구조 돌파
      - Bullish BOS: 이전 Swing High를 상향 돌파
      - Bearish BOS: 이전 Swing Low를 하향 돌파
    - CHoCH (Change of Character): 추세 반전 신호
      - Bullish CHoCH: 하락 추세에서 Swing High 돌파
      - Bearish CHoCH: 상승 추세에서 Swing Low 돌파
    """
    # 모든 swing point를 시간순 정렬
    all_swings = sorted(swing_highs + swing_lows, key=lambda s: s.index)
    if len(all_swings) < 3:
        return []

    breaks: list[StructureBreak] = []
    closes = df["close"].values
    timestamps = df.index

    # 현재 추세 추적 (최근 swing 기반)
    current_trend = "neutral"
    last_significant_high: SwingPoint | None = None
    last_significant_low: SwingPoint | None = None

    for swing in all_swings:
        if swing.swing_type == "high":
            if last_significant_high is not None:
                # 이전 Swing High보다 높은 Swing High → HH
                if swing.price > last_significant_high.price:
                    if current_trend == "bearish":
                        # 하락에서 HH → CHoCH (bullish)
                        breaks.append(StructureBreak(
                            index=swing.index, timestamp=swing.timestamp,
                            price=swing.price, break_type="choch",
                            direction="bullish", broken_swing=last_significant_high,
                        ))
                    else:
                        # 상승 유지 또는 중립 → BOS (bullish)
                        breaks.append(StructureBreak(
                            index=swing.index, timestamp=swing.timestamp,
                            price=swing.price, break_type="bos",
                            direction="bullish", broken_swing=last_significant_high,
                        ))
                    current_trend = "bullish"
            last_significant_high = swing

        elif swing.swing_type == "low":
            if last_significant_low is not None:
                # 이전 Swing Low보다 낮은 Swing Low → LL
                if swing.price < last_significant_low.price:
                    if current_trend == "bullish":
                        # 상승에서 LL → CHoCH (bearish)
                        breaks.append(StructureBreak(
                            index=swing.index, timestamp=swing.timestamp,
                            price=swing.price, break_type="choch",
                            direction="bearish", broken_swing=last_significant_low,
                        ))
                    else:
                        # 하락 유지 또는 중립 → BOS (bearish)
                        breaks.append(StructureBreak(
                            index=swing.index, timestamp=swing.timestamp,
                            price=swing.price, break_type="bos",
                            direction="bearish", broken_swing=last_significant_low,
                        ))
                    current_trend = "bearish"
            last_significant_low = swing

    return breaks


def determine_trend(
    swing_highs: list[SwingPoint],
    swing_lows: list[SwingPoint],
    structure_breaks: list[StructureBreak],
) -> str:
    """
    현재 추세를 판단한다.

    1차: 최근 Swing High/Low 패턴 (HH+HL=bullish, LH+LL=bearish)
    2차: BOS/CHoCH 방향
    """
    # 최근 2개 이상의 Swing High/Low가 있어야 판단
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        recent_highs = swing_highs[-2:]
        recent_lows = swing_lows[-2:]

        hh = recent_highs[-1].price > recent_highs[-2].price  # Higher High
        hl = recent_lows[-1].price > recent_lows[-2].price     # Higher Low
        lh = recent_highs[-1].price < recent_highs[-2].price   # Lower High
        ll = recent_lows[-1].price < recent_lows[-2].price     # Lower Low

        if hh and hl:
            return "bullish"
        if lh and ll:
            return "bearish"

    # 불명확 → 최근 BOS/CHoCH로 판단
    if structure_breaks:
        last_break = structure_breaks[-1]
        return last_break.direction

    return "neutral"


def analyze(df: pd.DataFrame, swing_bars: int) -> MarketStructureResult:
    """
    Market Structure 전체 분석을 실행한다.

    Args:
        df: OHLCV DataFrame
        swing_bars: Swing 감지 좌우 봉 수 (config에서 가져옴)

    Returns:
        MarketStructureResult
    """
    if df.empty or len(df) < swing_bars * 2 + 1:
        logger.warning("데이터 부족: Market Structure 분석 불가 (%d봉)", len(df))
        return MarketStructureResult()

    swing_highs, swing_lows = detect_swing_points(df, swing_bars)
    structure_breaks = detect_structure_breaks(swing_highs, swing_lows, df)
    trend = determine_trend(swing_highs, swing_lows, structure_breaks)

    result = MarketStructureResult(
        swing_highs=swing_highs,
        swing_lows=swing_lows,
        structure_breaks=structure_breaks,
        trend=trend,
    )
    logger.debug(
        "Market Structure: trend=%s, SH=%d, SL=%d, breaks=%d",
        trend, len(swing_highs), len(swing_lows), len(structure_breaks),
    )
    return result
