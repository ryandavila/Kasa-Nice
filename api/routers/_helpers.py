from pydantic import ValidationError

from ..logging_config import get_logger

logger = get_logger(__name__)


def _validated_rows(rows: list[dict], model: type, noun: str) -> list:
    """Validate stored rows through ``model``, skipping (and warning on) bad ones.

    The JSON stores tolerate hand-edited/older files by design, so one invalid
    row must degrade to a warning — not 500 the whole collection for every
    client. Writers still validate up front; this is the reader's backstop.
    """
    out = []
    for row in rows:
        try:
            out.append(model(**row))
        except ValidationError as e:
            logger.warning(f"Skipping invalid stored {noun} {row.get('id')!r}: {e}")
    return out
