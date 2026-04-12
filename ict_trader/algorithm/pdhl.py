"""
Previous Day High/Low (PDH/PDL) Sweep 감지.
전일 고가/저가를 스윕하면 방향 보강 컨플루언스.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class PDHLSweep:
    """PDH/PDL Sweep 이벤트."""
    index: int
    timestamp: pd.Timestamp
    sweep_type: str         # "pdh" or "pdl"
    level: float            # PDH 또는 PDL 가격
    reclaimed: bool         # close가 레벨 안으로 되돌아왔는지


def get_previous_day_hl(df: pd.DataFrame) -> tuple[float | None, float | None]:
    """
    전일(UTC 기준) 고가와 저가를 구한다.

    Args:
        df: OHLCV DataFrame (UTC index)

    Returns:
        (pdh, pdl) 또는 (None, None)
    """
    if df.empty:
        return None, None

    # UTC 날짜별 그룹
    df_copy = df.copy()
    df_copy["date"] = df_copy.index.date
    dates = sorted(df_copy["date"].unique())

    if len(dates) < 2:
        return None, None

    prev_date = dates[-2]
    prev_data = df_copy[df_copy["date"] == prev_date]

    pdh = float(prev_data["high"].max())
    pdl = float(prev_data["low"].min())

    return pdh, pdl


def detect_pdhl_sweep(
    df: pd.DataFrame,
    direction: str,
) -> PDHLSweep | None:
    """
    당일 데이터에서 PDH/PDL Sweep을 감지한다.

    Bullish 보강: PDL Sweep (전일 저가 아래로 스윕 후 회복)
    Bearish 보강: PDH Sweep (전일 고가 위로 스윕 후 회복)

    Args:
        df: OHLCV DataFrame (충분한 기간 포함)
        direction: "bullish" or "bearish"

    Returns:
        PDHLSweep 또는 None
    """
    pdh, pdl = get_previous_day_hl(df)
    if pdh is None or pdl is None:
        return None

    df_copy = df.copy()
    df_copy["date"] = df_copy.index.date
    dates = sorted(df_copy["date"].unique())
    today = dates[-1]
    today_data = df_copy[df_copy["date"] == today]

    if today_data.empty:
        return None

    highs = today_data["high"].values
    lows = today_data["low"].values
    closes = today_data["close"].values
    timestamps = today_data.index

    if direction == "bullish":
        # PDL Sweep: low가 PDL 아래, close는 PDL 위
        for i in range(len(today_data)):
            if lows[i] < pdl and closes[i] > pdl:
                return PDHLSweep(
                    index=i, timestamp=timestamps[i],
                    sweep_type="pdl", level=pdl,
                    reclaimed=True,
                )
    elif direction == "bearish":
        # PDH Sweep: high가 PDH 위, close는 PDH 아래
        for i in range(len(today_data)):
            if highs[i] > pdh and closes[i] < pdh:
                return PDHLSweep(
                    index=i, timestamp=timestamps[i],
                    sweep_type="pdh", level=pdh,
                    reclaimed=True,
                )

    return None
