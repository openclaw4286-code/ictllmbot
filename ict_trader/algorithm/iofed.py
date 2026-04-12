"""
IOFED (Institutional Order Flow Entry Drill) 패턴 감지.
연속봉 후 반전 캔들 = 기관의 방향 전환 신호.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class IOFEDSignal:
    """IOFED 신호."""
    index: int
    timestamp: pd.Timestamp
    direction: str          # "bullish" or "bearish"
    consecutive_count: int  # 연속 봉 수
    reversal_price: float   # 반전 봉 close


def detect_iofed(
    df: pd.DataFrame,
    lookback: int,
    direction: str,
) -> IOFEDSignal | None:
    """
    IOFED 패턴을 감지한다.

    Bullish IOFED: 연속 하락봉(close<open) 후 상승 반전봉(close>open)
    Bearish IOFED: 연속 상승봉(close>open) 후 하락 반전봉(close<open)

    최소 3개 연속봉 후 반전이어야 유효.
    lookback 봉 이내에서만 탐색.

    Args:
        df: OHLCV DataFrame
        lookback: IOFED 유효 범위 봉 수
        direction: "bullish" or "bearish"

    Returns:
        IOFEDSignal 또는 None
    """
    if df.empty or len(df) < 4:
        return None

    opens = df["open"].values
    closes = df["close"].values
    timestamps = df.index
    n = len(df)

    start_idx = max(0, n - lookback)
    min_consecutive = 3

    # 가장 최근부터 역순 탐색
    for i in range(n - 1, start_idx, -1):
        if direction == "bullish":
            # 현재 봉이 상승봉(반전)인지
            if closes[i] <= opens[i]:
                continue
            # 이전 봉들이 연속 하락인지
            count = 0
            for j in range(i - 1, max(start_idx - 1, -1), -1):
                if closes[j] < opens[j]:
                    count += 1
                else:
                    break
            if count >= min_consecutive:
                logger.debug(
                    "IOFED bullish 감지: %d연속 하락 후 반전 @ idx=%d",
                    count, i,
                )
                return IOFEDSignal(
                    index=i, timestamp=timestamps[i],
                    direction="bullish",
                    consecutive_count=count,
                    reversal_price=float(closes[i]),
                )

        elif direction == "bearish":
            # 현재 봉이 하락봉(반전)인지
            if closes[i] >= opens[i]:
                continue
            # 이전 봉들이 연속 상승인지
            count = 0
            for j in range(i - 1, max(start_idx - 1, -1), -1):
                if closes[j] > opens[j]:
                    count += 1
                else:
                    break
            if count >= min_consecutive:
                logger.debug(
                    "IOFED bearish 감지: %d연속 상승 후 반전 @ idx=%d",
                    count, i,
                )
                return IOFEDSignal(
                    index=i, timestamp=timestamps[i],
                    direction="bearish",
                    consecutive_count=count,
                    reversal_price=float(closes[i]),
                )

    return None
