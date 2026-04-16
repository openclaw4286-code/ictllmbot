"""
메인 루프 상태 관리.
ICT 알고리즘은 상시 판단, LLM 전달 시점에서 WAIT 로직 적용.
- LLM 결과는 PASS 또는 WAIT만 존재 (REJECT 없음)
- PASS: 즉시 주문 실행
- WAIT: 10분간 해당 심볼 스킵 후 재시도
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

# WAIT_COOLDOWN은 LLM이 동적으로 반환 (5~120분)

logger = logging.getLogger(__name__)


@dataclass
class SymbolState:
    """심볼별 상태."""
    llm_running: bool = False           # LLM 검토 진행 중
    wait_until: float = 0.0             # WAIT 쿨다운 만료 시각 (epoch)
    total_signals: int = 0
    total_passes: int = 0
    total_waits: int = 0
    total_fills: int = 0


@dataclass
class LoopStats:
    """루프 전체 통계."""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    total_loops: int = 0
    total_scans: int = 0
    total_triggers: int = 0
    total_llm_calls: int = 0
    total_passes: int = 0
    total_waits: int = 0
    total_fills: int = 0
    total_skipped_wait: int = 0
    total_skipped_position: int = 0
    total_skipped_llm: int = 0


class LoopState:
    """메인 루프 상태 관리자."""

    def __init__(self) -> None:
        self._symbols: dict[str, SymbolState] = {}
        self.stats = LoopStats()

    def _get_symbol(self, symbol: str) -> SymbolState:
        if symbol not in self._symbols:
            self._symbols[symbol] = SymbolState()
        return self._symbols[symbol]

    # ──────────────────────────────────────
    # 스킵 조건 판정
    # ──────────────────────────────────────

    def should_skip(self, symbol: str, has_position: bool) -> tuple[bool, str]:
        """
        스킵 조건 3가지 (순서대로):
        1. LLM 실행 중
        2. 포지션 보유 중
        3. WAIT 쿨다운 (10분)
        """
        state = self._get_symbol(symbol)

        if state.llm_running:
            self.stats.total_skipped_llm += 1
            return True, "LLM 실행 중"

        if has_position:
            self.stats.total_skipped_position += 1
            return True, "포지션 보유 중"

        now = time.time()
        if state.wait_until > now:
            remaining = int(state.wait_until - now)
            self.stats.total_skipped_wait += 1
            return True, f"WAIT 쿨다운 {remaining}초 남음"

        return False, ""

    # ──────────────────────────────────────
    # 상태 업데이트
    # ──────────────────────────────────────

    def mark_signal_generated(self, symbol: str) -> None:
        """신호 생성 기록."""
        state = self._get_symbol(symbol)
        state.total_signals += 1
        self.stats.total_triggers += 1

    def mark_llm_start(self, symbol: str) -> None:
        """LLM 검토 시작."""
        state = self._get_symbol(symbol)
        state.llm_running = True
        self.stats.total_llm_calls += 1

    def mark_llm_done(self, symbol: str, verdict: str, wait_minutes: int = 10) -> None:
        """
        LLM 검토 완료.
        - PASS: 주문 실행으로 진행
        - WAIT: wait_minutes분 쿨다운 (LLM이 5~120분 반환)
        """
        state = self._get_symbol(symbol)
        state.llm_running = False

        if verdict == "PASS":
            state.total_passes += 1
            self.stats.total_passes += 1
        else:
            # WAIT: LLM이 결정한 시간만큼 대기
            state.wait_until = time.time() + (wait_minutes * 60)
            state.total_waits += 1
            self.stats.total_waits += 1

    def mark_filled(self, symbol: str) -> None:
        """주문 체결됨."""
        state = self._get_symbol(symbol)
        state.total_fills += 1
        self.stats.total_fills += 1

    def mark_loop_complete(self, symbols_scanned: int) -> None:
        """루프 1회 완료."""
        self.stats.total_loops += 1
        self.stats.total_scans += symbols_scanned

    # ──────────────────────────────────────
    # 조회
    # ──────────────────────────────────────

    def get_llm_running_symbols(self) -> list[str]:
        return [s for s, st in self._symbols.items() if st.llm_running]

    def get_symbol_state(self, symbol: str) -> SymbolState:
        return self._get_symbol(symbol)

    def summary(self) -> str:
        s = self.stats
        uptime = datetime.now(timezone.utc) - s.started_at
        hours = uptime.total_seconds() / 3600

        lines = [
            f"=== 루프 통계 (가동 {hours:.1f}시간) ===",
            f"루프 {s.total_loops}회 | 스캔 {s.total_scans}회",
            f"신호 {s.total_triggers}건 | LLM {s.total_llm_calls}회",
            f"PASS {s.total_passes} | WAIT {s.total_waits}",
            f"체결 {s.total_fills}건",
            f"스킵: WAIT={s.total_skipped_wait} 포지션={s.total_skipped_position} "
            f"LLM진행={s.total_skipped_llm}",
        ]

        running = self.get_llm_running_symbols()
        if running:
            lines.append(f"LLM 진행 중: {', '.join(running)}")

        return "\n".join(lines)
