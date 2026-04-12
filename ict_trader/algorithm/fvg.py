"""
Fair Value Gap(FVG) 감지, Consequent Encroachment(CE) 계산, Fill 판정.
3-캔들 패턴에서 1번째와 3번째 캔들 사이의 갭.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class FairValueGap:
    """Fair Value Gap."""
    index: int              # 중간(2번째) 캔들 위치
    timestamp: pd.Timestamp
    fvg_type: str           # "bullish" or "bearish"
    high: float             # FVG 상단
    low: float              # FVG 하단
    ce: float               # Consequent Encroachment (중간값)
    fill_pct: float = 0.0   # 채워진 비율 (0.0~1.0)
    is_filled: bool = False # 50% 이상 채워짐
    is_active: bool = True  # 완전히 닫히지 않음


def detect_fvg(df: pd.DataFrame, direction: str) -> list[FairValueGap]:
    """
    FVG를 감지한다.

    Bullish FVG: candle[i-1].high < candle[i+1].low (갭 상승)
    Bearish FVG: candle[i-1].low > candle[i+1].high (갭 하락)

    Args:
        df: OHLCV DataFrame
        direction: "bullish" or "bearish"

    Returns:
        방향 일치 FVG 목록
    """
    highs = df["high"].values
    lows = df["low"].values
    timestamps = df.index
    n = len(df)

    fvgs: list[FairValueGap] = []

    for i in range(1, n - 1):
        if direction == "bullish":
            # 1번째 캔들 high < 3번째 캔들 low → 상승 갭
            gap_low = highs[i - 1]
            gap_high = lows[i + 1]
            if gap_high > gap_low:
                ce = (gap_high + gap_low) / 2
                fvgs.append(FairValueGap(
                    index=i, timestamp=timestamps[i],
                    fvg_type="bullish",
                    high=float(gap_high), low=float(gap_low),
                    ce=float(ce),
                ))

        elif direction == "bearish":
            # 1번째 캔들 low > 3번째 캔들 high → 하락 갭
            gap_high = lows[i - 1]
            gap_low = highs[i + 1]
            if gap_high > gap_low:
                ce = (gap_high + gap_low) / 2
                fvgs.append(FairValueGap(
                    index=i, timestamp=timestamps[i],
                    fvg_type="bearish",
                    high=float(gap_high), low=float(gap_low),
                    ce=float(ce),
                ))

    # 이후 캔들로 Fill 상태 업데이트
    closes = df["close"].values
    for fvg in fvgs:
        _update_fill_status(fvg, df, fvg.index + 2)

    active_count = sum(1 for f in fvgs if f.is_active)
    logger.debug("FVG 감지: %s 전체=%d, 활성=%d", direction, len(fvgs), active_count)
    return fvgs


def _update_fill_status(fvg: FairValueGap, df: pd.DataFrame, start_idx: int) -> None:
    """FVG의 채움 상태를 업데이트한다."""
    gap_size = fvg.high - fvg.low
    if gap_size <= 0:
        fvg.is_active = False
        return

    highs = df["high"].values
    lows = df["low"].values
    max_fill = 0.0

    for j in range(start_idx, len(df)):
        if fvg.fvg_type == "bullish":
            # 가격이 FVG 안으로 내려온 정도
            if lows[j] < fvg.high:
                penetration = fvg.high - max(lows[j], fvg.low)
                fill = penetration / gap_size
                max_fill = max(max_fill, fill)
            # 완전 관통 시 비활성
            if lows[j] <= fvg.low:
                fvg.is_active = False
                max_fill = 1.0
                break
        elif fvg.fvg_type == "bearish":
            # 가격이 FVG 안으로 올라온 정도
            if highs[j] > fvg.low:
                penetration = min(highs[j], fvg.high) - fvg.low
                fill = penetration / gap_size
                max_fill = max(max_fill, fill)
            # 완전 관통 시 비활성
            if highs[j] >= fvg.high:
                fvg.is_active = False
                max_fill = 1.0
                break

    fvg.fill_pct = min(max_fill, 1.0)
    fvg.is_filled = fvg.fill_pct >= 0.5


def check_ce_entry(
    fvgs: list[FairValueGap],
    current_price: float,
    threshold: float,
) -> FairValueGap | None:
    """
    현재 가격이 FVG CE(중간값)에서 threshold 이내인지 확인.

    Args:
        fvgs: FVG 목록
        current_price: 현재 가격
        threshold: 허용 오차 비율

    Returns:
        조건 충족하는 FVG (없으면 None)
    """
    for fvg in reversed(fvgs):  # 최근 것 우선
        if not fvg.is_active:
            continue
        distance = abs(current_price - fvg.ce) / current_price
        if distance <= threshold:
            logger.debug(
                "FVG CE 진입 감지: %s CE=%.2f price=%.2f dist=%.4f",
                fvg.fvg_type, fvg.ce, current_price, distance,
            )
            return fvg
    return None


def check_fvg_fill_entry(fvgs: list[FairValueGap]) -> FairValueGap | None:
    """
    50% 이상 채워진 미완료 FVG를 찾는다 (Fill 진입 조건).

    Returns:
        조건 충족하는 FVG (없으면 None)
    """
    for fvg in reversed(fvgs):
        if fvg.is_active and fvg.is_filled:
            logger.debug(
                "FVG Fill 진입 감지: %s fill=%.1f%%",
                fvg.fvg_type, fvg.fill_pct * 100,
            )
            return fvg
    return None


def mark_active_poi(
    fvgs: list[FairValueGap],
    current_price: float,
    tolerance: float,
) -> list[FairValueGap]:
    """현재 가격에서 tolerance 이내에 있는 FVG를 Active POI로 표시한다."""
    for fvg in fvgs:
        if not fvg.is_active:
            continue
        if fvg.fvg_type == "bullish":
            distance = (current_price - fvg.high) / current_price
            if 0 <= distance <= tolerance:
                fvg.is_active = True  # 이미 True이므로 표시 목적
        elif fvg.fvg_type == "bearish":
            distance = (fvg.low - current_price) / current_price
            if 0 <= distance <= tolerance:
                fvg.is_active = True
    return fvgs


def check_fvg_overlap(
    htf_fvgs: list[FairValueGap],
    mtf_fvgs: list[FairValueGap],
) -> list[tuple[FairValueGap, FairValueGap]]:
    """HTF와 MTF FVG 가격 범위 겹침 확인."""
    overlaps: list[tuple[FairValueGap, FairValueGap]] = []

    for htf in htf_fvgs:
        if not htf.is_active:
            continue
        for mtf in mtf_fvgs:
            if not mtf.is_active:
                continue
            if htf.fvg_type != mtf.fvg_type:
                continue
            if htf.low <= mtf.high and mtf.low <= htf.high:
                overlaps.append((htf, mtf))

    return overlaps
