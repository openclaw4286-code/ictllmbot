"""
백테스트 결과 리포터.
3가지 CSV 파일 생성:
1. 전체 거래 내역 CSV (세트 조합별)
2. 점수 구간별 승률 CSV
3. 세트 조합 비교 요약 CSV
"""

import logging
from datetime import datetime

import pandas as pd

from ict_trader.config import BACKTEST_RESULTS_DIR, KELLY_WIN_RATE
from ict_trader.backtest.engine import BacktestRun, TradeResult

logger = logging.getLogger(__name__)

_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")


def _trades_to_dataframe(runs: list[BacktestRun]) -> pd.DataFrame:
    """모든 거래를 하나의 DataFrame으로 합친다."""
    rows = []
    for run in runs:
        for trade in run.trades:
            rows.append({
                "param_set": run.param_set,
                "score_set": run.score_set,
                "symbol": trade.symbol,
                "timestamp": trade.timestamp,
                "direction": trade.direction,
                "entry_price": trade.entry_price,
                "stop_loss": trade.stop_loss,
                "take_profit": trade.take_profit,
                "rr_ratio": trade.rr_ratio,
                "setup_score": trade.setup_score,
                "grade": trade.grade,
                "confluences": trade.confluences,
                "result": trade.result,
                "session": trade.session,
                "entry_type": trade.entry_type,
                "exit_price": trade.exit_price,
                "exit_timestamp": trade.exit_timestamp,
                "bars_to_exit": trade.bars_to_exit,
            })
    return pd.DataFrame(rows)


def save_trades_csv(runs: list[BacktestRun]) -> str:
    """
    1. 전체 거래 내역 CSV.
    세트 조합별로 모든 거래를 기록한다.
    """
    df = _trades_to_dataframe(runs)
    if df.empty:
        logger.warning("거래 내역 없음, CSV 생성 스킵")
        return ""

    filepath = BACKTEST_RESULTS_DIR / f"trades_{_timestamp}.csv"
    df.to_csv(filepath, index=False, encoding="utf-8-sig")
    logger.info("거래 내역 CSV 저장: %s (%d건)", filepath.name, len(df))
    return str(filepath)


def save_score_winrate_csv(runs: list[BacktestRun]) -> str:
    """
    2. 점수 구간별 승률 CSV.
    60~69 / 70~79 / 80~89 / 90+ 구간별 거래 수, 승률, 평균 R:R,
    켈리 권장값 대비 실제 승률.
    """
    df = _trades_to_dataframe(runs)
    if df.empty:
        logger.warning("거래 없음, 점수 구간 CSV 스킵")
        return ""

    # 유효 거래만 (win/loss)
    valid = df[df["result"].isin(["win", "loss"])].copy()
    if valid.empty:
        logger.warning("유효 거래 없음")
        return ""

    # 점수 구간 분류
    def score_bin(score):
        if score >= 90:
            return "90+"
        elif score >= 80:
            return "80-89"
        elif score >= 70:
            return "70-79"
        elif score >= 60:
            return "60-69"
        return "<60"

    valid["score_bin"] = valid["setup_score"].apply(score_bin)
    valid["is_win"] = (valid["result"] == "win").astype(int)

    # 조합별 + 점수구간별 집계
    grouped = valid.groupby(["param_set", "score_set", "score_bin"]).agg(
        trade_count=("is_win", "count"),
        wins=("is_win", "sum"),
        avg_rr=("rr_ratio", "mean"),
    ).reset_index()

    grouped["win_rate"] = (grouped["wins"] / grouped["trade_count"] * 100).round(1)
    grouped["avg_rr"] = grouped["avg_rr"].round(2)

    # 켈리 권장 승률 대비
    def kelly_expected(bin_label):
        return KELLY_WIN_RATE.get(bin_label, 0.0) * 100

    grouped["kelly_expected_wr"] = grouped["score_bin"].apply(kelly_expected)
    grouped["wr_vs_kelly"] = (grouped["win_rate"] - grouped["kelly_expected_wr"]).round(1)

    # 정렬
    bin_order = {"<60": 0, "60-69": 1, "70-79": 2, "80-89": 3, "90+": 4}
    grouped["_sort"] = grouped["score_bin"].map(bin_order)
    grouped.sort_values(["param_set", "score_set", "_sort"], inplace=True)
    grouped.drop(columns=["_sort"], inplace=True)

    filepath = BACKTEST_RESULTS_DIR / f"score_winrate_{_timestamp}.csv"
    grouped.to_csv(filepath, index=False, encoding="utf-8-sig")
    logger.info("점수 구간별 승률 CSV 저장: %s", filepath.name)
    return str(filepath)


def save_combination_summary_csv(runs: list[BacktestRun]) -> str:
    """
    3. 세트 조합 비교 요약 CSV.
    세트명, 총 신호 수, 승률, 평균 R:R, 최대 연속 손실, 권장 여부.
    """
    df = _trades_to_dataframe(runs)
    if df.empty:
        logger.warning("거래 없음, 조합 요약 CSV 스킵")
        return ""

    valid = df[df["result"].isin(["win", "loss"])].copy()

    rows = []
    for (param_set, score_set), group in valid.groupby(["param_set", "score_set"]):
        total = len(group)
        wins = (group["result"] == "win").sum()
        losses = total - wins
        win_rate = (wins / total * 100) if total > 0 else 0
        avg_rr = group["rr_ratio"].mean()

        # 최대 연속 손실
        max_consec_loss = _max_consecutive_losses(group["result"].tolist())

        # 권장 여부: 승률 50% 이상 & 최대 연속 손실 8 이하
        recommended = "Y" if (win_rate >= 50 and max_consec_loss <= 8) else "N"

        rows.append({
            "param_set": param_set,
            "score_set": score_set,
            "total_signals": total,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(win_rate, 1),
            "avg_rr": round(avg_rr, 2),
            "max_consecutive_loss": max_consec_loss,
            "recommended": recommended,
        })

    summary = pd.DataFrame(rows)
    summary.sort_values("win_rate_pct", ascending=False, inplace=True)

    filepath = BACKTEST_RESULTS_DIR / f"combination_summary_{_timestamp}.csv"
    summary.to_csv(filepath, index=False, encoding="utf-8-sig")
    logger.info("조합 비교 요약 CSV 저장: %s", filepath.name)

    # 콘솔에도 출력
    logger.info("\n=== 조합 비교 요약 ===")
    for _, row in summary.iterrows():
        logger.info(
            "  [%s] param=%s, score=%s | %d trades, 승률=%.1f%%, "
            "평균R:R=%.2f, 최대연속손실=%d",
            row["recommended"], row["param_set"], row["score_set"],
            row["total_signals"], row["win_rate_pct"],
            row["avg_rr"], row["max_consecutive_loss"],
        )

    return str(filepath)


def _max_consecutive_losses(results: list[str]) -> int:
    """최대 연속 손실 횟수를 계산한다."""
    max_streak = 0
    current = 0
    for r in results:
        if r == "loss":
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak
