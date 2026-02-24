from __future__ import annotations

from fastapi import APIRouter

from zammad_pdf_archiver.app.jobs.shutdown import is_shutting_down
from zammad_pdf_archiver.app.jobs.ticket_stores import is_ticket_in_flight

router = APIRouter()


@router.get("/jobs/{ticket_id}")
async def get_job_status(ticket_id: int) -> dict[str, bool | int]:
    return {
        "ticket_id": ticket_id,
        "in_flight": is_ticket_in_flight(ticket_id),
        "shutting_down": is_shutting_down(),
    }
