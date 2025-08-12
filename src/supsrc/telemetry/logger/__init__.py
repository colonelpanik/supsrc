#
# supsrc/telemetry/logger/__init__.py
# -*- coding: utf-8 -*-
"""
Logging setup for supsrc using structlog.
"""

from supsrc.telemetry.logger.base import (  # Expose setup and type hint
    StructLogger,
    setup_logging,
)

__all__ = ["StructLogger", "setup_logging"]


# 🔼⚙️
