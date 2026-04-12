"""
백테스트 러너.
config의 파라미터 세트 × 점수 세트 조합을 순차 실행.
1년치 과거 OHLCV를 수집하여 각 조합별 백테스트.
"""

import asyncio
import logging
import sys
import time
from datetime import datetime, timezone, timedelta

from ict_trader.config import (
    BACKTEST_DAYS,
    BACKTEST_PARAM_SETS,
    BACKTEST_SCORE_SETS,
    PARAM_SETS,
    SCORE_SETS,
    HTF, MTF, LTF,
    LOG_DIR,
)
# 활성 세트를 런타임에 교체하기 위해 config 모듈 자체를 import
import ict_trader.config as config_module

from ict_trader.data.universe import fetch_top_coins
from ict_trader.data.fetcher import fetch_ohlcv_range, close_exchange
from ict_trader.backtest.engine import run_backtest_for_symbol, BacktestRun
from ict_trader.backtest.reporter import (
    save_trades_csv,
    save_score_winrate_csv,
    save_combination_summary_csv,
)

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    """백테스트용 로깅 설정."""
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    log_file = LOG_DIR / f"backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(str(log_file), encoding="utf-8"),
        ],
    )
    logging.getLogger("ccxt").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _timeframe_to_ms(tf: str) -> int:
    """타임프레임을 밀리초로 변환."""
    if tf.endswith("m"):
        return int(tf[:-1]) * 60 * 1000
    elif tf.endswith("h"):
        return int(tf[:-1]) * 3600 * 1000
    return 60000


async def _fetch_symbol_data(
    symbol: str,
    since_ms: int,
    until_ms: int,
) -> dict[str, "pd.DataFrame"]:
    """심볼의 3개 타임프레임 OHLCV를 수집한다."""
    import pandas as pd

    logger.info("  데이터 수집: %s", symbol)
    frames: dict[str, pd.DataFrame] = {}

    for tf in [HTF, MTF, LTF]:
        df = await fetch_ohlcv_range(symbol, tf, since_ms, until_ms)
        frames[tf] = df
        if not df.empty:
            logger.info("    %s: %d봉 (%s ~ %s)", tf, len(df),
                        df.index[0].strftime("%Y-%m-%d"),
                        df.index[-1].strftime("%Y-%m-%d"))
        else:
            logger.warning("    %s: 데이터 없음", tf)

    return frames


async def run_all_combinations() -> None:
    """
    전체 파라미터 세트 × 점수 세트 조합을 순차 실행한다.

    1. 유니버스에서 심볼 목록 조회
    2. 각 심볼의 1년치 OHLCV 수집
    3. 각 (파라미터, 점수) 조합별 백테스트 실행
    4. 결과 CSV 저장
    """
    logger.info("=" * 60)
    logger.info("백테스트 시작")
    logger.info("기간: %d일", BACKTEST_DAYS)
    logger.info("파라미터 세트: %s", BACKTEST_PARAM_SETS)
    logger.info("점수 세트: %s", BACKTEST_SCORE_SETS)
    logger.info("=" * 60)

    start_time = time.time()

    # 기간 설정
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=BACKTEST_DAYS)
    since_ms = int(since.timestamp() * 1000)
    until_ms = int(now.timestamp() * 1000)

    # 유니버스
    symbols = fetch_top_coins()
    if not symbols:
        logger.error("유니버스 비어있음, 백테스트 중단")
        return

    logger.info("대상 심볼: %d개 — %s", len(symbols), ", ".join(symbols[:5]) + "...")

    # 심볼별 데이터 수집 (1회만, 조합별로 재사용)
    symbol_data: dict[str, dict] = {}
    for symbol in symbols:
        try:
            frames = await _fetch_symbol_data(symbol, since_ms, until_ms)
            # 최소 데이터 확인
            htf_ok = not frames.get(HTF, __import__("pandas").DataFrame()).empty
            ltf_ok = not frames.get(LTF, __import__("pandas").DataFrame()).empty
            if htf_ok and ltf_ok:
                symbol_data[symbol] = frames
            else:
                logger.warning("  %s: 데이터 부족, 스킵", symbol)
        except Exception as e:
            logger.error("  %s: 데이터 수집 실패 — %s", symbol, e)

    logger.info("데이터 수집 완료: %d/%d 심볼", len(symbol_data), len(symbols))

    if not symbol_data:
        logger.error("유효한 데이터 없음, 백테스트 중단")
        await close_exchange()
        return

    # 조합별 백테스트 실행
    all_runs: list[BacktestRun] = []
    combination_count = len(BACKTEST_PARAM_SETS) * len(BACKTEST_SCORE_SETS)
    combo_idx = 0

    for param_name in BACKTEST_PARAM_SETS:
        for score_name in BACKTEST_SCORE_SETS:
            combo_idx += 1
            logger.info(
                "\n[%d/%d] 조합: param=%s, score=%s",
                combo_idx, combination_count, param_name, score_name,
            )

            # 활성 세트를 런타임에 교체
            config_module.ACTIVE_PARAM_SET = param_name
            config_module.ACTIVE_SCORE_SET = score_name

            combo_runs: list[BacktestRun] = []

            for symbol, frames in symbol_data.items():
                htf_df = frames.get(HTF)
                mtf_df = frames.get(MTF)
                ltf_df = frames.get(LTF)

                if htf_df is None or mtf_df is None or ltf_df is None:
                    continue

                run = run_backtest_for_symbol(
                    symbol, htf_df, mtf_df, ltf_df,
                    param_name, score_name,
                )
                combo_runs.append(run)
                all_runs.append(run)

            # 조합 결과 요약
            total = sum(r.total_trades for r in combo_runs)
            wins = sum(r.wins for r in combo_runs)
            losses = sum(r.losses for r in combo_runs)
            wr = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
            logger.info(
                "  조합 결과: %d trades, W=%d L=%d, 승률=%.1f%%",
                total, wins, losses, wr,
            )

    # CSV 결과 저장
    logger.info("\n결과 파일 저장 중...")
    save_trades_csv(all_runs)
    save_score_winrate_csv(all_runs)
    save_combination_summary_csv(all_runs)

    elapsed = time.time() - start_time
    logger.info(
        "\n백테스트 완료 (%.1f분 소요) | 총 %d 거래",
        elapsed / 60, sum(r.total_trades for r in all_runs),
    )

    # 거래소 연결 종료
    await close_exchange()


def main() -> None:
    """백테스트 엔트리포인트."""
    _setup_logging()
    logger.info("ICT Trader — 백테스트 모드")

    try:
        asyncio.run(run_all_combinations())
    except KeyboardInterrupt:
        logger.info("백테스트 중단 (키보드 인터럽트)")
    except Exception as e:
        logger.critical("백테스트 치명적 오류: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
