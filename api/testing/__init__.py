"""Test-only helpers, never on the normal startup path.

Pulled in only by the pytest suite and the ``KASA_FAKE_DEVICES`` seam (see
``fake_devices``), so production is unaffected when that flag is off.
"""
