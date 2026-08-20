from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from local_llm_gateway.request_store import RequestCapture, RequestStore


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "test_review.db")


@pytest.fixture()
async def store(db_path: str) -> RequestStore:
    s = RequestStore(db_path=db_path, retention_seconds=1, max_pinned=3)
    await s.connect()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_add_and_get(store: RequestStore) -> None:
    cap = await store.add(
        public_model="ollama-llama3",
        backend_model="llama3",
        backend_name="ollama",
        prompt=json.dumps([{"role": "user", "content": "hello"}]),
        response=json.dumps(["Hi there!"]),
        temperature=0.7,
        thinking_effort="medium",
    )
    assert cap.id is not None
    assert cap.pinned is False

    fetched = await store.get(cap.id)
    assert fetched is not None
    assert fetched.public_model == "ollama-llama3"
    assert fetched.temperature == 0.7
    assert fetched.thinking_effort == "medium"


@pytest.mark.asyncio
async def test_get_missing(store: RequestStore) -> None:
    assert await store.get("nonexistent") is None


@pytest.mark.asyncio
async def test_list_captures(store: RequestStore) -> None:
    for i in range(5):
        await store.add(
            public_model=f"ollama-model{i}",
            backend_model=f"model{i}",
            backend_name="ollama",
            prompt="[]",
            response='["resp"]',
        )

    result = await store.list_captures(limit=3)
    assert len(result) == 3
    # Most recent first
    assert result[0].public_model == "ollama-model4"

    result = await store.list_captures(limit=10, offset=2)
    assert len(result) == 3
    assert result[0].public_model == "ollama-model2"


@pytest.mark.asyncio
async def test_list_with_model_filter(store: RequestStore) -> None:
    await store.add(
        public_model="ollama-llama3",
        backend_model="llama3",
        backend_name="ollama",
        prompt="[]",
        response='["a"]',
    )
    await store.add(
        public_model="vllm-mistral",
        backend_model="mistral",
        backend_name="vllm",
        prompt="[]",
        response='["b"]',
    )

    result = await store.list_captures(model="llama")
    assert len(result) == 1
    assert result[0].public_model == "ollama-llama3"


@pytest.mark.asyncio
async def test_pin_and_unpin(store: RequestStore) -> None:
    cap = await store.add(
        public_model="ollama-llama3",
        backend_model="llama3",
        backend_name="ollama",
        prompt="[]",
        response='["resp"]',
    )

    assert await store.pin(cap.id) is True
    fetched = await store.get(cap.id)
    assert fetched is not None
    assert fetched.pinned is True

    assert await store.unpin(cap.id) is True
    fetched = await store.get(cap.id)
    assert fetched is not None
    assert fetched.pinned is False


@pytest.mark.asyncio
async def test_pin_limit(store: RequestStore) -> None:
    ids = []
    for _ in range(3):
        cap = await store.add(
            public_model="ollama-llama3",
            backend_model="llama3",
            backend_name="ollama",
            prompt="[]",
            response='["resp"]',
        )
        ids.append(cap.id)

    # All 3 should be pinned (max_pinned=3)
    for iid in ids:
        assert await store.pin(iid) is True

    # 4th should fail
    cap4 = await store.add(
        public_model="ollama-llama3",
        backend_model="llama3",
        backend_name="ollama",
        prompt="[]",
        response='["resp"]',
    )
    assert await store.pin(cap4.id) is False


@pytest.mark.asyncio
async def test_delete(store: RequestStore) -> None:
    cap = await store.add(
        public_model="ollama-llama3",
        backend_model="llama3",
        backend_name="ollama",
        prompt="[]",
        response='["resp"]',
    )

    assert await store.delete(cap.id) is True
    assert await store.get(cap.id) is None
    assert await store.delete(cap.id) is False


@pytest.mark.asyncio
async def test_count_captures(store: RequestStore) -> None:
    assert await store.count_captures() == 0

    await store.add(
        public_model="ollama-llama3",
        backend_model="llama3",
        backend_name="ollama",
        prompt="[]",
        response='["resp"]',
    )
    await store.add(
        public_model="ollama-llama3",
        backend_model="llama3",
        backend_name="ollama",
        prompt="[]",
        response='["resp"]',
    )

    assert await store.count_captures() == 2


@pytest.mark.asyncio
async def test_prune_unpinned(store: RequestStore) -> None:
    # Store has retention_seconds=1
    cap = await store.add(
        public_model="ollama-llama3",
        backend_model="llama3",
        backend_name="ollama",
        prompt="[]",
        response='["resp"]',
    )

    # Manually backdate the capture
    async with store._db.execute(  # type: ignore[union-attr]
        "UPDATE captures SET timestamp = ? WHERE id = ?",
        (time.time() - 10, cap.id),
    ) as cursor:
        pass
    await store._db.commit()  # type: ignore[union-attr]

    # Prune
    await store._prune()

    assert await store.count_captures() == 0


@pytest.mark.asyncio
async def test_prune_keeps_pinned(store: RequestStore) -> None:
    cap = await store.add(
        public_model="ollama-llama3",
        backend_model="llama3",
        backend_name="ollama",
        prompt="[]",
        response='["resp"]',
    )
    await store.pin(cap.id)

    # Backdate
    async with store._db.execute(  # type: ignore[union-attr]
        "UPDATE captures SET timestamp = ? WHERE id = ?",
        (time.time() - 10, cap.id),
    ) as cursor:
        pass
    await store._db.commit()  # type: ignore[union-attr]

    await store._prune()

    assert await store.count_captures() == 1
    fetched = await store.get(cap.id)
    assert fetched is not None
    assert fetched.pinned is True


@pytest.mark.asyncio
async def test_list_pinned_only(store: RequestStore) -> None:
    cap1 = await store.add(
        public_model="ollama-llama3",
        backend_model="llama3",
        backend_name="ollama",
        prompt="[]",
        response='["resp"]',
    )
    cap2 = await store.add(
        public_model="ollama-mistral",
        backend_model="mistral",
        backend_name="ollama",
        prompt="[]",
        response='["resp"]',
    )
    await store.pin(cap1.id)

    all_caps = await store.list_captures(pinned_only=False)
    assert len(all_caps) == 2

    pinned = await store.list_captures(pinned_only=True)
    assert len(pinned) == 1
    assert pinned[0].id == cap1.id


@pytest.mark.asyncio
async def test_not_connected_raises() -> None:
    s = RequestStore(db_path="/tmp/never.db")
    with pytest.raises(RuntimeError, match="not connected"):
        await s.get("x")
