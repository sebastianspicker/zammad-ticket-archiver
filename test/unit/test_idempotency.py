from __future__ import annotations

from zammad_pdf_archiver.domain.idempotency import InMemoryTTLSet


def test_ttl_expiry() -> None:
    now_value = 1000.0

    def now() -> float:
        return now_value

    ttl = InMemoryTTLSet(ttl_seconds=5.0, now=now)

    assert ttl.seen("abc") is False
    ttl.add("abc")
    assert ttl.seen("abc") is True

    now_value = 1004.999
    assert ttl.seen("abc") is True

    now_value = 1005.0
    assert ttl.seen("abc") is False

    ttl.add("abc")
    assert ttl.seen("abc") is True


def test_add_triggers_eviction_of_expired_keys() -> None:
    now_value = 0.0

    def now() -> float:
        return now_value

    ttl = InMemoryTTLSet(ttl_seconds=1.0, now=now)

    for idx in range(200):
        ttl.add(f"k{idx}")

    assert len(ttl) == 200

    now_value = 2.0
    ttl.add("fresh")

    assert len(ttl) == 1
