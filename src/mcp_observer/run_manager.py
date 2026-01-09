"""
Run Manager for MCP Observer SDK.
Manages run lifecycle with automatic timeout-based closure.

A run represents a logical grouping of tool calls within a session,
typically corresponding to a single conversation or task.
"""

import asyncio
import time
import uuid
import logging
from typing import Dict, Optional, Tuple
from datetime import datetime, timezone
from dataclasses import dataclass


@dataclass
class ActiveRun:
    """In-memory state for an active run."""
    run_id: str
    session_id: str

    # Wall-clock timestamps (for logging, API payloads)
    started_at: datetime  # Timezone-aware UTC
    last_seen_at: datetime  # Timezone-aware UTC

    # Monotonic timestamps (for timeout math, clock-change resistant)
    started_at_mono: float  # time.monotonic() value
    last_seen_at_mono: float  # time.monotonic() value

    def is_timed_out(self, timeout_seconds: float) -> bool:
        """
        Check if this run has exceeded the inactivity timeout.

        Uses monotonic time for clock-change resilience.
        """
        elapsed_mono = time.monotonic() - self.last_seen_at_mono
        return elapsed_mono > timeout_seconds

    def duration_seconds(self) -> float:
        """Calculate duration using monotonic time."""
        return time.monotonic() - self.started_at_mono


