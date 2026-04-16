"""
ICT 자동 트레이딩 시스템 — 메인 루프.

세션 체크 → 경제지표 잠금 → 유니버스 순회 → 스킵 조건 →
멀티TF 데이터 수집 → 알고리즘 탑다운 분석 → TriggerEvent 생성 →
상위 3개 LLM 검토 (비동기) → PASS 시 리스크 사이징 → 주문 실행 →
Telegram 알림 → 포지션 등록 → 포지션 동기화 → 루프 통계 로깅
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone

from ict_trader.config import (
    LOOP_INTERVAL_SECONDS,
    TOP_SIGNALS_FOR_LLM,
    PAPER_TRADING,
    LOG_DIR,
    prompt_api_keys,
)
from ict_trader.data.universe import fetch_top_coins
from ict_trader.data.fetcher import fetch_multi_timeframe, get_tick_size, close_exchange
from ict_trader.data.news import fetch_news
from ict_trader.data.economic_calendar import (
    is_economic_lock_active,
    get_upcoming_events,
)
from ict_trader.algorithm.session import is_session_active, get_current_session, get_session_display_name
from ict_trader.algorithm.trigger import analyze_topdown, TriggerEvent
from ict_trader.llm.analyzer import analyze_signal, LLMVerdict
from ict_trader.execution.gate_executor import (
    calculate_bet_fraction,
    calculate_position_size,
    calculate_optimal_leverage,
    execute_order,
    get_balance,
    get_free_balance,
)
from ict_trader.execution.position_manager import PositionManager
from ict_trader.execution.notifier import (
    notify_signal,
    notify_status,
    notify_error,
    notify_position_closed,
)
from ict_trader.state.loop_state import LoopState

# ──────────────────────────────────────────────
# 로깅 설정
# ──────────────────────────────────────────────

def setup_logging() -> None:
    """로깅을 설정한다."""
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    log_file = LOG_DIR / f"ict_trader_{datetime.now().strftime('%Y%m%d')}.log"

    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(str(log_file), encoding="utf-8"),
        ],
    )
    # 외부 라이브러리 로그 레벨 조정
    logging.getLogger("ccxt").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 종료 핸들러
# ──────────────────────────────────────────────

_shutdown = False
_execution_lock = asyncio.Lock()  # 주문 실행 직렬화용


def _signal_handler(sig, frame) -> None:
    global _shutdown
    logger.info("종료 신호 수신 (%s), 루프 종료 중...", signal.Signals(sig).name)
    _shutdown = True


# ──────────────────────────────────────────────
# LLM 검토 + 실행 (비동기 태스크)
# ──────────────────────────────────────────────

async def _process_trigger(
    trigger: TriggerEvent,
    news_list: list[dict],
    econ_events: list[dict],
    position_manager: PositionManager,
    loop_state: LoopState,
) -> None:
    """
    단일 트리거에 대해 LLM 검토 → 주문 실행 → 알림.
    백그라운드 비동기 태스크로 실행. 루프를 블로킹하지 않음.
    """
    symbol = trigger.symbol

    try:
        loop_state.mark_llm_start(symbol)

        # LLM 검토
        verdict = await analyze_signal(trigger, news_list, econ_events)
        loop_state.mark_llm_done(symbol, verdict.verdict, verdict.wait_minutes)

        logger.info(
            "LLM 결과: %s %s → %s | %s",
            symbol, trigger.direction, verdict.verdict, verdict.reasoning[:80],
        )

        if verdict.verdict == "PASS":
            await _execute_pass(
                trigger, verdict,
                position_manager, loop_state,
            )
        else:
            logger.info(
                "%s WAIT (%d분 대기) — %s",
                symbol, verdict.wait_minutes, verdict.reasoning[:100],
            )

    except Exception as e:
        logger.error("트리거 처리 예외: %s — %s", symbol, e, exc_info=True)
        loop_state.mark_llm_done(symbol, "WAIT", 10)


async def _emergency_close(trigger: TriggerEvent, order_result: dict) -> None:
    """
    SL/TP 설정 실패 시 즉시 시장가 청산.
    체결됐지만 보호 주문이 안 걸린 포지션을 그대로 두면 위험.
    """
    from ict_trader.data.fetcher import get_exchange

    futures_symbol = trigger.symbol
    if ":USDT" not in futures_symbol:
        futures_symbol = f"{futures_symbol}:USDT"

    close_side = "sell" if trigger.direction == "bullish" else "buy"
    amount = order_result.get("amount", 0)

    try:
        exchange = await get_exchange()
        close_order = await exchange.create_order(
            symbol=futures_symbol,
            type="market",
            side=close_side,
            amount=amount,
            price=trigger.entry_price,
            params={"reduceOnly": True},
        )
        logger.info(
            "긴급 청산 완료: %s %s %.6f (id=%s)",
            trigger.symbol, close_side.upper(), amount, close_order.get("id"),
        )
    except Exception as e:
        logger.critical(
            "긴급 청산 실패!!! %s: %s — 수동 개입 필요",
            trigger.symbol, e,
        )

    # 미체결 주문(한쪽만 걸렸을 경우) 취소
    try:
        exchange = await get_exchange()
        open_orders = await exchange.fetch_open_orders(futures_symbol)
        for o in open_orders:
            try:
                await exchange.cancel_order(o["id"], futures_symbol)
                logger.info("미체결 주문 취소: %s", o["id"])
            except Exception as e:
                logger.warning("주문 취소 실패: %s — %s", o["id"], e)
    except Exception as e:
        logger.error("미체결 주문 조회 실패: %s", e)


async def _execute_pass(
    trigger: TriggerEvent,
    verdict: LLMVerdict,
    position_manager: PositionManager,
    loop_state: LoopState,
) -> None:
    """PASS 판정 → 리스크 사이징 → 주문 실행 → 알림.
    Lock으로 직렬화: 동시에 여러 PASS가 와도 증거금 한도 초과 방지.
    """
    async with _execution_lock:
        await _execute_pass_locked(
            trigger, verdict, position_manager, loop_state
        )


async def _execute_pass_locked(
    trigger: TriggerEvent,
    verdict: LLMVerdict,
    position_manager: PositionManager,
    loop_state: LoopState,
) -> None:
    """Lock 내부 실행 로직."""
    symbol = trigger.symbol

    # 리스크 비율 계산
    bet_f = calculate_bet_fraction(trigger.rr_ratio)
    if bet_f <= 0:
        return

    balance = await get_balance()
    position_manager.set_balance(balance)
    current_usage = position_manager.get_margin_usage()

    # 동적 레버리지 계산
    leverage = calculate_optimal_leverage(bet_f, trigger.entry_price, trigger.stop_loss)

    margin, amount = calculate_position_size(
        balance, bet_f, trigger.entry_price, trigger.stop_loss, current_usage, leverage,
    )
    if amount <= 0:
        return

    # 담보 잔여량 체크 (free 기준, Gate.io가 주문 거부 방지)
    free = await get_free_balance()
    if margin > free:
        logger.info(
            "%s: 담보 부족 (필요 $%.2f > 사용가능 $%.2f / 전재산 $%.2f), 진입 안 함",
            symbol, margin, free, balance,
        )
        return

    # tick_size 정보 조회 & 주문 실행
    try:
        market_info = await get_tick_size(symbol)
    except Exception as e:
        logger.error("%s: tick_size 조회 실패 — %s", symbol, e)
        return

    order_result = await execute_order(trigger, amount, market_info, leverage)
    if order_result is None:
        logger.error("%s: 주문 실행 실패", symbol)
        return

    # SL/TP 미설정 시 즉시 긴급 청산 (안전장치)
    sl_ok = bool(order_result.get("sl_order_id")) and "sl_error" not in order_result
    tp_ok = bool(order_result.get("tp_order_id")) and "tp_error" not in order_result
    if not sl_ok or not tp_ok:
        logger.error(
            "%s: SL/TP 미설정 (sl=%s, tp=%s) → 긴급 청산",
            symbol, sl_ok, tp_ok,
        )
        await _emergency_close(trigger, order_result)
        return

    # 포지션 등록
    position_manager.open_position(
        symbol=symbol,
        direction=trigger.direction,
        entry_price=trigger.entry_price,
        amount=order_result.get("amount", amount),
        margin=margin,
        stop_loss=trigger.stop_loss,
        take_profit=trigger.take_profit,
        order_id=order_result.get("order_id", ""),
        sl_order_id=order_result.get("sl_order_id", ""),
        tp_order_id=order_result.get("tp_order_id", ""),
        rr_ratio=trigger.rr_ratio,
        entry_type=trigger.entry_type,
        session=trigger.session,
    )
    loop_state.mark_filled(symbol)

    # Telegram 알림
    risk_pct = bet_f * 100
    await notify_signal(
        trigger, verdict, risk_pct,
        order_result.get("amount", amount),
    )

    logger.info(
        "체결 완료: %s %s %.6f @ $%.2f | 리스크 %.1f%% | 증거금 $%.2f",
        symbol, trigger.direction, order_result.get("amount", amount),
        trigger.entry_price, risk_pct, margin
    )


# ──────────────────────────────────────────────
# 메인 루프
# ──────────────────────────────────────────────

async def main_loop() -> None:
    """메인 트레이딩 루프."""
    position_manager = PositionManager()
    loop_state = LoopState()

    mode = "PAPER" if PAPER_TRADING else "LIVE"
    logger.info("=== ICT Trader 시작 (%s 모드) ===", mode)
    await notify_status(f"시스템 시작 ({mode} 모드)")

    while not _shutdown:
        try:
            await _run_one_cycle(position_manager, loop_state)
        except Exception as e:
            logger.error("루프 예외: %s", e, exc_info=True)
            await notify_error(f"루프 예외: {e}")

        # 루프 간격 대기 (1초 단위로 분할하여 종료 신호 빠르게 감지)
        for _ in range(LOOP_INTERVAL_SECONDS):
            if _shutdown:
                break
            await asyncio.sleep(1)

    # 종료 처리
    logger.info("시스템 종료 중...")
    logger.info(loop_state.summary())
    await notify_status(f"시스템 종료\n{loop_state.summary()}")
    await close_exchange()
    logger.info("=== ICT Trader 종료 ===")


async def _run_one_cycle(
    position_manager: PositionManager,
    loop_state: LoopState,
) -> None:
    """루프 1회 실행."""

    # 1. 세션 체크
    session = get_current_session()
    if not is_session_active():
        logger.debug("세션 외 시간, 스킵 (%s)", get_session_display_name(session))
        return

    # 2. 경제지표 잠금 체크
    if is_economic_lock_active():
        logger.info("경제지표 잠금 활성, 스킵")
        return

    # 3. 포지션 동기화
    await position_manager.sync_from_exchange()

    # 4. 잔액 갱신
    balance = await get_balance()
    position_manager.set_balance(balance)

    # 5. 유니버스 조회
    symbols = fetch_top_coins()
    if not symbols:
        logger.warning("유니버스 비어있음, 스킵")
        return

    # 6. 경제지표 이벤트 (LLM 전달용)
    econ_events = get_upcoming_events(hours_ahead=4)

    # 7. 심볼 순회 → 알고리즘 분석
    triggers: list[tuple[TriggerEvent, str | None, bytes | None, list[dict]]] = []

    for symbol in symbols:
        # 스킵 조건
        skip, reason = loop_state.should_skip(symbol, position_manager.has_position(symbol))
        if skip:
            logger.debug("%s 스킵: %s", symbol, reason)
            continue

        # 멀티TF 데이터 수집
        try:
            frames = await fetch_multi_timeframe(symbol)
        except Exception as e:
            logger.error("%s OHLCV 수집 실패: %s", symbol, e)
            continue

        htf_df = frames.get("4h")
        mtf_df = frames.get("15m")
        ltf_df = frames.get("5m")

        if htf_df is None or mtf_df is None or ltf_df is None:
            continue
        if htf_df.empty or mtf_df.empty or ltf_df.empty:
            continue

        # 탑다운 분석
        trigger = analyze_topdown(symbol, htf_df, mtf_df, ltf_df, session)
        if trigger is None:
            continue

        loop_state.mark_signal_generated(symbol)

        # 뉴스 수집
        news_list = fetch_news(symbol)

        triggers.append((trigger, news_list))

        logger.info(
            "신호 감지: %s %s %s | 점수=%d(%s) R:R=1:%.1f",
            symbol, trigger.direction, trigger.entry_type,
            trigger.rr_ratio, trigger.entry_type, len(trigger.confluences),
        )

    # 8. 상위 N개만 LLM 검토 (점수 기준 정렬)
    triggers.sort(key=lambda t: t[0].rr_ratio, reverse=True)
    top_triggers = triggers[:TOP_SIGNALS_FOR_LLM]

    if top_triggers:
        logger.info(
            "LLM 검토 대상: %d개 / %d개 신호",
            len(top_triggers), len(triggers),
        )

    # 9. 비동기 LLM 검토 + 실행 (루프 블로킹 없음)
    tasks = []
    for trigger, news_list in top_triggers:
        task = asyncio.create_task(
            _process_trigger(
                trigger, news_list, econ_events,
                position_manager, loop_state,
            )
        )
        tasks.append(task)

    # 태스크들이 백그라운드에서 실행됨 — 다음 루프에서 완료 확인
    if tasks:
        # 현재 루프 내에서 완료 대기 (다음 루프 전에 결과 확보)
        await asyncio.gather(*tasks, return_exceptions=True)

    # 10. 루프 통계
    loop_state.mark_loop_complete(symbols_scanned=len(symbols))

    # 10루프마다 요약 로깅
    if loop_state.stats.total_loops % 10 == 0:
        logger.info(loop_state.summary())
        logger.info(position_manager.summary())


# ──────────────────────────────────────────────
# 엔트리포인트
# ──────────────────────────────────────────────

def main() -> None:
    """메인 엔트리포인트."""
    setup_logging()

    # API 키가 없으면 터미널에서 입력받기
    prompt_api_keys()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        logger.info("키보드 인터럽트로 종료")
    except Exception as e:
        logger.critical("치명적 오류: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
