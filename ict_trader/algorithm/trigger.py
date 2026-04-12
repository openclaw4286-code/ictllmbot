"""
3단계 탑다운 분석 및 TriggerEvent 생성.
HTF(4H) → MTF(15M) → LTF(5M) 분석, SL/TP/R:R 산출.
점수 시스템 없음 — 3단계 통과 + R:R 충족이면 LLM에 전달.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from ict_trader.config import get_active_params

from ict_trader.algorithm.market_structure import (
    analyze as ms_analyze,
    MarketStructureResult, SwingPoint,
)
from ict_trader.algorithm.order_block import (
    detect_order_blocks, mark_active_poi as ob_mark_poi,
    check_poi_overlap as ob_check_overlap, OrderBlock,
)
from ict_trader.algorithm.fvg import (
    detect_fvg, check_ce_entry, check_fvg_fill_entry,
    check_fvg_overlap, FairValueGap,
)
from ict_trader.algorithm.liquidity import (
    detect_liquidity_sweeps, filter_direction_sweeps,
    find_opposing_liquidity, LiquiditySweep,
)
from ict_trader.algorithm.pdhl import detect_pdhl_sweep, PDHLSweep
from ict_trader.algorithm.iofed import detect_iofed, IOFEDSignal
from ict_trader.algorithm.session import (
    get_current_session, detect_session_range_sweep,
    get_session_display_name,
)

logger = logging.getLogger(__name__)


@dataclass
class Confluence:
    """개별 컨플루언스 항목 (설명용, 점수 없음)."""
    name: str
    detail: str = ""


@dataclass
class TriggerEvent:
    """알고리즘 트리거 이벤트. LLM 검토 대상."""
    symbol: str
    timestamp: pd.Timestamp
    direction: str              # "bullish" or "bearish"
    entry_price: float
    stop_loss: float
    take_profit: float
    rr_ratio: float             # R:R 비율
    entry_type: str             # "fvg_ce" / "fvg_fill" / "iofed"
    session: str                # 세션 이름
    confluences: list[Confluence] = field(default_factory=list)

    # 분석 데이터 (LLM 프롬프트 구성용)
    htf_trend: str = ""
    htf_obs: list = field(default_factory=list)
    htf_fvgs: list = field(default_factory=list)
    mtf_obs: list = field(default_factory=list)
    mtf_fvgs: list = field(default_factory=list)
    poi_overlaps: list = field(default_factory=list)
    ltf_sweeps: list = field(default_factory=list)
    pdhl_sweep: PDHLSweep | None = None
    iofed_signal: IOFEDSignal | None = None


# ──────────────────────────────────────────────
# SL 결정
# ──────────────────────────────────────────────

def _determine_sl(
    direction: str,
    entry_price: float,
    htf_obs: list[OrderBlock],
    mtf_obs: list[OrderBlock],
    ltf_ms: MarketStructureResult,
    pdhl_sweep: PDHLSweep | None,
    sl_max_risk_pct: float,
) -> float:
    """SL 우선순위: HTF OB → MTF OB → LTF Swing → PDH/PDL → 고정% 폴백."""
    candidates: list[float] = []

    for ob in htf_obs:
        if direction == "bullish":
            candidates.append(ob.low)
        else:
            candidates.append(ob.high)

    for ob in mtf_obs:
        if direction == "bullish":
            candidates.append(ob.low)
        else:
            candidates.append(ob.high)

    if direction == "bullish" and ltf_ms.swing_lows:
        candidates.append(ltf_ms.swing_lows[-1].price)
    elif direction == "bearish" and ltf_ms.swing_highs:
        candidates.append(ltf_ms.swing_highs[-1].price)

    if pdhl_sweep:
        candidates.append(pdhl_sweep.level)

    valid: list[float] = []
    for sl in candidates:
        risk = abs(entry_price - sl) / entry_price
        if risk <= sl_max_risk_pct and risk > 0:
            if direction == "bullish" and sl < entry_price:
                valid.append(sl)
            elif direction == "bearish" and sl > entry_price:
                valid.append(sl)

    if valid:
        if direction == "bullish":
            return max(valid)
        else:
            return min(valid)

    if direction == "bullish":
        return entry_price * (1 - sl_max_risk_pct)
    else:
        return entry_price * (1 + sl_max_risk_pct)


# ──────────────────────────────────────────────
# TP 결정
# ──────────────────────────────────────────────

def _determine_tp(
    direction: str,
    entry_price: float,
    stop_loss: float,
    htf_ms: MarketStructureResult,
    mtf_ms: MarketStructureResult,
    rr_min: float,
    rr_max: float,
) -> tuple[float, float]:
    """TP: opposing liquidity 후보 중 R:R 충족하는 첫 번째."""
    risk = abs(entry_price - stop_loss)
    if risk == 0:
        return entry_price, 0.0

    all_highs = htf_ms.swing_highs + mtf_ms.swing_highs
    all_lows = htf_ms.swing_lows + mtf_ms.swing_lows
    targets = find_opposing_liquidity(all_highs, all_lows, entry_price, direction)

    for target in targets:
        reward = abs(target - entry_price)
        rr = reward / risk
        if rr_min <= rr <= rr_max:
            return target, round(rr, 2)

    if direction == "bullish":
        fallback_tp = entry_price + risk * rr_min
    else:
        fallback_tp = entry_price - risk * rr_min

    return fallback_tp, rr_min


# ──────────────────────────────────────────────
# 컨플루언스 수집 (점수 없이 설명만)
# ──────────────────────────────────────────────

def _collect_confluences(
    htf_trend_aligned: bool,
    htf_obs_active: list[OrderBlock],
    htf_fvgs_active: list[FairValueGap],
    htf_sweeps: list[LiquiditySweep],
    htf_pdhl: PDHLSweep | None,
    mtf_bos_choch: bool,
    mtf_obs_active: list[OrderBlock],
    mtf_fvgs_active: list[FairValueGap],
    mtf_sweeps: list[LiquiditySweep],
    poi_overlaps: list,
    entry_type: str,
    ltf_session_sweep: bool,
    ltf_sweeps: list[LiquiditySweep],
    ltf_bos_choch: bool,
) -> list[Confluence]:
    """감지된 컨플루언스를 리스트로 수집한다 (LLM 전달용)."""
    confluences: list[Confluence] = []

    if htf_trend_aligned:
        confluences.append(Confluence("HTF 추세 일치"))
    if htf_obs_active:
        confluences.append(Confluence("HTF OB Active", f"{len(htf_obs_active)}개"))
    if htf_fvgs_active:
        confluences.append(Confluence("HTF FVG Active", f"{len(htf_fvgs_active)}개"))
    if htf_sweeps:
        confluences.append(Confluence("HTF Liquidity Sweep", f"{len(htf_sweeps)}건"))
    if htf_pdhl:
        confluences.append(Confluence("PDH/PDL Sweep", htf_pdhl.sweep_type.upper()))
    if mtf_bos_choch:
        confluences.append(Confluence("MTF BOS/CHoCH"))
    if mtf_obs_active:
        confluences.append(Confluence("MTF OB Active", f"{len(mtf_obs_active)}개"))
    if mtf_fvgs_active:
        confluences.append(Confluence("MTF FVG Active", f"{len(mtf_fvgs_active)}개"))
    if mtf_sweeps:
        confluences.append(Confluence("MTF Liquidity Sweep", f"{len(mtf_sweeps)}건"))
    if poi_overlaps:
        confluences.append(Confluence("HTF+MTF POI 겹침", f"{len(poi_overlaps)}쌍"))
    if entry_type == "fvg_ce":
        confluences.append(Confluence("LTF FVG CE 진입"))
    elif entry_type == "fvg_fill":
        confluences.append(Confluence("LTF FVG Fill 진입"))
    elif entry_type == "iofed":
        confluences.append(Confluence("LTF IOFED 진입"))
    if ltf_session_sweep:
        confluences.append(Confluence("Session Range Sweep"))
    if ltf_sweeps:
        confluences.append(Confluence("LTF Liquidity Sweep", f"{len(ltf_sweeps)}건"))
    if ltf_bos_choch:
        confluences.append(Confluence("LTF BOS/CHoCH"))

    return confluences


# ──────────────────────────────────────────────
# 메인 탑다운 분석
# ──────────────────────────────────────────────

def analyze_topdown(
    symbol: str,
    htf_df: pd.DataFrame,
    mtf_df: pd.DataFrame,
    ltf_df: pd.DataFrame,
    session_name: str | None = None,
) -> TriggerEvent | None:
    """
    3단계 탑다운 분석을 실행하고 TriggerEvent를 생성한다.
    HTF(4H) → MTF(15M) → LTF(5M) 순서.
    모든 단계 충족 + R:R 기준 충족 시 TriggerEvent 반환.
    점수 필터 없음 — LLM이 최종 판단.
    """
    params = get_active_params()

    if htf_df.empty or mtf_df.empty or ltf_df.empty:
        logger.debug("%s: 데이터 부족, 분석 스킵", symbol)
        return None

    current_price = float(ltf_df["close"].iloc[-1])
    if session_name is None:
        session_name = get_current_session()

    # ────────── STEP 1: HTF 분석 ──────────
    htf_ms = ms_analyze(htf_df, params["swing_bars"])
    direction = htf_ms.trend

    if direction == "neutral":
        logger.debug("%s: HTF 추세 중립, 스킵", symbol)
        return None

    htf_obs = detect_order_blocks(htf_df, htf_ms.structure_breaks, direction)
    htf_obs = ob_mark_poi(htf_obs, current_price, params["htf_poi_tolerance"])
    htf_fvgs = detect_fvg(htf_df, direction)

    htf_sweeps_all = detect_liquidity_sweeps(
        htf_df, htf_ms.swing_highs, htf_ms.swing_lows, params["liquidity_lookback"],
    )
    htf_sweeps = filter_direction_sweeps(htf_sweeps_all, direction)
    pdhl_sweep = detect_pdhl_sweep(htf_df, direction)

    # ────────── STEP 2: MTF 분석 ──────────
    mtf_ms = ms_analyze(mtf_df, params["swing_bars"])

    mtf_bos_choch = any(
        sb.direction == direction for sb in mtf_ms.structure_breaks
    )

    mtf_obs = detect_order_blocks(mtf_df, mtf_ms.structure_breaks, direction)
    mtf_obs = ob_mark_poi(mtf_obs, current_price, params["htf_poi_tolerance"])
    mtf_fvgs = detect_fvg(mtf_df, direction)

    mtf_has_structure = mtf_bos_choch
    mtf_has_poi = bool(mtf_obs) or bool([f for f in mtf_fvgs if f.is_active])

    if not mtf_has_structure and not mtf_has_poi:
        logger.debug("%s: MTF 조건 미충족, 스킵", symbol)
        return None

    mtf_sweeps_all = detect_liquidity_sweeps(
        mtf_df, mtf_ms.swing_highs, mtf_ms.swing_lows, params["liquidity_lookback"],
    )
    mtf_sweeps = filter_direction_sweeps(mtf_sweeps_all, direction)

    ob_overlaps = ob_check_overlap(htf_obs, mtf_obs)
    fvg_overlaps = check_fvg_overlap(htf_fvgs, mtf_fvgs)
    poi_overlaps = ob_overlaps + fvg_overlaps

    # ────────── STEP 3: LTF 분석 ──────────
    ltf_ms = ms_analyze(ltf_df, max(2, params["swing_bars"] - 2))

    entry_type: str | None = None
    entry_price = current_price

    ltf_fvgs = detect_fvg(ltf_df, direction)
    active_ltf_fvgs = [f for f in ltf_fvgs if f.is_active]
    ce_fvg = check_ce_entry(active_ltf_fvgs, current_price, params["fvg_ce_threshold"])
    if ce_fvg:
        entry_type = "fvg_ce"
        entry_price = ce_fvg.ce

    if entry_type is None:
        fill_fvg = check_fvg_fill_entry(active_ltf_fvgs)
        if fill_fvg:
            entry_type = "fvg_fill"

    if entry_type is None:
        iofed_signal = detect_iofed(ltf_df, params["iofed_lookback"], direction)
        if iofed_signal:
            entry_type = "iofed"
            entry_price = iofed_signal.reversal_price
    else:
        iofed_signal = None

    if entry_type is None:
        logger.debug("%s: LTF 진입 조건 미충족, 스킵", symbol)
        return None

    # LTF 추가 컨플루언스
    ltf_session_sweep = False
    if session_name:
        prev_sessions = {"asia": "new_york", "london": "asia", "new_york": "london"}
        prev_session = prev_sessions.get(session_name or "")
        if prev_session:
            ltf_session_sweep = detect_session_range_sweep(ltf_df, prev_session, direction)

    ltf_sweeps_all = detect_liquidity_sweeps(
        ltf_df, ltf_ms.swing_highs, ltf_ms.swing_lows, params["liquidity_lookback"],
    )
    ltf_sweeps = filter_direction_sweeps(ltf_sweeps_all, direction)
    ltf_bos_choch = any(sb.direction == direction for sb in ltf_ms.structure_breaks)

    # ────────── SL / TP / R:R ──────────
    stop_loss = _determine_sl(
        direction, entry_price, htf_obs, mtf_obs,
        ltf_ms, pdhl_sweep, params["sl_max_risk_pct"],
    )

    if poi_overlaps:
        rr_min = params["rr_min_strong"]
    elif any(ob.is_poi for ob in htf_obs):
        rr_min = params["rr_min_with_poi"]
    else:
        rr_min = params["rr_min_default"]

    take_profit, rr_ratio = _determine_tp(
        direction, entry_price, stop_loss,
        htf_ms, mtf_ms, rr_min, params["rr_max"],
    )

    if rr_ratio < rr_min:
        logger.debug("%s: R:R %.2f < 최소 %.2f, 스킵", symbol, rr_ratio, rr_min)
        return None

    # ────────── 컨플루언스 수집 ──────────
    htf_obs_active = [ob for ob in htf_obs if ob.is_poi]
    htf_fvgs_active = [f for f in htf_fvgs if f.is_active]
    mtf_obs_active = [ob for ob in mtf_obs if ob.is_poi or ob.is_active]
    mtf_fvgs_active = [f for f in mtf_fvgs if f.is_active]

    confluences = _collect_confluences(
        htf_trend_aligned=(direction == htf_ms.trend),
        htf_obs_active=htf_obs_active,
        htf_fvgs_active=htf_fvgs_active,
        htf_sweeps=htf_sweeps,
        htf_pdhl=pdhl_sweep,
        mtf_bos_choch=mtf_bos_choch,
        mtf_obs_active=mtf_obs_active,
        mtf_fvgs_active=mtf_fvgs_active,
        mtf_sweeps=mtf_sweeps,
        poi_overlaps=poi_overlaps,
        entry_type=entry_type,
        ltf_session_sweep=ltf_session_sweep,
        ltf_sweeps=ltf_sweeps,
        ltf_bos_choch=ltf_bos_choch,
    )

    trigger = TriggerEvent(
        symbol=symbol,
        timestamp=ltf_df.index[-1],
        direction=direction,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        rr_ratio=rr_ratio,
        entry_type=entry_type,
        session=get_session_display_name(session_name),
        confluences=confluences,
        htf_trend=direction,
        htf_obs=htf_obs,
        htf_fvgs=htf_fvgs,
        mtf_obs=mtf_obs,
        mtf_fvgs=mtf_fvgs,
        poi_overlaps=poi_overlaps,
        ltf_sweeps=ltf_sweeps,
        pdhl_sweep=pdhl_sweep,
        iofed_signal=iofed_signal if entry_type == "iofed" else None,
    )

    logger.info(
        "TriggerEvent 생성: %s %s %s | R:R=%.2f 진입=$%s | 컨플루언스 %d개",
        symbol, direction.upper(), entry_type,
        rr_ratio, f"{entry_price:,.2f}", len(confluences),
    )

    return trigger
