import datetime

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from ..config import get_settings
from ..schedule_store import schedules
from ..schemas import Schedule, ScheduleCreate, ScheduleUpdate
from ._helpers import _validated_rows

router = APIRouter(prefix="/api")


@router.get("/schedules", response_model=list[Schedule])
async def list_schedules() -> list[Schedule]:
    return _validated_rows(schedules.list_rules(), Schedule, "schedule")


def _reject_unfireable_rule(kind: str, at: str | None) -> None:
    """422 for a rule that would persist but could never (or never again) fire.

    Shared by create and update so a PATCH can't sneak in what a POST rejects: a
    sunrise/sunset rule with no configured location silently never fires, and a
    one-shot whose ``at`` is already in the past would only ever be marked
    "missed" by the scheduler.
    """
    if kind in ("sunrise", "sunset") and get_settings().location is None:
        raise HTTPException(
            status_code=422,
            detail="Sunrise/sunset schedules require a server location; set "
            "KASA_LATITUDE and KASA_LONGITUDE.",
        )
    if kind == "once" and at is not None:
        # ``at`` is schema-validated as a naive local datetime; compare at
        # minute granularity so "this minute" still counts as future.
        target = datetime.datetime.fromisoformat(at)
        if target < datetime.datetime.now().replace(second=0, microsecond=0):
            raise HTTPException(
                status_code=422,
                detail=f"'at' is in the past: {at}",
            )


@router.post("/schedules", response_model=Schedule, status_code=201)
async def create_schedule(req: ScheduleCreate) -> Schedule:
    # ``req`` is pydantic-validated for shape; additionally reject a rule that
    # could never fire, with a clear message rather than persisting it.
    _reject_unfireable_rule(req.kind, req.at)
    # The store stamps id and null ``last_fired``. The scheduler picks it up on
    # its next minute tick — no restart needed.
    return Schedule(**schedules.create_rule(req.model_dump()))


@router.patch("/schedules/{schedule_id}", response_model=Schedule)
async def update_schedule(schedule_id: str, req: ScheduleUpdate) -> Schedule:
    # exclude_unset => true partial update: only sent fields are written, so an
    # enable/disable toggle leaves time/days/target untouched.
    existing = schedules.get_rule(schedule_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Unknown schedule: {schedule_id}")
    fields = req.model_dump(exclude_unset=True)
    # Validate the merged result BEFORE persisting: an incoherent rule (e.g.
    # kind=once with no 'at') must never reach the file — every rule in it is
    # re-validated on each GET, so one bad write would 500 the whole endpoint.
    merged = {**existing, **{k: v for k, v in fields.items() if k != "id"}}
    try:
        candidate = Schedule(**merged)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    # Only re-check fireability when the patch touches what it depends on, so a
    # plain enable/disable toggle of an old one-shot doesn't 422 on its past 'at'.
    if "kind" in fields or "at" in fields:
        _reject_unfireable_rule(candidate.kind, candidate.at)
    updated = schedules.update_rule(schedule_id, fields)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Unknown schedule: {schedule_id}")
    return Schedule(**updated)


@router.delete("/schedules/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: str) -> None:
    if not schedules.delete_rule(schedule_id):
        raise HTTPException(status_code=404, detail=f"Unknown schedule: {schedule_id}")
