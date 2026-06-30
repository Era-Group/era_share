# -*- coding: utf-8 -*-
"""Provider handlers, all implementing the uniform ProviderHandler contract in
base.py. Imported lazily by the engine — never at module load.

Dispatch is by the provider's ``handler_key`` (NOT provider_type): two providers
can share a provider_type yet need different handlers — e.g. an email FINDER
(Hunter) and an email VERIFIER (ZeroBounce) are both 'email'. :func:`get_handler`
maps handler_key -> class; a key with no handler yet (hunter / wathiq / maroof
land in later tasks) resolves to ``None`` and the engine simply skips that
provider, so the waterfall is fully functional with whatever handlers exist.
For back-compat, an empty handler_key falls back to provider_type.
"""

#: handler_key -> handler class, overriding the built-ins. Gives later tasks and
#: tests a seam to register/override a handler without editing the dispatch table:
#:     from .handlers import EXTRA_HANDLERS
#:     EXTRA_HANDLERS["hunter"] = HunterHandler
EXTRA_HANDLERS = {}


def _builtin_handlers():
    """Map of the handlers shipped so far (lazy import — never at package load)."""
    from . import whatsapp_waha, email_zerobounce, phone_hlr, websearch
    return {
        "waha": whatsapp_waha.WhatsAppWahaHandler,
        "zerobounce": email_zerobounce.EmailZeroBounceHandler,
        "hlr": phone_hlr.PhoneHlrHandler,
        "websearch": websearch.WebSearchHandler,
    }


def get_handler(env, provider):
    """Return an instantiated handler for ``provider``, or None if none exists.

    Keys on handler_key (empty -> provider_type fallback). EXTRA_HANDLERS wins
    over the built-ins.
    """
    table = dict(_builtin_handlers())
    table.update(EXTRA_HANDLERS)
    key = provider.handler_key or provider.provider_type
    cls = table.get(key)
    return cls(env, provider) if cls else None
