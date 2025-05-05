#
# config/__init__.py
#
"""
Configuration handling sub-package for supsrc.

Exports the loading function and core configuration model.
"""

# Export the main loading function from the loader module
from .loader import load_config

# Export the core configuration models from the models module
from .models import (
    GlobalConfig,
    InactivityRuleConfig,  # Export specific rule types if needed externally
    ManualRuleConfig,
    RepositoryConfig,
    RuleConfig,  # Export the union type
    SaveCountRuleConfig,
    SupsrcConfig,
)

__all__ = [
    "GlobalConfig",
    "InactivityRuleConfig",
    "ManualRuleConfig",
    "RepositoryConfig",
    "RuleConfig",
    "SaveCountRuleConfig",
    "SupsrcConfig",
    "load_config",
]

# 🔼⚙️
