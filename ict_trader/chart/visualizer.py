"""
차트 시각화.
mplfinance 캔들차트에 ICT 요소(OB/FVG/Swing/SL/TP/진입가) 마킹.
base64 인코딩으로 Claude Vision 전송, Telegram 첨부용 이미지 생성.
"""

from __future__ import annotations

import io
import base64
import logging
from pathlib import Path

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mplfinance as mpf

from ict_trader.algorithm.trigger import TriggerEvent
from ict_trader.algorithm.order_block import OrderBlock
from ict_trader.algorithm.fvg import FairValueGap

logger = logging.getLogger(__name__)

# 차트 스타일
CHART_STYLE = mpf.make_mpf_style(
    base_mpf_style="charles",
    marketcolors=mpf.make_marketcolors(
        up="#26a69a", down="#ef5350",
        edge={"up": "#26a69a", "down": "#ef5350"},
        wick={"up": "#26a69a", "down": "#ef5350"},
        volume={"up": "#26a69a80", "down": "#ef535080"},
    ),
    rc={
        "axes.facecolor": "#1e1e2f",
        "figure.facecolor": "#1e1e2f",
        "axes.edgecolor": "#3a3a5c",
        "axes.labelcolor": "#cccccc",
        "xtick.color": "#999999",
        "ytick.color": "#999999",
        "grid.color": "#2a2a45",
        "grid.linestyle": "--",
        "grid.alpha": 0.4,
    },
)

# 색상 상수
COLOR_BULL_OB = "#26a69a40"
COLOR_BEAR_OB = "#ef535040"
COLOR_BULL_FVG = "#4fc3f760"
COLOR_BEAR_FVG = "#ff980060"
COLOR_ENTRY = "#ffffff"
COLOR_SL = "#ff1744"
COLOR_TP = "#00e676"
COLOR_SWING_HIGH = "#ffeb3b"
COLOR_SWING_LOW = "#7c4dff"


def _add_ob_patches(
    ax: plt.Axes,
    obs: list[OrderBlock],
    df: pd.DataFrame,
) -> None:
    """OB 영역을 차트에 반투명 사각형으로 표시."""
    for ob in obs:
        if ob.index >= len(df):
            continue
        color = COLOR_BULL_OB if ob.ob_type == "bullish" else COLOR_BEAR_OB
        edge_color = "#26a69a" if ob.ob_type == "bullish" else "#ef5350"
        # OB 시작부터 차트 끝까지 연장
        x_start = ob.index
        x_width = len(df) - ob.index
        ax.barh(
            y=(ob.high + ob.low) / 2,
            width=x_width,
            height=ob.high - ob.low,
            left=x_start,
            color=color,
            edgecolor=edge_color,
            linewidth=0.8,
            alpha=0.4,
        )
        label = "OB" if not ob.is_poi else "POI"
        ax.text(
            x_start + 1, ob.high,
            label, fontsize=7, color=edge_color,
            va="bottom", ha="left", fontweight="bold",
        )


def _add_fvg_patches(
    ax: plt.Axes,
    fvgs: list[FairValueGap],
    df: pd.DataFrame,
) -> None:
    """FVG 영역을 차트에 표시."""
    for fvg in fvgs:
        if not fvg.is_active or fvg.index >= len(df):
            continue
        color = COLOR_BULL_FVG if fvg.fvg_type == "bullish" else COLOR_BEAR_FVG
        x_start = fvg.index
        x_width = len(df) - fvg.index
        ax.barh(
            y=(fvg.high + fvg.low) / 2,
            width=x_width,
            height=fvg.high - fvg.low,
            left=x_start,
            color=color,
            alpha=0.3,
        )
        # CE 점선
        ax.hlines(
            fvg.ce, x_start, x_start + x_width,
            colors=color.replace("60", ""),
            linestyles="dashed",
            linewidth=0.6,
            alpha=0.6,
        )


def _add_swing_markers(
    ax: plt.Axes,
    swing_highs: list,
    swing_lows: list,
) -> None:
    """Swing High/Low를 마커로 표시."""
    for sh in swing_highs:
        ax.plot(
            sh.index, sh.price,
            marker="v", color=COLOR_SWING_HIGH,
            markersize=5, alpha=0.8,
        )
    for sl in swing_lows:
        ax.plot(
            sl.index, sl.price,
            marker="^", color=COLOR_SWING_LOW,
            markersize=5, alpha=0.8,
        )


