"""
Gate.io 주문 실행.
tick_size 정밀도 처리, 켈리 포지션 사이징, 주문 생성.
모든 주문은 반드시 정밀도 처리 함수를 통과해야 한다.
"""

from __future__ import annotations

import math
import logging
import asyncio

from ict_trader.config import (
    LEVERAGE_MIN, LEVERAGE_MAX, LIQUIDATION_SAFETY_BUFFER,
    PAPER_TRADING,
    RISK_PER_TRADE, MAX_CONCURRENT_POSITIONS,
)
from ict_trader.data.fetcher import get_exchange, get_tick_size
from ict_trader.algorithm.trigger import TriggerEvent

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 정밀도 처리 함수
# ──────────────────────────────────────────────

def _precision_digits(value: float | str | None) -> int:
    """precision 값에서 소수점 자릿수를 결정한다."""
    if value is None:
        return 8
    val = float(value)
    if val <= 0:
        return 8
    if val >= 1:
        return 0
    return max(0, -int(math.floor(math.log10(val))))


def round_price_floor(price: float, tick_size: float | str | None) -> float:
    """가격을 tick_size 기준 내림 (Long SL, Short TP 용)."""
    if tick_size is None or float(tick_size) <= 0:
        return price
    ts = float(tick_size)
    digits = _precision_digits(ts)
    floored = math.floor(price / ts) * ts
    return round(floored, digits)


def round_price_ceil(price: float, tick_size: float | str | None) -> float:
    """가격을 tick_size 기준 올림 (Short SL, Long TP 용)."""
    if tick_size is None or float(tick_size) <= 0:
        return price
    ts = float(tick_size)
    digits = _precision_digits(ts)
    ceiled = math.ceil(price / ts) * ts
    return round(ceiled, digits)


def round_amount_floor(amount: float, precision: float | str | None) -> float:
    """수량을 precision 기준 내림."""
    if precision is None or float(precision) <= 0:
        return amount
    prec = float(precision)
    digits = _precision_digits(prec)
    floored = math.floor(amount / prec) * prec
    return round(floored, digits)


def adjust_order_precision(
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    amount: float,
    market_info: dict,
) -> dict:
    """
    주문의 모든 가격/수량을 tick_size/precision에 맞게 조정한다.
    모든 주문은 반드시 이 함수를 통과해야 한다.

    Long SL: floor / Long TP: ceil
    Short SL: ceil / Short TP: floor
    Amount: 항상 floor

    Args:
        direction: "bullish" or "bearish"
        entry_price: 진입가
        stop_loss: 손절가
        take_profit: 목표가
        amount: 수량
        market_info: get_tick_size() 반환값

    Returns:
        조정된 {"entry_price", "stop_loss", "take_profit", "amount"}
    """
    tick = market_info.get("tick_size") or market_info.get("price_precision")
    amt_prec = market_info.get("amount_precision")

    if direction == "bullish":
        adj_sl = round_price_floor(stop_loss, tick)
        adj_tp = round_price_ceil(take_profit, tick)
    else:
        adj_sl = round_price_ceil(stop_loss, tick)
        adj_tp = round_price_floor(take_profit, tick)

    adj_entry = round_price_floor(entry_price, tick)  # 시장가 진입이므로 참고용
    adj_amount = round_amount_floor(amount, amt_prec)

    min_amount = market_info.get("min_amount")
    if min_amount and adj_amount < float(min_amount):
        logger.warning(
            "수량 %.6f < 최소 %s, 주문 불가",
            adj_amount, min_amount,
        )
        adj_amount = 0.0

    return {
        "entry_price": adj_entry,
        "stop_loss": adj_sl,
        "take_profit": adj_tp,
        "amount": adj_amount,
    }


# ──────────────────────────────────────────────
# 거래 이력 추적 (Kelly 승률 계산용) + JSON 영속화
# ──────────────────────────────────────────────

import json
from pathlib import Path
from ict_trader.config import BASE_DIR

_TRADE_HISTORY_FILE = BASE_DIR / "trade_history.json"
_trade_results: list[dict] = []


def _load_trade_history() -> None:
    """시작 시 JSON에서 거래 이력 로드."""
    global _trade_results
    if _TRADE_HISTORY_FILE.exists():
        try:
            with open(_TRADE_HISTORY_FILE, "r") as f:
                _trade_results = json.load(f)
            logger.info("거래 이력 로드: %d건", len(_trade_results))
        except Exception as e:
            logger.warning("거래 이력 로드 실패: %s", e)
            _trade_results = []


def _save_trade_history() -> None:
    """거래 이력을 JSON에 저장."""
    try:
        with open(_TRADE_HISTORY_FILE, "w") as f:
            json.dump(_trade_results, f, indent=2)
    except Exception as e:
        logger.error("거래 이력 저장 실패: %s", e)


# 모듈 로드 시 자동으로 이력 읽기
_load_trade_history()


