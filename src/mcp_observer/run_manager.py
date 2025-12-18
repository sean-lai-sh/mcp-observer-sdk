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
from datetime import datetime
from dataclasses import dataclass


@dataclass
class ActiveRun:
    """In-memory state for an active run."""
    run_id: str
    session_id: str
    started_at: datetime
    last_seen_at: datetime

    def is_timed_out(self, timeout_seconds: float) -> bool:
        """Check if this run has exceeded the inactivity timeout."""
        elapsed = time.time() - self.last_seen_at.timestamp()
        return elapsed > timeout_seconds


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
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize RunManager.

        Args:
            run_timeout_seconds: Inactivity timeout in seconds (default 30s)
            logger: Optional logger for run lifecycle events
        """
        self._run_timeout_seconds = run_timeout_seconds
        self._logger = logger or logging.getLogger(__name__)

        # In-memory map: session_id -> ActiveRun
        # Only one active run per session at a time
        self._active_runs: Dict[str, ActiveRun] = {}

        # Async lock for thread-safe concurrent access
        self._lock = asyncio.Lock()

        # Track closed runs for logging/debugging
        self._closed_runs_count = 0

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
        async with self._lock:
            active_run = self._active_runs.get(session_id)

            # Case 1: No active run exists - create new one
            if active_run is None:
                return await self._create_new_run(session_id, timestamp), True

            # Case 2: Active run exists but timed out - close and create new
            if active_run.is_timed_out(self._run_timeout_seconds):
                await self._close_run_internal(
                    active_run,
                    reason="timeout",
                    ended_at=timestamp
                )
                return await self._create_new_run(session_id, timestamp), True

            # Case 3: Active run exists and still valid - update and return
            active_run.last_seen_at = timestamp
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
                    await self._close_run_internal(active_run, reason, ended_at)
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

        active_run = ActiveRun(
            run_id=run_id,
            session_id=session_id,
            started_at=timestamp,
            last_seen_at=timestamp
        )

        self._active_runs[session_id] = active_run

        self._logger.info(
            f"[RUN] Created new run {run_id} for session {session_id} "
            f"(timeout: {self._run_timeout_seconds}s)"
        )

        return run_id

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
            ended_at = datetime.now()

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
