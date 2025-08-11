#
# config/models.py
#
"""
Attrs-based data models for supsrc configuration structure.
"""

import logging  # Still needed for level names
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import (  # Added Mapping
    Any,
    TypeAlias,
)

from attrs import define, field, mutable

# --- Validators (can stay here or move to a validators module) ---

def _validate_log_level(inst: Any, attr: Any, value: str) -> None:
    """Validator for standard logging level names."""
    valid = logging._nameToLevel.keys()
    if value.upper() not in valid:
        # Note: Raising validation error here is fine, structlog logger isn't needed
        raise ValueError(f"Invalid log_level '{value}'. Must be one of {list(valid)}.")

def _validate_positive_int(inst: Any, attr: Any, value: int) -> None:
    """Validator ensures integer is positive."""
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"Field '{attr.name}' must be positive integer, got {value}")

# --- attrs Data Classes for Rules ---
# Define the structure for different rule types.
# The 'type' literal should match the key used for loading (e.g., built-in path or plugin key)

@define(slots=True)
class InactivityRuleConfig: # Renamed from InactivityTrigger for clarity as config object
    """Configuration for the inactivity rule."""
    # Type can be validated during plugin loading if needed, or assume correct here
    # Use kw_only=True so 'type' isn't required during cattrs structuring if hook handles it
    type: str = field(default="supsrc.rules.inactivity", kw_only=True)
    period: timedelta = field()

@define(slots=True)
class SaveCountRuleConfig: # Renamed from SaveCountTrigger
    """Configuration for the save count rule."""
    type: str = field(default="supsrc.rules.save_count", kw_only=True)
    count: int = field(validator=_validate_positive_int)

@define(slots=True)
class ManualRuleConfig: # Renamed from ManualTrigger
    """Configuration for the manual rule."""
    type: str = field(default="supsrc.rules.manual", kw_only=True)


# Type alias for the union of rule configuration types
# cattrs will use this union to structure the 'rule' section based on 'type' using the registered hook
RuleConfig: TypeAlias = InactivityRuleConfig | SaveCountRuleConfig | ManualRuleConfig

# --- LLM Configuration Model (NEW) ---
@define(frozen=True, slots=True)
class LlmConfig:
    """Configuration for an LLM provider."""
    provider: str = field()  # e.g., "ollama", "openai", "gemini"
    model: str = field()
    api_key: str | None = field(default=None) # For providers that need it
    base_url: str | None = field(default=None) # For self-hosted/proxy
    # Additional provider-specific options can be stored here
    options: Mapping[str, Any] = field(factory=dict)


# --- Repository and Global Config Models ---

@mutable(slots=True)
class RepositoryConfig:
    """
    Configuration for a repository. Mutable to allow disabling on load if path invalid.
    """
    # Mandatory fields first
    path: Path = field()
    # This field will hold the structured rule config object (e.g., InactivityRuleConfig)
    rule: RuleConfig = field() # Holds the specific structured rule config
    # Holds the raw dictionary from the TOML [repositories.*.repository] section for the engine to parse.
    repository: Mapping[str, Any] = field(factory=dict)

    # Optional fields after
    enabled: bool = field(default=True)
    # Optional LLM configuration for this specific repository (NEW)
    llm: LlmConfig | None = field(default=None)


    # Internal state flag
    _path_valid: bool = field(default=True, repr=False, init=False)


@define(frozen=True, slots=True)
class GlobalConfig:
    """Global default settings for supsrc."""
    log_level: str = field(default="INFO", validator=_validate_log_level)
    # Optional global LLM configuration (NEW)
    # FIX: Removed incorrect metadata from this nested field.
    llm: LlmConfig | None = field(default=None)

    @property
    def numeric_log_level(self) -> int:
        """Return the numeric logging level."""
        return logging.getLevelName(self.log_level.upper())

@define(frozen=True, slots=True)
class SupsrcConfig:
    """Root configuration object for the supsrc application."""
    repositories: dict[str, RepositoryConfig] = field(factory=dict)
    global_config: GlobalConfig = field(
        factory=GlobalConfig, metadata={"toml_name": "global"}
    )

# 🔼⚙️
