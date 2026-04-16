"""
알림 — 콘솔/로그 출력.
Telegram 대신 터미널 + 로그 파일에 알림.
"""

from __future__ import annotations

import logging

from ict_trader.algorithm.trigger import TriggerEvent
from ict_trader.llm.analyzer import LLMVerdict

logger = logging.getLogger(__name__)


def _build_signal_message(
    trigger: TriggerEvent,
    verdict: LLMVerdict,
    risk_pct: float,
    position_amount: float,
) -> str:
    """트레이딩 신호 메시지 포맷팅."""
    direction_emoji = "\u001b[32m" if trigger.direction == "bullish" else "\u001b[31m"
    reset = "\u001b[0m"
    direction_text = "LONG" if trigger.direction == "bullish" else "SHORT"
    coin = trigger.symbol.split("/")[0]

    news_kr = {
        "POSITIVE": "긍정적",
        "NEGATIVE": "부정적",
        "NEUTRAL": "중립",
    }.get(verdict.news_impact, verdict.news_impact)

    econ_kr = "높음" if verdict.econ_risk == "HIGH" else "낮음"

    lines = [
        f"",
        f"{'='*60}",
        f"{direction_emoji}  {direction_text} — {trigger.symbol}  |  R:R 1:{trigger.rr_ratio:.1f}  |  {len(trigger.confluences)} confluences{reset}",
        f"  진입 ${trigger.entry_price:,.2f}  SL ${trigger.stop_loss:,.2f}  TP ${trigger.take_profit:,.2f}",
        f"  R:R 1:{trigger.rr_ratio:.1f}  |  {trigger.session} 오픈  |  {trigger.entry_type.upper()}",
        f"  리스크: {risk_pct:.1f}%  |  포지션: {position_amount:.6f} {coin}",
        f"",
        f"  [LLM 검토] {verdict.reasoning}",
        f"  뉴스: {news_kr}  |  경제지표 리스크: {econ_kr}",
        f"{'='*60}",
    ]

    return "\n".join(lines)


async def send_message(text: str) -> bool:
    """콘솔에 메시지를 출력한다."""
    logger.info(text)
    print(text)
    return True


async def notify_signal(
    trigger: TriggerEvent,
    verdict: LLMVerdict,
    risk_pct: float,
    position_amount: float,
) -> bool:
    """트레이딩 신호 알림."""
    message = _build_signal_message(trigger, verdict, risk_pct, position_amount)
    print(message)
    logger.info(message)
    return True


async def notify_status(text: str) -> bool:
    """상태 알림."""
    msg = f"[STATUS] {text}"
    print(msg)
    logger.info(msg)
    return True


async def notify_error(error_text: str) -> bool:
    """에러 알림."""
    msg = f"[ERROR] {error_text}"
    print(msg)
    logger.error(msg)
    return True


async def notify_position_closed(
    symbol: str,
    direction: str,
    reason: str,
    pnl: float | None = None,
) -> bool:
    """포지션 종료 알림."""
    dir_text = "LONG" if direction == "bullish" else "SHORT"
    pnl_text = f" | PnL: ${pnl:+,.2f}" if pnl is not None else ""
    msg = f"[CLOSED] {symbol} {dir_text} — {reason}{pnl_text}"
    print(msg)
    logger.info(msg)
    return True
