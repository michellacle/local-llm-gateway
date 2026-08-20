from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite


@dataclass
class RequestCapture:
    id: str
    timestamp: float
    public_model: str
    backend_model: str
    backend_name: str
    prompt: str  # JSON-serialized messages array
    response: str  # JSON-serialized response text/choices
    temperature: float | None = None
    thinking_effort: str | None = None
    pinned: bool = False


class RequestStore:
    """SQLite-backed store for request captures with time-based expiry and pinning."""

    def __init__(
        self,
        db_path: str = "request_review.db",
        retention_seconds: float = 3600,
        max_pinned: int = 500,
    ) -> None:
        self.db_path = db_path
        self.retention_seconds = retention_seconds
        self.max_pinned = max_pinned
        self._db: aiosqlite.Connection | None = None
        self._prune_task: asyncio.Task | None = None

    async def connect(self) -> None:
        """Initialize the database connection and schema."""
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS captures (
                id TEXT PRIMARY KEY,
                timestamp REAL NOT NULL,
                public_model TEXT NOT NULL,
                backend_model TEXT NOT NULL,
                backend_name TEXT NOT NULL,
                prompt TEXT NOT NULL,
                response TEXT NOT NULL,
                temperature REAL,
                thinking_effort TEXT,
                pinned INTEGER NOT NULL DEFAULT 0
            )
        """)
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_captures_timestamp
            ON captures(timestamp)
        """)
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_captures_pinned
            ON captures(pinned)
        """)
        await self._db.commit()
        # Start background pruning
        if self._prune_task is None or self._prune_task.done():
            self._prune_task = asyncio.create_task(self._prune_loop())

    async def close(self) -> None:
        """Close the database connection and cancel background tasks."""
        if self._prune_task is not None:
            self._prune_task.cancel()
            try:
                await self._prune_task
            except asyncio.CancelledError:
                pass
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def add(
        self,
        public_model: str,
        backend_model: str,
        backend_name: str,
        prompt: str,
        response: str,
        temperature: float | None = None,
        thinking_effort: str | None = None,
    ) -> RequestCapture:
        """Add a new request capture."""
        if self._db is None:
            raise RuntimeError("RequestStore not connected")

        capture_id = str(uuid.uuid4())
        timestamp = time.time()

        await self._db.execute(
            """
            INSERT INTO captures (id, timestamp, public_model, backend_model, backend_name,
                                  prompt, response, temperature, thinking_effort, pinned)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                capture_id,
                timestamp,
                public_model,
                backend_model,
                backend_name,
                prompt,
                response,
                temperature,
                thinking_effort,
            ),
        )
        await self._db.commit()

        return RequestCapture(
            id=capture_id,
            timestamp=timestamp,
            public_model=public_model,
            backend_model=backend_model,
            backend_name=backend_name,
            prompt=prompt,
            response=response,
            temperature=temperature,
            thinking_effort=thinking_effort,
            pinned=False,
        )

    async def get(self, capture_id: str) -> RequestCapture | None:
        """Get a single capture by ID."""
        if self._db is None:
            raise RuntimeError("RequestStore not connected")

        async with self._db.execute(
            "SELECT * FROM captures WHERE id = ?", (capture_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return self._row_to_capture(row)

    async def list_captures(
        self,
        limit: int = 100,
        offset: int = 0,
        pinned_only: bool = False,
        model: str | None = None,
    ) -> list[RequestCapture]:
        """List captures with optional filtering."""
        if self._db is None:
            raise RuntimeError("RequestStore not connected")

        clauses: list[str] = []
        params: list[Any] = []

        if pinned_only:
            clauses.append("pinned = 1")
        if model:
            clauses.append("public_model LIKE ?")
            params.append(f"%{model}%")

        where = ""
        if clauses:
            where = f"WHERE {' AND '.join(clauses)}"

        query = f"SELECT * FROM captures {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        async with self._db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_capture(row) for row in rows]

    async def count_captures(self, pinned_only: bool = False) -> int:
        """Count total captures, optionally pinned only."""
        if self._db is None:
            raise RuntimeError("RequestStore not connected")

        where = "WHERE pinned = 1" if pinned_only else ""
        async with self._db.execute(f"SELECT COUNT(*) FROM captures {where}") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def pin(self, capture_id: str) -> bool:
        """Pin a capture to prevent expiry."""
        if self._db is None:
            raise RuntimeError("RequestStore not connected")

        # Check if we're at the pinned limit
        async with self._db.execute(
            "SELECT COUNT(*) FROM captures WHERE pinned = 1"
        ) as cursor:
            row = await cursor.fetchone()
            count = row[0] if row else 0

        if count >= self.max_pinned:
            return False

        result = await self._db.execute(
            "UPDATE captures SET pinned = 1 WHERE id = ? AND pinned = 0",
            (capture_id,),
        )
        await self._db.commit()
        return result.rowcount > 0

    async def unpin(self, capture_id: str) -> bool:
        """Unpin a capture, making it eligible for expiry."""
        if self._db is None:
            raise RuntimeError("RequestStore not connected")

        result = await self._db.execute(
            "UPDATE captures SET pinned = 0 WHERE id = ? AND pinned = 1",
            (capture_id,),
        )
        await self._db.commit()
        return result.rowcount > 0

    async def delete(self, capture_id: str) -> bool:
        """Delete a capture permanently."""
        if self._db is None:
            raise RuntimeError("RequestStore not connected")

        result = await self._db.execute(
            "DELETE FROM captures WHERE id = ?",
            (capture_id,),
        )
        await self._db.commit()
        return result.rowcount > 0

    async def _prune_loop(self) -> None:
        """Background task to prune expired unpinned captures."""
        try:
            while True:
                await asyncio.sleep(self.retention_seconds / 2)
                await self._prune()
        except asyncio.CancelledError:
            return

    async def _prune(self) -> None:
        """Remove unpinned captures older than retention_seconds."""
        if self._db is None:
            return

        cutoff = time.time() - self.retention_seconds
        await self._db.execute(
            "DELETE FROM captures WHERE pinned = 0 AND timestamp < ?",
            (cutoff,),
        )
        await self._db.commit()

    @staticmethod
    def _row_to_capture(row: aiosqlite.Row) -> RequestCapture:
        return RequestCapture(
            id=row["id"],
            timestamp=row["timestamp"],
            public_model=row["public_model"],
            backend_model=row["backend_model"],
            backend_name=row["backend_name"],
            prompt=row["prompt"],
            response=row["response"],
            temperature=row["temperature"],
            thinking_effort=row["thinking_effort"],
            pinned=bool(row["pinned"]),
        )
