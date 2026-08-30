"""Verify the pure ticket identifier contract."""

from chronikwerk.operations.job import extract_ticket_id


def test_ticket_ids_reject_boolean_zero_and_ambiguous_values() -> None:
    assert extract_ticket_id({"ticket_id": " 42 "}) == 42
    assert extract_ticket_id({"ticket": {"id": "+7"}}) == 7
    assert extract_ticket_id({"ticket_id": True, "ticket": {"id": 9}}) == 9
    assert extract_ticket_id({"ticket_id": 0}) is None
    assert extract_ticket_id({"ticket": "4.2"}) is None
