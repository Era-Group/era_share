# -*- coding: utf-8 -*-
import functools
import inspect
import logging


_logger = logging.getLogger(__name__)
_PATCH_FLAG = "__era_ws_terminate_guard_patched__"


def _is_serialization_failure(exc):
    # PostgreSQL serialization failure SQLSTATE.
    if getattr(exc, "pgcode", None) == "40001":
        return True
    return "could not serialize access due to concurrent update" in str(exc).lower()


def _patch_ws_terminate(ws_class):
    original = getattr(ws_class, "_terminate", None)
    if not callable(original) or getattr(original, _PATCH_FLAG, False):
        return False

    @functools.wraps(original)
    def _terminate_with_guard(self, *args, **kwargs):
        try:
            return original(self, *args, **kwargs)
        except Exception as exc:
            if _is_serialization_failure(exc):
                _logger.info(
                    "Ignored websocket terminate serialization failure (db=%s): %s",
                    getattr(self, "_db", None),
                    exc,
                )
                return None
            raise

    setattr(_terminate_with_guard, _PATCH_FLAG, True)
    ws_class._terminate = _terminate_with_guard
    return True


def post_load():
    """Monkey patch bus websocket terminate to tolerate presence update races."""
    try:
        from odoo.addons.bus import websocket as bus_websocket
    except Exception as exc:
        _logger.info("Unable to import bus.websocket, skip patch: %s", exc)
        return

    patched_count = 0
    for _, ws_class in inspect.getmembers(bus_websocket, inspect.isclass):
        if ws_class.__module__ != bus_websocket.__name__:
            continue
        if hasattr(ws_class, "_terminate") and hasattr(ws_class, "_disconnect"):
            if _patch_ws_terminate(ws_class):
                patched_count += 1

    if patched_count:
        _logger.info("Applied websocket serialization guard to %s class(es).", patched_count)
    else:
        _logger.info("No websocket class found for serialization guard patch.")