def record_trade_result(win: bool, rr_ratio: float, symbol: str = "") -> None:
    """거래 결과를 기록하고 JSON에 저장."""
    from datetime import datetime, timezone
    _trade_results.append({
        "win": win,
        "rr": rr_ratio,
        "symbol": symbol,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    _save_trade_history()
    total = len(_trade_results)
    wins = sum(1 for t in _trade_results if t["win"])
    logger.info("거래 기록: %s %s (누적 %d건, 승률 %.1f%%)",
                symbol, "승" if win else "패", total,
                wins / total * 100 if total else 0)


def get_trade_stats() -> tuple[int, float, float]:
    """누적 거래 통계를 반환한다. (표본수, 승률, 평균R:R)"""
    total = len(_trade_results)
    if total == 0:
        return 0, 0.0, 0.0
    wins = sum(1 for t in _trade_results if t["win"])
    avg_rr = sum(t["rr"] for t in _trade_results) / total
    return total, wins / total, avg_rr


# ──────────────────────────────────────────────
# 동적 레버리지 계산
# ──────────────────────────────────────────────

def calculate_optimal_leverage(
    kelly_fraction: float,
    entry_price: float,
    stop_loss: float,
) -> int:
    """
    Kelly 리스크와 SL 거리를 고려한 최적 레버리지를 계산한다.

    수학적 근거:
    - Kelly 리스크 달성 조건: margin × leverage × SL% = balance × kelly
    - 증거금 한도 내에서 Kelly 달성 최소 레버리지:
      L_min = kelly / (MaxExposure × SL%)
    - 여기에 안전 버퍼 1.2배 적용, 실용적 범위[3, 25]로 클램프

    Returns:
        최적 레버리지 (정수)
    """
    # 동적 레버리지: 청산 버퍼 확보 가능한 최대 레버리지
    # 청산가가 SL보다 LIQUIDATION_SAFETY_BUFFER배 이상 멀도록 함
    sl_distance = abs(entry_price - stop_loss) / entry_price
    if sl_distance <= 0:
        return LEVERAGE_MIN

    # 최대 안전 레버리지: 1/L > SL × safety_buffer
    # → L < 1 / (SL × safety_buffer)
    l_max_safe = 1.0 / (sl_distance * LIQUIDATION_SAFETY_BUFFER)
    l_final = int(max(LEVERAGE_MIN, min(LEVERAGE_MAX, l_max_safe)))

    logger.info(
        "동적 레버리지: SL=%.2f%%, 최대안전=%.1fx → L=%dx",
        sl_distance * 100, l_max_safe, l_final,
    )
    return l_final


# ──────────────────────────────────────────────
# 포지션 사이징 (고정 → Half-Kelly 자동 전환)
# ──────────────────────────────────────────────

def calculate_bet_fraction(rr_ratio: float) -> float:
    """
    ICT Fixed Fractional: 거래당 고정 리스크 비율 반환.
    R:R이나 승률과 무관하게 RISK_PER_TRADE 반환.

    Returns:
        자산 대비 리스크 비율 (예: 0.02 = 2%)
    """
    logger.info("사이징: Fixed Fractional %.1f%% (R:R=%.2f)", RISK_PER_TRADE * 100, rr_ratio)
    return RISK_PER_TRADE


def calculate_position_size(
    balance: float,
    risk_fraction: float,
    entry_price: float,
    stop_loss: float,
    current_exposure: float,
    leverage: int,
) -> tuple[float, float]:
    """
    ICT Fixed Fractional 포지션 사이징.
    포지션 사이즈는 오직 risk%와 SL거리로 결정 (레버리지는 margin만 영향).
    """
    sl_distance_pct = abs(entry_price - stop_loss) / entry_price
    if sl_distance_pct <= 0:
        return 0.0, 0.0

    risk_amount = balance * risk_fraction
    notional = risk_amount / sl_distance_pct
    margin = notional / leverage
    amount = notional / entry_price

    logger.info(
        "사이징: risk=%.1f%%(=$%.2f), SL=%.2f%%, L=%dx → margin=$%.2f, notional=$%.2f",
        risk_fraction * 100, risk_amount, sl_distance_pct * 100,
        leverage, margin, notional,
    )
    return margin, amount


# ──────────────────────────────────────────────
# 주문 실행
# ──────────────────────────────────────────────

async def execute_order(
    trigger: TriggerEvent,
    amount: float,
    market_info: dict,
    leverage: int = 10,
) -> dict | None:
    """
    Gate.io에 주문을 실행한다.
    PAPER_TRADING=True이면 실제 주문 없이 로그만 기록.

    Args:
        trigger: 트리거 이벤트
        amount: 정밀도 조정 전 수량
        market_info: tick_size 정보

    Returns:
        주문 결과 dict 또는 None (실패/페이퍼)
    """
    # Gate.io 선물: 코인 수량 → 계약 수 변환
    contract_size = market_info.get("contract_size", 1.0)
    if contract_size and contract_size > 0:
        contracts = amount / contract_size
        contracts = max(1, int(contracts))  # 최소 1계약, 내림
        logger.info(
            "%s: 수량 %.6f → %d계약 (계약크기=%.6f)",
            trigger.symbol, amount, contracts, contract_size,
        )
    else:
        contracts = amount

    # 정밀도 조정
    adjusted = adjust_order_precision(
        trigger.direction,
        trigger.entry_price,
        trigger.stop_loss,
        trigger.take_profit,
        contracts,
        market_info,
    )

    if adjusted["amount"] <= 0:
        logger.warning("%s: 정밀도 조정 후 수량 0, 주문 취소", trigger.symbol)
        return None

    side = "buy" if trigger.direction == "bullish" else "sell"

    order_info = {
        "symbol": trigger.symbol,
        "side": side,
        "amount": adjusted["amount"],
        "entry_price": adjusted["entry_price"],
        "stop_loss": adjusted["stop_loss"],
        "take_profit": adjusted["take_profit"],
        "leverage": leverage,
        "paper": PAPER_TRADING,
    }

    if PAPER_TRADING:
        logger.info(
            "[PAPER] 주문: %s %s %.6f @ $%.2f | SL=$%.2f TP=$%.2f",
            side.upper(), trigger.symbol, adjusted["amount"],
            adjusted["entry_price"], adjusted["stop_loss"], adjusted["take_profit"],
        )
        order_info["order_id"] = f"paper_{trigger.symbol}_{trigger.timestamp.isoformat()}"
        order_info["status"] = "paper_filled"
        return order_info

    # 실제 주문
    try:
        exchange = await get_exchange()

        # Gate.io 선물 심볼 변환: BTC/USDT → BTC/USDT:USDT
        futures_symbol = trigger.symbol
        if ":USDT" not in futures_symbol:
            futures_symbol = f"{futures_symbol}:USDT"

        # 레버리지 설정
        try:
            await exchange.set_leverage(leverage, futures_symbol)
        except Exception as e:
            logger.warning("레버리지 설정 실패 (기존값 사용): %s", e)

        # 시장가 주문 (Gate.io는 market buy에 price 필요)
        order = await exchange.create_order(
            symbol=futures_symbol,
            type="market",
            side=side,
            amount=adjusted["amount"],
            price=adjusted["entry_price"],
        )

        order_id = order.get("id", "")
        fill_price = order.get("average") or order.get("price") or adjusted["entry_price"]
        order_info["order_id"] = order_id
        order_info["fill_price"] = fill_price
        order_info["status"] = "filled"

        logger.info(
            "[LIVE] 시장가 체결: %s %s %.6f @ $%.2f (id=%s)",
            side.upper(), trigger.symbol, adjusted["amount"],
            fill_price, order_id,
        )

        # SL 주문 (스탑로스)
        sl_side = "sell" if side == "buy" else "buy"
        try:
            sl_order = await exchange.create_order(
                symbol=futures_symbol,
                type="stop",
                side=sl_side,
                amount=adjusted["amount"],
                price=adjusted["stop_loss"],
                params={
                    "stopPrice": adjusted["stop_loss"],
                    "reduceOnly": True,
                },
            )
            order_info["sl_order_id"] = sl_order.get("id", "")
            logger.info("SL 주문 설정: $%.2f (id=%s)", adjusted["stop_loss"], sl_order.get("id"))
        except Exception as e:
            logger.error("SL 주문 실패: %s — %s", trigger.symbol, e)
            order_info["sl_error"] = str(e)

        # TP 주문 (익절)
        try:
            tp_order = await exchange.create_order(
                symbol=futures_symbol,
                type="limit",
                side=sl_side,
                amount=adjusted["amount"],
                price=adjusted["take_profit"],
                params={"reduceOnly": True},
            )
            order_info["tp_order_id"] = tp_order.get("id", "")
            logger.info("TP 주문 설정: $%.2f (id=%s)", adjusted["take_profit"], tp_order.get("id"))
        except Exception as e:
            logger.error("TP 주문 실패: %s — %s", trigger.symbol, e)
            order_info["tp_error"] = str(e)

        return order_info

    except Exception as e:
        logger.error("주문 실행 실패: %s — %s", trigger.symbol, e)
        return None


async def get_balance() -> float:
    """Gate.io USDT 잔액을 조회한다."""
    if PAPER_TRADING:
        logger.debug("[PAPER] 잔액 조회: $10,000 (모의)")
        return 10000.0

    try:
        exchange = await get_exchange()
        balance = await exchange.fetch_balance()
        usdt = balance.get("USDT", {})
        free = float(usdt.get("free", 0))
        logger.debug("잔액 조회: $%.2f USDT", free)
        return free
    except Exception as e:
        logger.error("잔액 조회 실패: %s", e)
        return 0.0


async def cancel_orders(symbol: str) -> bool:
    """심볼의 미체결 주문을 전부 취소한다."""
    if PAPER_TRADING:
        logger.info("[PAPER] 미체결 주문 취소: %s", symbol)
        return True

    try:
        exchange = await get_exchange()
        fs = f"{symbol}:USDT" if ":USDT" not in symbol else symbol
        open_orders = await exchange.fetch_open_orders(fs)
        for order in open_orders:
            await exchange.cancel_order(order["id"], fs)
            logger.info("주문 취소: %s (id=%s)", symbol, order["id"])
        return True
    except Exception as e:
        logger.error("주문 취소 실패: %s — %s", symbol, e)
        return False
