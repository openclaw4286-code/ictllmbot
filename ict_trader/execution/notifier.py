"""
Telegram 알림.
트레이딩 신호 + 차트 이미지를 Telegram으로 전송.
"""

from __future__ import annotations

import logging
import asyncio

import aiohttp

from ict_trader.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from ict_trader.algorithm.trigger import TriggerEvent
from ict_trader.llm.analyzer import LLMVerdict

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}"


def _is_configured() -> bool:
    """Telegram 설정이 완료되었는지 확인."""
    return bool(TELEGRAM_BOT_TOKEN) and bool(TELEGRAM_CHAT_ID)


def _build_signal_message(
    trigger: TriggerEvent,
    verdict: LLMVerdict,
    kelly_pct: float,
    position_amount: float,
) -> str:
    """
    Telegram 알림 메시지를 포맷팅한다.

    포맷:
    🟢 LONG — BTC/USDT  |  A (83점)
    진입 $94,200  SL $93,100  TP $97,400
    R:R 1:2.9  |  뉴욕 오픈  |  FVG_CE
    켈리 베팅: 8.4%  |  포지션: 0.063 BTC
    [LLM 검토 의견 3줄]
    뉴스: 긍정적  |  경제지표 리스크: 낮음
    """
    direction_emoji = "🟢" if trigger.direction == "bullish" else "🔴"
    direction_text = "LONG" if trigger.direction == "bullish" else "SHORT"
    coin = trigger.symbol.split("/")[0]

    # 뉴스 영향 한글
    news_kr = {
        "POSITIVE": "긍정적",
        "NEGATIVE": "부정적",
        "NEUTRAL": "중립",
    }.get(verdict.news_impact, verdict.news_impact)

    # 경제지표 리스크 한글
    econ_kr = "높음" if verdict.econ_risk == "HIGH" else "낮음"

    # TradingView 링크 (참고용, Gate.io 심볼로)
    tv_symbol = f"GATEIO:{coin}USDT"
    tv_link = f"https://www.tradingview.com/chart/?symbol={tv_symbol}"

    lines = [
        f"{direction_emoji} {direction_text} — {trigger.symbol}  |  {trigger.grade} ({trigger.setup_score}점)",
        f"진입 ${trigger.entry_price:,.2f}  SL ${trigger.stop_loss:,.2f}  TP ${trigger.take_profit:,.2f}",
        f"R:R 1:{trigger.rr_ratio:.1f}  |  {trigger.session} 오픈  |  {trigger.entry_type.upper()}",
        f"켈리 베팅: {kelly_pct:.1f}%  |  포지션: {position_amount:.6f} {coin}",
        "",
        f"[LLM 검토]",
        verdict.reasoning,
        "",
        f"뉴스: {news_kr}  |  경제지표 리스크: {econ_kr}",
        f"🔗 {tv_link}",
    ]

    return "\n".join(lines)


def _build_status_message(text: str) -> str:
    """일반 상태 알림 메시지."""
    return f"📊 ICT Trader\n{text}"


async def send_message(text: str) -> bool:
    """텍스트 메시지를 전송한다."""
    if not _is_configured():
        logger.warning("Telegram 미설정, 메시지 전송 스킵")
        return False

    url = f"{TELEGRAM_API.format(token=TELEGRAM_BOT_TOKEN)}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    logger.debug("Telegram 메시지 전송 성공")
                    return True
                else:
                    body = await resp.text()
                    logger.error("Telegram 전송 실패 (%d): %s", resp.status, body[:200])
                    return False
    except Exception as e:
        logger.error("Telegram 전송 예외: %s", e)
        return False


async def send_photo(image_bytes: bytes, caption: str = "") -> bool:
    """차트 이미지를 전송한다."""
    if not _is_configured():
        logger.warning("Telegram 미설정, 이미지 전송 스킵")
        return False

    url = f"{TELEGRAM_API.format(token=TELEGRAM_BOT_TOKEN)}/sendPhoto"

    data = aiohttp.FormData()
    data.add_field("chat_id", TELEGRAM_CHAT_ID)
    data.add_field("photo", image_bytes, filename="chart.png", content_type="image/png")
    if caption:
        data.add_field("caption", caption[:1024])  # Telegram caption 한도
        data.add_field("parse_mode", "HTML")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    logger.debug("Telegram 이미지 전송 성공")
                    return True
                else:
                    body = await resp.text()
                    logger.error("Telegram 이미지 전송 실패 (%d): %s", resp.status, body[:200])
                    return False
    except Exception as e:
        logger.error("Telegram 이미지 전송 예외: %s", e)
        return False


async def notify_signal(
    trigger: TriggerEvent,
    verdict: LLMVerdict,
    kelly_pct: float,
    position_amount: float,
    chart_bytes: bytes | None = None,
) -> bool:
    """
    트레이딩 신호를 Telegram으로 알린다.
    텍스트 + 차트 이미지.

    Args:
        trigger: 알고리즘 트리거 이벤트
        verdict: LLM 판단 결과
        kelly_pct: 켈리 베팅 비율 (%)
        position_amount: 포지션 수량
        chart_bytes: 차트 PNG bytes (None이면 텍스트만)

    Returns:
        전송 성공 여부
    """
    message = _build_signal_message(trigger, verdict, kelly_pct, position_amount)

    if chart_bytes:
        # 이미지와 함께 전송 (caption으로)
        return await send_photo(chart_bytes, caption=message)
    else:
        return await send_message(message)


async def notify_status(text: str) -> bool:
    """일반 상태 알림."""
    return await send_message(_build_status_message(text))


async def notify_error(error_text: str) -> bool:
    """에러 알림."""
    return await send_message(f"⚠️ ICT Trader 오류\n{error_text}")


async def notify_position_closed(
    symbol: str,
    direction: str,
    reason: str,
    pnl: float | None = None,
) -> bool:
    """포지션 종료 알림."""
    dir_text = "LONG" if direction == "bullish" else "SHORT"
    pnl_text = f" | PnL: ${pnl:+,.2f}" if pnl is not None else ""
    return await send_message(
        f"📍 포지션 종료: {symbol} {dir_text}\n사유: {reason}{pnl_text}"
    )
