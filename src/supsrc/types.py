#
# supsrc/types.py
#
"""Common type definitions to avoid circular imports."""

from typing import TypeAlias

from supsrc.state import RepositoryState

# Type alias for repository states mapping
RepositoryStatesMap: TypeAlias = dict[str, RepositoryState]


# 🔼⚙️
