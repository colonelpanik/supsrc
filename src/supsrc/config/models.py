#
# config/models.py
#
"""
Attrs-based data models for supsrc configuration structure.
"""

import logging
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any, TypeAlias

from attrs import define, field, mutable


# --- Validators (can stay here or move to a validators module) ---
def _validate_log_level(inst: Any, attr: Any, value: str) -> None:
    """Validator for standard logging level names."""
    valid = logging._nameToLevel.keys()
    if value.upper() not in valid:
        raise ValueError(f"Invalid log_level '{value}'. Must be one of {list(valid)}.")


def _validate_positive_int(inst: Any, attr: Any, value: int) -> None:
    """Validator ensures integer is positive."""
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"Field '{attr.name}' must be positive integer, got {value}")


# --- attrs Data Classes for Rules ---
@define(slots=True)
class InactivityRuleConfig:
    """Configuration for the inactivity rule."""
    type: str = field(default="supsrc.rules.inactivity", kw_only=True)
    period: timedelta = field()


@define(slots=True)
class SaveCountRuleConfig:
    """Configuration for the save count rule."""
    type: str = field(default="supsrc.rules.save_count", kw_only=True)
    count: int = field(validator=_validate_positive_int)


@define(slots=True)
class ManualRuleConfig:
    """Configuration for the manual rule."""
    type: str = field(default="supsrc.rules.manual", kw_only=True)


RuleConfig: TypeAlias = InactivityRuleConfig | SaveCountRuleConfig | ManualRuleConfig


# --- New LLM Configuration Model ---
@define(frozen=True, slots=True)
class LLMConfig:
    """Configuration for optional LLM features."""
    enabled: bool = field(default=False)
    provider: str = field(default="gemini")
    model: str = field(default="gemini-1.5-flash")
    api_key_env_var: str | None = field(default="GEMINI_API_KEY")

    # Feature Flags
    generate_commit_message: bool = field(default=True)
    use_conventional_commit: bool = field(default=True)
    review_changes: bool = field(default=True)
    run_tests: bool = field(default=True)
    analyze_test_failures: bool = field(default=True)
    generate_change_fragment: bool = field(default=False)

    # Configurable settings
    test_command: str | None = field(default=None)
    change_fragment_dir: str | None = field(default="changes")


# --- Repository and Global Config Models ---
@mutable(slots=True)
class RepositoryConfig:
    """
    Configuration for a repository. Mutable to allow disabling on load if path invalid.
    """
    path: Path = field()
    rule: RuleConfig = field()
    repository: Mapping[str, Any] = field(factory=dict)
    enabled: bool = field(default=True)
    llm: LLMConfig | None = field(default=None)  # Added LLM config
    _path_valid: bool = field(default=True, repr=False, init=False)


@define(frozen=True, slots=True)
class GlobalConfig:
    """Global default settings for supsrc."""
    log_level: str = field(default="INFO", validator=_validate_log_level)

    @property
    def numeric_log_level(self) -> int:
        return logging.getLevelName(self.log_level.upper())


@define(frozen=True, slots=True)
class SupsrcConfig:
    """Root configuration object for the supsrc application."""
    repositories: dict[str, RepositoryConfig] = field(factory=dict)
    global_config: GlobalConfig = field(factory=GlobalConfig, metadata={"toml_name": "global"})


# 🔼⚙️
