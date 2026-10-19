"""Expected boundary failures, distinct from unexpected programming errors."""


class TransitStressError(ValueError):
    """Expected failure with a field, reason and corrective action."""


class ConfigurationError(TransitStressError):
    """Unsupported or malformed protocol configuration."""


class DataValidationError(TransitStressError):
    """Invalid scientific input or contract."""


class IdentityError(TransitStressError):
    """Content cannot be represented by the canonical identity format."""