def _add_entry_lines(
    ax: plt.Axes,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    n_candles: int,
) -> None:
    """진입가/SL/TP 수평선 표시."""
    line_start = n_candles * 0.6
    line_end = n_candles - 1

    # 진입가
    ax.hlines(
        entry_price, line_start, line_end,
        colors=COLOR_ENTRY, linestyles="solid",
        linewidth=1.5, alpha=0.9,
    )
    ax.text(
        line_end + 0.5, entry_price,
        f"Entry ${entry_price:,.1f}",
        fontsize=7, color=COLOR_ENTRY, va="center",
    )

    # SL
    ax.hlines(
        stop_loss, line_start, line_end,
        colors=COLOR_SL, linestyles="dashed",
        linewidth=1.2, alpha=0.9,
    )
    ax.text(
        line_end + 0.5, stop_loss,
        f"SL ${stop_loss:,.1f}",
        fontsize=7, color=COLOR_SL, va="center",
    )

    # TP
    ax.hlines(
        take_profit, line_start, line_end,
        colors=COLOR_TP, linestyles="dashed",
        linewidth=1.2, alpha=0.9,
    )
    ax.text(
        line_end + 0.5, take_profit,
        f"TP ${take_profit:,.1f}",
        fontsize=7, color=COLOR_TP, va="center",
    )


def _render_single_chart(
    df: pd.DataFrame,
    title: str,
    trigger: TriggerEvent | None = None,
    obs: list[OrderBlock] | None = None,
    fvgs: list[FairValueGap] | None = None,
    swing_highs: list | None = None,
    swing_lows: list | None = None,
    show_volume: bool = True,
) -> plt.Figure:
    """단일 타임프레임 캔들차트를 렌더링한다."""
    kwargs = {
        "type": "candle",
        "style": CHART_STYLE,
        "title": title,
        "ylabel": "Price",
        "volume": show_volume,
        "figsize": (14, 7),
        "returnfig": True,
        "warn_too_much_data": 999,
    }

    fig, axes = mpf.plot(df, **kwargs)
    ax = axes[0]

    # ICT 요소 추가
    if obs:
        _add_ob_patches(ax, obs, df)
    if fvgs:
        _add_fvg_patches(ax, fvgs, df)
    if swing_highs or swing_lows:
        _add_swing_markers(ax, swing_highs or [], swing_lows or [])
    if trigger:
        _add_entry_lines(ax, trigger.entry_price, trigger.stop_loss, trigger.take_profit, len(df))

    fig.tight_layout()
    return fig


def render_trigger_charts(
    trigger: TriggerEvent,
    htf_df: pd.DataFrame,
    ltf_df: pd.DataFrame,
    htf_ms_result=None,
    ltf_ms_result=None,
) -> plt.Figure:
    """
    4H + 5M 듀얼 차트를 생성한다.

    Args:
        trigger: TriggerEvent
        htf_df: 4H OHLCV (최근 80봉 사용)
        ltf_df: 5M OHLCV (최근 80봉 사용)
        htf_ms_result: HTF MarketStructureResult (Swing 마킹용)
        ltf_ms_result: LTF MarketStructureResult

    Returns:
        matplotlib Figure
    """
    # 최근 80봉으로 자르기
    htf_plot = htf_df.iloc[-80:].copy() if len(htf_df) > 80 else htf_df.copy()
    ltf_plot = ltf_df.iloc[-80:].copy() if len(ltf_df) > 80 else ltf_df.copy()

    fig, (ax_htf, ax_ltf) = plt.subplots(
        2, 1, figsize=(16, 14),
        facecolor="#1e1e2f",
    )

    direction_emoji = "LONG" if trigger.direction == "bullish" else "SHORT"
    suptitle = (
        f"{trigger.symbol}  |  {direction_emoji}  |  "
        f"{trigger.grade} ({trigger.setup_score}pts)  |  "
        f"R:R 1:{trigger.rr_ratio:.1f}  |  {trigger.session}"
    )
    fig.suptitle(suptitle, fontsize=13, color="#ffffff", fontweight="bold", y=0.98)

    # ── HTF (4H) 차트 ──
    _draw_candles_on_axis(ax_htf, htf_plot, "4H")
    if trigger.htf_obs:
        _add_ob_patches(ax_htf, trigger.htf_obs, htf_plot)
    if trigger.htf_fvgs:
        _add_fvg_patches(ax_htf, [f for f in trigger.htf_fvgs if f.is_active], htf_plot)
    if htf_ms_result:
        _add_swing_markers(ax_htf, htf_ms_result.swing_highs, htf_ms_result.swing_lows)

    # ── LTF (5M) 차트 ──
    _draw_candles_on_axis(ax_ltf, ltf_plot, "5M")
    if ltf_ms_result:
        _add_swing_markers(ax_ltf, ltf_ms_result.swing_highs, ltf_ms_result.swing_lows)
    _add_entry_lines(ax_ltf, trigger.entry_price, trigger.stop_loss, trigger.take_profit, len(ltf_plot))

    fig.tight_layout(rect=[0, 0, 1, 0.96])
    return fig


