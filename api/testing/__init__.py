"""Test-only helpers that ship with the package but are never used in production.

Nothing here is imported on the normal startup path; it is pulled in only by the
pytest suite and by the ``KASA_FAKE_DEVICES`` seam (see ``fake_devices``), so
production behavior is unaffected when that flag is off.
"""
