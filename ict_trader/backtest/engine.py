"""
백테스트 엔진.
실거래와 동일한 알고리즘 코드를 사용하되 LLM 호출 없이 알고리즘 레이어만 테스트.
OHLCV 데이터에서 TP/SL 터치 순서로 승패 판정.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd
import numpy as np

from ict_trader.algorithm.trigger import analyze_topdown, TriggerEvent
from ict_trader.algorithm.session import get_current_session, get_session_display_name
from ict_trader.config import HTF, MTF, LTF, SESSIONS

logger = logging.getLogger(__name__)


@dataclass
class TradeResult:
    """단일 거래 결과."""
    symbol: str
    timestamp: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    rr_ratio: float
    setup_score: int
    grade: str
    confluences: str        # 컨플루언스 요약
    result: str             # "win" / "loss" / "invalid"
    session: str
    entry_type: str
    exit_price: float = 0.0
    exit_timestamp: str = ""
    bars_to_exit: int = 0


@dataclass
class BacktestRun:
    """백테스트 1회 실행 결과."""
    param_set: str
    score_set: str
    symbol: str
    trades: list[TradeResult] = field(default_factory=list)
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    invalid: int = 0


def _timeframe_to_minutes(tf: str) -> int:
    """타임프레임 문자열을 분 단위로 변환."""
    if tf.endswith("m"):
        return int(tf[:-1])
    elif tf.endswith("h"):
        return int(tf[:-1]) * 60
    elif tf.endswith("d"):
        return int(tf[:-1]) * 1440
    return 60


def _is_in_session(ts: pd.Timestamp) -> tuple[bool, str | None]:
    """타임스탬프가 세션 시간 내인지, 주말이 아닌지 확인."""
    if ts.weekday() >= 5:
        return False, None
    hour = ts.hour
    for name, times in SESSIONS.items():
        if times["start"] <= hour < times["end"]:
            return True, name
    return False, None


def simulate_trade(
    trigger: TriggerEvent,
    ltf_df: pd.DataFrame,
    trigger_bar_idx: int,
) -> TradeResult:
    """
    트리거 이후 LTF 봉을 순회하며 TP/SL 터치를 판정한다.

    - TP 먼저 터치 → 승 (win)
    - SL 먼저 터치 → 패 (loss)
    - 데이터 끝까지 미터치 → 무효 (invalid)

    같은 봉에서 둘 다 터치되면 보수적으로 SL(loss) 처리.
    """
    confluences_str = ", ".join(
        f"{c.name}(+{c.score})" for c in trigger.confluences
    )

    result = TradeResult(
        symbol=trigger.symbol,
        timestamp=str(trigger.timestamp),
        direction=trigger.direction,
        entry_price=trigger.entry_price,
        stop_loss=trigger.stop_loss,
        take_profit=trigger.take_profit,
        rr_ratio=trigger.rr_ratio,
        setup_score=trigger.setup_score,
        grade=trigger.grade,
        confluences=confluences_str,
        result="invalid",
        session=trigger.session,
        entry_type=trigger.entry_type,
    )

    highs = ltf_df["high"].values
    lows = ltf_df["low"].values

    for i in range(trigger_bar_idx + 1, len(ltf_df)):
        bar_high = highs[i]
        bar_low = lows[i]

        tp_hit = False
        sl_hit = False

        if trigger.direction == "bullish":
            tp_hit = bar_high >= trigger.take_profit
            sl_hit = bar_low <= trigger.stop_loss
        else:  # bearish
            tp_hit = bar_low <= trigger.take_profit
            sl_hit = bar_high >= trigger.stop_loss

        if tp_hit and sl_hit:
            # 보수적: SL 처리
            result.result = "loss"
            result.exit_price = trigger.stop_loss
            result.exit_timestamp = str(ltf_df.index[i])
            result.bars_to_exit = i - trigger_bar_idx
            return result

        if tp_hit:
            result.result = "win"
            result.exit_price = trigger.take_profit
            result.exit_timestamp = str(ltf_df.index[i])
            result.bars_to_exit = i - trigger_bar_idx
            return result

        if sl_hit:
            result.result = "loss"
            result.exit_price = trigger.stop_loss
            result.exit_timestamp = str(ltf_df.index[i])
            result.bars_to_exit = i - trigger_bar_idx
            return result

    # 미터치
    return result


def run_backtest_for_symbol(
    symbol: str,
    htf_df: pd.DataFrame,
    mtf_df: pd.DataFrame,
    ltf_df: pd.DataFrame,
    param_set_name: str,
    score_set_name: str,
) -> BacktestRun:
    """
    단일 심볼에 대해 백테스트를 실행한다.

    LTF 데이터를 슬라이딩 윈도우로 순회하며 트리거를 탐색.
    세션 시간 내에서만 분석, 트리거 발생 시 시뮬레이션.
    동시에 하나의 포지션만 (이전 거래가 끝나야 다음 진입).

    Args:
        symbol: 거래 쌍
        htf_df: 4H OHLCV 전체 기간
        mtf_df: 15M OHLCV 전체 기간
        ltf_df: 5M OHLCV 전체 기간
        param_set_name: 파라미터 세트 이름
        score_set_name: 점수 세트 이름

    Returns:
        BacktestRun
    """
    run = BacktestRun(
        param_set=param_set_name,
        score_set=score_set_name,
        symbol=symbol,
    )

    if ltf_df.empty or htf_df.empty or mtf_df.empty:
        return run

    # 최소 데이터 요구량
    htf_min = 50
    mtf_min = 50
    ltf_min = 50
    ltf_step = 12  # 1시간(5m × 12봉) 단위로 슬라이딩

    n_ltf = len(ltf_df)
    in_position_until = 0  # 이 인덱스까지 포지션 유지 중

    for scan_idx in range(ltf_min, n_ltf - ltf_step, ltf_step):
        # 이전 거래가 아직 진행 중이면 스킵
        if scan_idx < in_position_until:
            continue

        scan_time = ltf_df.index[scan_idx]

        # 세션 체크
        in_session, session_name = _is_in_session(scan_time)
        if not in_session:
            continue

        # 현재 시점까지의 데이터 슬라이스
        htf_slice = htf_df[htf_df.index <= scan_time].iloc[-htf_min:]
        mtf_slice = mtf_df[mtf_df.index <= scan_time].iloc[-mtf_min:]
        ltf_slice = ltf_df.iloc[max(0, scan_idx - ltf_min):scan_idx + 1]

        if len(htf_slice) < htf_min or len(mtf_slice) < mtf_min or len(ltf_slice) < ltf_min:
            continue

        # 알고리즘 탑다운 분석 (실거래와 동일 코드)
        trigger = analyze_topdown(symbol, htf_slice, mtf_slice, ltf_slice, session_name)
        if trigger is None:
            continue

        # TP/SL 시뮬레이션
        trade = simulate_trade(trigger, ltf_df, scan_idx)
        run.trades.append(trade)
        run.total_trades += 1

        if trade.result == "win":
            run.wins += 1
        elif trade.result == "loss":
            run.losses += 1
        else:
            run.invalid += 1

        # 포지션이 종료될 때까지 다음 진입 불가
        if trade.bars_to_exit > 0:
            in_position_until = scan_idx + trade.bars_to_exit + 1

        logger.debug(
            "[BT] %s %s %s | score=%d R:R=%.1f → %s (%d bars)",
            symbol, trigger.direction, trigger.entry_type,
            trigger.setup_score, trigger.rr_ratio,
            trade.result, trade.bars_to_exit,
        )

    win_rate = (run.wins / (run.wins + run.losses) * 100) if (run.wins + run.losses) > 0 else 0
    logger.info(
        "[BT] %s 완료: %d trades | W=%d L=%d I=%d | 승률=%.1f%%",
        symbol, run.total_trades, run.wins, run.losses, run.invalid, win_rate,
    )

    return run
