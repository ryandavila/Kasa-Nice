from fastapi import APIRouter

from ..alerts import alert_center, alert_thresholds
from ..schemas import Alert, AlertThresholds

router = APIRouter(prefix="/api")


@router.get("/alerts/recent", response_model=list[Alert])
async def recent_alerts() -> list[Alert]:
    """The in-memory ring buffer of recent alerts, newest first.

    Not persisted across restarts in v1, so an empty list is normal right after a
    restart even if incidents occurred before it.
    """
    return alert_center.recent()


@router.get("/alerts/thresholds", response_model=AlertThresholds)
async def get_alert_thresholds() -> AlertThresholds:
    return AlertThresholds(thresholds=alert_thresholds.get_all())


@router.put("/alerts/thresholds", response_model=AlertThresholds)
async def set_alert_thresholds(req: AlertThresholds) -> AlertThresholds:
    """Full replace of the per-device wattage thresholds (mirrors ``PUT /favorites``)."""
    return AlertThresholds(thresholds=alert_thresholds.set_all(req.thresholds))
