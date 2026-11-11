"""typesafe-jev-delegation: registers the ``jev_routes`` delegation backend. Inert until
``delegation.backend: jev_routes`` is set in config.yaml."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    if not hasattr(ctx, "register_delegation_provider"):
        logger.warning("typesafe-jev-delegation needs a Hermes core with pluggable delegation providers.")
        return
    from .jev_routes import JevRoutesProvider
    ctx.register_delegation_provider(JevRoutesProvider())
