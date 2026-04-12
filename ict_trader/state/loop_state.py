"""
메인 루프 상태 관리.
심볼별 쿨다운, LLM 실행 플래그, WAIT 연속 횟수, 루프 통계 추적.
"""

import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ict_trader.config import SIGNAL_COOLDOWN_SECONDS, LLM_WAIT_MAX_CONSECUTIVE

logger = logging.getLogger(__name__)


@dataclass
class SymbolState:
    """심볼별 상태."""
    last_signal_ts: float = 0.0         # 마지막 신호 시간 (epoch)
    llm_running: bool = False           # LLM 검토 진행 중
    wait_count: int = 0                 # 연속 WAIT 횟수
    total_signals: int = 0              # 총 신호 발생 수
    total_passes: int = 0               # LLM PASS 수
    total_rejects: int = 0              # LLM REJECT 수
    total_fills: int = 0                # 체결 수


@dataclass
class LoopStats:
    """루프 전체 통계."""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    total_loops: int = 0
    total_scans: int = 0                # 심볼 스캔 총 횟수
    total_triggers: int = 0             # TriggerEvent 생성 수
    total_llm_calls: int = 0            # LLM 호출 수
    total_passes: int = 0
    total_rejects: int = 0
    total_waits: int = 0
    total_fills: int = 0                # 실제 체결 수
    total_skipped_cooldown: int = 0     # 쿨다운 스킵 수
    total_skipped_position: int = 0     # 포지션 보유 스킵 수
    total_skipped_llm: int = 0          # LLM 진행중 스킵 수
    total_skipped_wait: int = 0         # WAIT 초과 스킵 수


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
        해당 심볼을 이번 루프에서 스킵해야 하는지 판단한다.

        Returns:
            (skip: bool, reason: str)
        """
        state = self._get_symbol(symbol)

        # 1. LLM 실행 중
        if state.llm_running:
            self.stats.total_skipped_llm += 1
            return True, "LLM 실행 중"

        # 2. 포지션 보유 중
        if has_position:
            self.stats.total_skipped_position += 1
            return True, "포지션 보유 중"

        # 3. 마지막 신호 쿨다운
        elapsed = time.time() - state.last_signal_ts
        if state.last_signal_ts > 0 and elapsed < SIGNAL_COOLDOWN_SECONDS:
            remaining = int(SIGNAL_COOLDOWN_SECONDS - elapsed)
            self.stats.total_skipped_cooldown += 1
            return True, f"쿨다운 {remaining}초 남음"

        # 4. WAIT 연속 초과
        if state.wait_count >= LLM_WAIT_MAX_CONSECUTIVE:
            self.stats.total_skipped_wait += 1
            return True, f"WAIT {state.wait_count}회 연속 (최대 {LLM_WAIT_MAX_CONSECUTIVE})"

        return False, ""

    # ──────────────────────────────────────
    # 상태 업데이트
    # ──────────────────────────────────────

    def mark_signal_generated(self, symbol: str) -> None:
        """신호가 생성되었음을 기록."""
        state = self._get_symbol(symbol)
        state.last_signal_ts = time.time()
        state.total_signals += 1
        self.stats.total_triggers += 1

    def mark_llm_start(self, symbol: str) -> None:
        """LLM 검토 시작."""
        state = self._get_symbol(symbol)
        state.llm_running = True
        self.stats.total_llm_calls += 1

    def mark_llm_done(self, symbol: str, verdict: str) -> None:
        """
        LLM 검토 완료.

        Args:
            symbol: 심볼
            verdict: "PASS" / "REJECT" / "WAIT"
        """
        state = self._get_symbol(symbol)
        state.llm_running = False

        if verdict == "PASS":
            state.wait_count = 0
            state.total_passes += 1
            self.stats.total_passes += 1
        elif verdict == "REJECT":
            state.wait_count = 0
            state.total_rejects += 1
            self.stats.total_rejects += 1
        elif verdict == "WAIT":
            state.wait_count += 1
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

    def reset_wait_count(self, symbol: str) -> None:
        """WAIT 카운트를 리셋한다 (수동 리셋용)."""
        state = self._get_symbol(symbol)
        state.wait_count = 0

    def reset_cooldown(self, symbol: str) -> None:
        """쿨다운을 리셋한다."""
        state = self._get_symbol(symbol)
        state.last_signal_ts = 0.0

    # ──────────────────────────────────────
    # 조회
    # ──────────────────────────────────────

    def get_llm_running_symbols(self) -> list[str]:
        """현재 LLM 실행 중인 심볼 목록."""
        return [s for s, st in self._symbols.items() if st.llm_running]

    def get_symbol_state(self, symbol: str) -> SymbolState:
        return self._get_symbol(symbol)

    def summary(self) -> str:
        """루프 통계 요약."""
        s = self.stats
        uptime = datetime.now(timezone.utc) - s.started_at
        hours = uptime.total_seconds() / 3600

        lines = [
            f"=== 루프 통계 (가동 {hours:.1f}시간) ===",
            f"루프 {s.total_loops}회 | 스캔 {s.total_scans}회",
            f"신호 {s.total_triggers}건 | LLM {s.total_llm_calls}회",
            f"PASS {s.total_passes} | REJECT {s.total_rejects} | WAIT {s.total_waits}",
            f"체결 {s.total_fills}건",
            f"스킵: 쿨다운={s.total_skipped_cooldown} 포지션={s.total_skipped_position} "
            f"LLM진행={s.total_skipped_llm} WAIT초과={s.total_skipped_wait}",
        ]

        # 심볼별 LLM 진행 중
        running = self.get_llm_running_symbols()
        if running:
            lines.append(f"LLM 진행 중: {', '.join(running)}")

        return "\n".join(lines)
