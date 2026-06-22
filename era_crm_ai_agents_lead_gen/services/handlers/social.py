# -*- coding: utf-8 -*-
"""social handler — LinkedIn, intentionally a flagged STUB.

LinkedIn is the hardest and most ToS-restricted source (overview: "last priority
or deferred"). The provider row exists and sits last in the waterfall, but no
fetch is implemented: this handler raises NotImplementedError, which the engine
catches and logs as a graceful skip — the module is NEVER blocked on it. When a
compliant LinkedIn integration is approved, implement ``fetch`` here.
"""
from .base import BaseHandler, register


@register("social")
class SocialHandler(BaseHandler):
    provider_type = "social"

    def fetch(self, engine, provider, target):
        # Declared stub: do not implement an uncompliant scrape. The engine turns
        # this into a logged skip and moves on.
        raise NotImplementedError(
            "LinkedIn (social) fetching is not implemented (ToS-restricted, "
            "deferred). Provider %r is flagged, not active by default." % provider.name
        )