class RunManager:
    """
    Manages run lifecycle for MCP tool calls.

    A run represents a logical grouping of tool calls within a session,
    typically corresponding to a single conversation or task. Runs are
    automatically created and closed based on inactivity timeouts.

    Thread Safety:
        This class uses asyncio.Lock for basic async safety. Since MCP servers
        are typically per-user instances, heavy concurrency is not expected.
    """

    def __init__(
        self,
        run_timeout_seconds: float = 30.0,
        sweeper_interval: float = 5.0,
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize RunManager.

        Args:
            run_timeout_seconds: Inactivity timeout in seconds (default 30s)
            sweeper_interval: How often to check for expired runs (default 5s)
            logger: Optional logger for run lifecycle events
        """
        self._run_timeout_seconds = run_timeout_seconds
        self._sweeper_interval = sweeper_interval
        self._logger = logger or logging.getLogger(__name__)

        # In-memory map: session_id -> ActiveRun
        # Only one active run per session at a time
        self._active_runs: Dict[str, ActiveRun] = {}

        # Async lock for thread-safe concurrent access
        self._lock = asyncio.Lock()

        # Track closed runs for logging/debugging
        self._closed_runs_count = 0

        # Sweeper control
        self._sweeper_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()

        # Reference to observer for run_end callbacks
        self._observer = None

    def set_observer(self, observer):
        """Set reference to MCPObserver for run_end notifications."""
        self._observer = observer

    async def start_sweeper(self):
        """Start the background sweeper task."""
        if self._sweeper_task is None or self._sweeper_task.done():
            if self._shutdown_event.is_set():
                self._shutdown_event.clear()
            self._sweeper_task = asyncio.create_task(self._sweeper_loop())
            self._logger.info("Run sweeper started")

    async def stop_sweeper(self):
        """Stop the background sweeper gracefully."""
        if self._sweeper_task:
            self._shutdown_event.set()
            await self._sweeper_task
            self._sweeper_task = None
            self._logger.info("Run sweeper stopped")

    async def _sweeper_loop(self):
        """Background loop that closes expired runs."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(self._sweeper_interval)
                await self._sweep_expired_runs()
            except Exception as e:
                self._logger.error(f"Sweeper error: {e}", exc_info=True)

    async def _sweep_expired_runs(self):
        """Check all active runs and close expired ones."""
        from datetime import timezone
        async with self._lock:
            expired_runs = []

            for session_id, run in list(self._active_runs.items()):
                if run.is_timed_out(self._run_timeout_seconds):
                    expired_runs.append(run)

            for run in expired_runs:
                ended_at = datetime.now(timezone.utc)
                await self._close_run_and_notify(
                    run,
                    reason="timeout_sweep",
                    ended_at=ended_at,
                    notify_reason="timeout"
                )

            if expired_runs:
                self._logger.info(
                    f"Sweeper closed {len(expired_runs)} expired run(s)"
                )

    async def resolve_or_create_run(
        self,
        session_id: str,
        timestamp: datetime
    ) -> Tuple[str, bool]:
        """
        Resolve or create a run for the given session.

        This is the core method called before each tool call. It implements
        the timeout-based run lifecycle:
        1. Check if there's an active run for this session
        2. If yes and not timed out, return it (and update last_seen_at)
        3. If yes but timed out, close it and create new one
        4. If no active run, create new one

        Args:
            session_id: The session ID from FastMCP Context
            timestamp: The timestamp of the current tool call

        Returns:
            Tuple of (run_id, is_new_run)
            - run_id: The run ID to attach to this call
            - is_new_run: True if a new run was created, False if reusing existing
        """
        await self.start_sweeper()
        async with self._lock:
            active_run = self._active_runs.get(session_id)

            # Case 1: No active run exists - create new one
            if active_run is None:
                return await self._create_new_run(session_id, timestamp), True

            # Case 2: Active run exists but timed out - close and create new
            if active_run.is_timed_out(self._run_timeout_seconds):
                await self._close_run_and_notify(
                    active_run,
                    reason="timeout",
                    ended_at=timestamp,
                    notify_reason="timeout"
                )
                return await self._create_new_run(session_id, timestamp), True

            # Case 3: Active run exists and still valid - update and return
            active_run.last_seen_at = timestamp
            active_run.last_seen_at_mono = time.monotonic()  # Update monotonic timestamp
            self._logger.debug(
                f"Reusing active run {active_run.run_id} for session {session_id}"
            )
            return active_run.run_id, False

    async def close_run(
        self,
        run_id: str,
        reason: str = "explicit",
        ended_at: Optional[datetime] = None
    ) -> bool:
        """
        Explicitly close a run (for future API support).

        Args:
            run_id: The run ID to close
            reason: Reason for closure (default: "explicit")
            ended_at: Optional explicit end timestamp

        Returns:
            bool: True if run was closed, False if not found
        """
        async with self._lock:
            # Find the run by run_id
            for session_id, active_run in list(self._active_runs.items()):
                if active_run.run_id == run_id:
                    await self._close_run_and_notify(active_run, reason, ended_at)
                    return True

            self._logger.warning(f"Attempted to close non-existent run {run_id}")
            return False

    async def _create_new_run(
        self,
        session_id: str,
        timestamp: datetime
    ) -> str:
        """
        Internal method to create a new run.
        Must be called within lock context.
        """
        run_id = str(uuid.uuid4())
        now_mono = time.monotonic()

        active_run = ActiveRun(
            run_id=run_id,
            session_id=session_id,
            started_at=timestamp,
            last_seen_at=timestamp,
            started_at_mono=now_mono,
            last_seen_at_mono=now_mono
        )

        self._active_runs[session_id] = active_run

        self._logger.info(
            f"[RUN] Created new run {run_id} for session {session_id} "
            f"(timeout: {self._run_timeout_seconds}s)"
        )

        return run_id

    async def _close_run_and_notify(
        self,
        active_run: ActiveRun,
        reason: str,
        ended_at: Optional[datetime] = None,
        notify_reason: Optional[str] = None
    ):
        """
        Close a run and notify the backend if an observer is configured.
        Must be called within lock context.
        """
        if ended_at is None:
            ended_at = datetime.now(timezone.utc)

        await self._close_run_internal(active_run, reason, ended_at)

        if self._observer:
            try:
                await self._observer.notify_run_end(
                    run_id=active_run.run_id,
                    session_id=active_run.session_id,
                    ended_at=ended_at,
                    reason=notify_reason or reason
                )
            except Exception as notify_error:
                self._logger.warning(
                    f"Failed to notify backend of run end: {notify_error}"
                )

    async def _close_run_internal(
        self,
        active_run: ActiveRun,
        reason: str,
        ended_at: Optional[datetime] = None
    ):
        """
        Internal method to close a run.
        Must be called within lock context.
        """
        if ended_at is None:
            ended_at = datetime.now(timezone.utc)

        duration = (ended_at - active_run.started_at).total_seconds()

        # Remove from active runs
        del self._active_runs[active_run.session_id]
        self._closed_runs_count += 1

        self._logger.info(
            f"[RUN] Closed run {active_run.run_id} "
            f"(reason: {reason}, session: {active_run.session_id}, duration: {duration:.1f}s)"
        )

    def get_stats(self) -> Dict:
        """Get current RunManager statistics (for debugging/monitoring)."""
        return {
            "active_runs": len(self._active_runs),
            "closed_runs_total": self._closed_runs_count,
            "timeout_seconds": self._run_timeout_seconds
        }
