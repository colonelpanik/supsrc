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
    LlmConfig,
    ManualRuleConfig,
    RepositoryConfig,
    RuleConfig,  # Export the union type
    SaveCountRuleConfig,
    SupsrcConfig,
    TestingConfig,
)

__all__ = [
    "GlobalConfig",
    "InactivityRuleConfig",
    "LlmConfig",
    "ManualRuleConfig",
    "RepositoryConfig",
    "RuleConfig",
    "SaveCountRuleConfig",
    "SupsrcConfig",
    "TestingConfig",
    "load_config",
]

# 🔼⚙️
