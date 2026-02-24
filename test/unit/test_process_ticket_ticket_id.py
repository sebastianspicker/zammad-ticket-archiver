from __future__ import annotations

import pytest

from zammad_pdf_archiver.domain.ticket_id import extract_ticket_id as _extract_ticket_id


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"ticket": {"id": 123}}, 123),
        ({"ticket": {"id": "123"}}, 123),
        ({"ticket_id": 456}, 456),
        ({"ticket_id": "456"}, 456),
    ],
)
def test_extract_ticket_id_accepts_integer_values(
    payload: dict[str, object], expected: int
) -> None:
    assert _extract_ticket_id(payload) == expected


@pytest.mark.parametrize(
    "payload",
    [
        {"ticket": {"id": True}},
        {"ticket": {"id": False}},
        {"ticket": {"id": 0}},
        {"ticket": {"id": -1}},
        {"ticket": {"id": 1.5}},
        {"ticket_id": True},
        {"ticket_id": 0},
        {"ticket_id": "0"},
        {"ticket_id": "-1"},
        {"ticket_id": 1.5},
        {"ticket_id": "not-a-number"},
    ],
)
def test_extract_ticket_id_rejects_non_integer_values(payload: dict[str, object]) -> None:
    assert _extract_ticket_id(payload) is None
