#
# supsrc/exceptions.py
#
"""
Custom Exception types for the supsrc application.

Provides a hierarchy of specific exceptions for better error handling and reporting.
"""

# --- Base Exception ---

class SupsrcError(Exception):
    """Base class for all supsrc application specific errors."""
    pass

# --- Configuration Related Exceptions ---

class ConfigurationError(SupsrcError):
    """Base class for configuration file loading, parsing, or validation errors."""
    def __init__(self, message: str, path: str | None = None):
        self.path = path
        full_message = f"{message}"
        if path:
            full_message += f" (File: '{path}')"
        super().__init__(full_message)

class ConfigFileNotFoundError(ConfigurationError, FileNotFoundError):
    """Raised when the specified configuration file cannot be found."""
    def __init__(self, message: str = "Configuration file not found", path: str | None = None):
        super().__init__(message, path=path)


class ConfigParsingError(ConfigurationError):
    """Raised when the configuration file has invalid syntax (e.g., invalid TOML)."""
    def __init__(self, message: str, path: str | None = None, details: Exception | None = None):
        self.details = details # Store original parsing exception if available
        full_message = f"Failed to parse configuration file: {message}"
        super().__init__(full_message, path=path)
        if details and hasattr(self, "add_note"):
             self.add_note(f"Original parsing error: {type(details).__name__}: {details}")

class ConfigValidationError(ConfigurationError):
    """
    Raised when the configuration file content is syntactically valid
    but fails semantic validation (e.g., missing required fields, invalid values).
    """
    def __init__(self, message: str, path: str | None = None, details: Exception | None = None):
        self.details = details # Store original validation exception if available
        full_message = f"Configuration validation failed: {message}"
        super().__init__(full_message, path=path)
        if details and hasattr(self, "add_note"):
            self.add_note(f"Original validation error: {type(details).__name__}: {details}")


class PathValidationError(ConfigValidationError):
    """Raised for specific errors during path validation (existence, type)."""
    def __init__(self, message: str, path_value: str, config_path: str | None = None):
        self.path_value = path_value
        full_message = f"{message}: '{path_value}'"
        super().__init__(full_message, path=config_path)

class DurationValidationError(ConfigValidationError):
    """Raised for specific errors during duration string parsing or validation."""
    def __init__(self, message: str, duration_str: str, config_path: str | None = None):
        self.duration_str = duration_str
        full_message = f"{message}: '{duration_str}'"
        super().__init__(full_message, path=config_path)

class MonitoringError(SupsrcError):
    """Base class for errors related to file system monitoring."""
    def __init__(self, message: str, repo_id: str | None = None, path: str | None = None):
        self.repo_id = repo_id
        self.path = path
        full_message = f"{message}"
        if repo_id:
            full_message += f" (Repo: {repo_id})"
        if path:
            full_message += f" (Path: '{path}')"
        super().__init__(full_message)

class MonitoringSetupError(MonitoringError):
    """Raised when setting up monitoring for a specific repository fails."""
    pass

# 🔼⚙️