def _draw_candles_on_axis(ax: plt.Axes, df: pd.DataFrame, label: str) -> None:
    """Axes에 캔들스틱을 직접 그린다."""
    ax.set_facecolor("#1e1e2f")
    ax.tick_params(colors="#999999")
    ax.set_title(label, fontsize=11, color="#cccccc", loc="left")

    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values

    for i in range(len(df)):
        color = "#26a69a" if closes[i] >= opens[i] else "#ef5350"
        # 위크
        ax.plot([i, i], [lows[i], highs[i]], color=color, linewidth=0.8)
        # 바디
        body_low = min(opens[i], closes[i])
        body_high = max(opens[i], closes[i])
        body_height = body_high - body_low
        if body_height == 0:
            body_height = (highs[i] - lows[i]) * 0.01
        ax.bar(
            i, body_height, bottom=body_low,
            width=0.6, color=color, edgecolor=color, linewidth=0.5,
        )

    ax.set_xlim(-1, len(df) + 5)
    ax.grid(True, alpha=0.2, color="#2a2a45", linestyle="--")

    # x축 라벨 (5개 기준점)
    n = len(df)
    tick_positions = np.linspace(0, n - 1, min(6, n), dtype=int)
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(
        [df.index[i].strftime("%m/%d %H:%M") for i in tick_positions],
        fontsize=7, color="#999999", rotation=30,
    )


def figure_to_base64(fig: plt.Figure, dpi: int = 150) -> str:
    """matplotlib Figure를 base64 PNG 문자열로 변환."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", facecolor=fig.get_facecolor())
    buf.seek(0)
    encoded = base64.b64encode(buf.read()).decode("utf-8")
    buf.close()
    plt.close(fig)
    return encoded


def figure_to_bytes(fig: plt.Figure, dpi: int = 150) -> bytes:
    """matplotlib Figure를 PNG bytes로 변환 (Telegram 전송용)."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", facecolor=fig.get_facecolor())
    buf.seek(0)
    data = buf.read()
    buf.close()
    plt.close(fig)
    return data


def save_chart(fig: plt.Figure, filepath: str | Path, dpi: int = 150) -> Path:
    """차트를 파일로 저장한다."""
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(filepath), format="png", dpi=dpi, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    logger.info("차트 저장: %s", filepath)
    return filepath


def generate_chart_for_trigger(
    trigger: TriggerEvent,
    htf_df: pd.DataFrame,
    ltf_df: pd.DataFrame,
    htf_ms_result=None,
    ltf_ms_result=None,
) -> tuple[str, bytes]:
    """
    TriggerEvent용 차트를 생성하고 base64 + bytes를 반환한다.

    Returns:
        (base64_str, png_bytes) — LLM Vision용, Telegram 전송용
    """
    fig = render_trigger_charts(
        trigger, htf_df, ltf_df, htf_ms_result, ltf_ms_result,
    )

    # base64 (LLM용)
    buf_b64 = io.BytesIO()
    fig.savefig(buf_b64, format="png", dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    buf_b64.seek(0)
    b64_str = base64.b64encode(buf_b64.read()).decode("utf-8")
    buf_b64.close()

    # bytes (Telegram용)
    buf_bytes = io.BytesIO()
    fig.savefig(buf_bytes, format="png", dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    buf_bytes.seek(0)
    png_bytes = buf_bytes.read()
    buf_bytes.close()

    plt.close(fig)
    return b64_str, png_bytes
