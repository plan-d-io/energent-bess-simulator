"""Configuration validation failures."""


class ConfigError(ValueError):
    """Invalid TOML, CLI, or resolved simulation configuration."""
