"""
포지션 추적 및 증거금 사용량 관리.
실시간 포지션 상태를 메모리에서 관리하고 거래소 동기화.
"""

from __future__ import annotations

import logging
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ict_trader.config import PAPER_TRADING
from ict_trader.data.fetcher import get_exchange

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """활성 포지션."""
    symbol: str
    direction: str              # "bullish" or "bearish"
    entry_price: float
    amount: float
    margin: float               # 투입 증거금
    stop_loss: float
    take_profit: float
    order_id: str
    sl_order_id: str = ""
    tp_order_id: str = ""
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    rr_ratio: float = 0.0
    entry_type: str = ""
    session: str = ""


class PositionManager:
    """포지션 매니저. 메인 루프에서 싱글턴으로 사용."""

    def __init__(self) -> None:
        self._positions: dict[str, Position] = {}  # symbol → Position
        self._total_balance: float = 0.0

    @property
    def active_symbols(self) -> set[str]:
        """현재 포지션 보유 중인 심볼 집합."""
        return set(self._positions.keys())

    @property
    def position_count(self) -> int:
        return len(self._positions)

    def has_position(self, symbol: str) -> bool:
        return symbol in self._positions

    def get_position(self, symbol: str) -> Position | None:
        return self._positions.get(symbol)

    def get_all_positions(self) -> list[Position]:
        return list(self._positions.values())

    def get_margin_usage(self) -> float:
        """
        현재 증거금 사용 비율을 계산한다.

        Returns:
            0.0 ~ 1.0
        """
        if self._total_balance <= 0:
            return 0.0
        total_margin = sum(p.margin for p in self._positions.values())
        return total_margin / self._total_balance

    def get_remaining_margin_ratio(self) -> float:
        """잔여 사용 가능 증거금 비율 (1.0 = 전체 사용 가능)."""
        return max(0.0, 1.0 - self.get_margin_usage())

    def set_balance(self, balance: float) -> None:
        """총 잔액을 설정한다."""
        self._total_balance = balance

    def open_position(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        amount: float,
        margin: float,
        stop_loss: float,
        take_profit: float,
        order_id: str,
        sl_order_id: str = "",
        tp_order_id: str = "",
        rr_ratio: float = 0.0,
        entry_type: str = "",
        session: str = "",
    ) -> Position:
        """포지션을 등록한다."""
        pos = Position(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            amount=amount,
            margin=margin,
            stop_loss=stop_loss,
            take_profit=take_profit,
            order_id=order_id,
            sl_order_id=sl_order_id,
            tp_order_id=tp_order_id,
            rr_ratio=rr_ratio,
            entry_type=entry_type,
            session=session,
        )
        self._positions[symbol] = pos
        logger.info(
            "포지션 오픈: %s %s %.6f @ $%.2f | 증거금 $%.2f (사용률 %.1f%%)",
            symbol, direction, amount, entry_price,
            margin, self.get_margin_usage() * 100,
        )
        return pos

    def close_position(self, symbol: str, reason: str = "") -> Position | None:
        """포지션을 종료 처리한다."""
        pos = self._positions.pop(symbol, None)
        if pos:
            logger.info(
                "포지션 종료: %s %s (사유: %s) | 잔여 증거금률 %.1f%%",
                symbol, pos.direction, reason or "수동",
                self.get_margin_usage() * 100,
            )
        return pos

    async def sync_from_exchange(self) -> None:
        """
        거래소에서 실제 포지션을 조회하여 동기화한다.
        포지션이 청산(SL/TP 체결)되었으면 로컬에서도 제거.
        """
        if PAPER_TRADING:
            logger.debug("[PAPER] 거래소 동기화 스킵")
            return

        try:
            exchange = await get_exchange()
            exchange_positions = await exchange.fetch_positions()

            # 거래소에 실제로 열려있는 심볼
            exchange_symbols: set[str] = set()
            for ep in exchange_positions:
                contracts = float(ep.get("contracts", 0))
                if contracts > 0:
                    sym = ep.get("symbol", "")
                    exchange_symbols.add(sym)

            # 로컬에는 있지만 거래소에 없는 포지션 = 청산됨
            closed: list[str] = []
            for symbol in list(self._positions.keys()):
                if symbol not in exchange_symbols:
                    closed.append(symbol)

            for symbol in closed:
                pos = self._positions.get(symbol)
                if pos:
                    # SL/TP 주문 상태 확인 → 승/패 판정
                    win = await self._determine_win_loss(exchange, pos)
                    reason = "TP 체결 (승)" if win else "SL 체결 (패)"

                    # Kelly 학습용으로 기록
                    from ict_trader.execution.gate_executor import record_trade_result
                    record_trade_result(win, pos.rr_ratio, symbol=symbol)

                self.close_position(symbol, reason=reason if pos else "종료")

            if closed:
                logger.info("동기화로 종료된 포지션: %s", closed)

        except Exception as e:
            logger.error("포지션 동기화 실패: %s", e)

    async def _determine_win_loss(self, exchange, pos: Position) -> bool:
        """
        포지션 종료 원인을 SL/TP 주문 상태로 판정한다.
        TP 주문이 filled/closed 상태면 승, SL 주문이 filled면 패.
        조회 실패 시 최근가 비교로 폴백.
        """
        futures_symbol = f"{pos.symbol}:USDT" if ":USDT" not in pos.symbol else pos.symbol

        # TP 주문 상태 확인 먼저
        if pos.tp_order_id:
            try:
                tp_order = await exchange.fetch_order(pos.tp_order_id, futures_symbol)
                status = tp_order.get("status", "")
                if status in ("closed", "filled"):
                    return True  # TP 체결 = 승
            except Exception as e:
                logger.debug("TP 주문 상태 조회 실패: %s", e)

        # SL 주문 상태 확인
        if pos.sl_order_id:
            try:
                sl_order = await exchange.fetch_order(pos.sl_order_id, futures_symbol)
                status = sl_order.get("status", "")
                if status in ("closed", "filled"):
                    return False  # SL 체결 = 패
            except Exception as e:
                logger.debug("SL 주문 상태 조회 실패: %s", e)

        # 폴백: 현재가 vs 진입가 방향 비교
        try:
            ticker = await exchange.fetch_ticker(futures_symbol)
            current = float(ticker.get("last", 0))
            if pos.direction == "bullish":
                return current > pos.entry_price
            else:
                return current < pos.entry_price
        except Exception as e:
            logger.warning("현재가 조회 실패, 패로 처리: %s", e)
            return False

    def summary(self) -> str:
        """현재 포지션 요약 문자열."""
        if not self._positions:
            return "활성 포지션 없음"

        lines = [f"활성 포지션 {self.position_count}개 (증거금 사용률 {self.get_margin_usage()*100:.1f}%):"]
        for sym, pos in self._positions.items():
            direction_kr = "LONG" if pos.direction == "bullish" else "SHORT"
            lines.append(
                f"  {sym} {direction_kr} {pos.amount:.6f} @ ${pos.entry_price:,.2f} "
                f"| SL=${pos.stop_loss:,.2f} TP=${pos.take_profit:,.2f}"
            )
        return "\n".join(lines)
