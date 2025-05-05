#
# engines/git/__init__.py
#
"""
Git Engine implementation for supsrc.
"""

from .base import GitEngine
from .info import GitRepoSummary

__all__ = ["GitEngine", "GitRepoSummary"]

# 🔼⚙️
