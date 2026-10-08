"""Typed errors for the BMS-owned BioXP control-plane boundary."""


class BioXpError(RuntimeError):
    """Base class for safe, operator-facing BioXP failures."""


class TargetPolicyError(BioXpError):
    """The configured robot target violates the explicit allowlist policy."""


class ProfileStoreError(BioXpError):
    """The canonical BioXP profile could not be read or written safely."""


class ConnectionStateError(BioXpError):
    """A connection transition could not be completed."""


class RobotTransportError(BioXpError):
    """The validated robot transport failed without exposing internal details."""


class RobotResponseError(RobotTransportError):
    """The robot returned a non-success response."""

    def __init__(self, status_code: int, detail: object) -> None:
        super().__init__(f"BioXP robot returned HTTP {status_code}")
        self.status_code = status_code
        self.detail = detail
