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


class RobotTimeoutError(RobotTransportError):
    """The BMS request deadline expired before a robot response arrived."""

    def __init__(self, message: str, *, dispatched: bool) -> None:
        super().__init__(message)
        self.dispatched = dispatched
        self.dispatch_state = "outcome_ambiguous" if dispatched else "not_dispatched"
        self.caller_can_retry = not dispatched
        self.status_recovery = (
            "query_current_v2_dashboard_and_receipt_before_any_retry"
            if dispatched
            else "safe_to_retry_with_current_authority"
        )


class RobotResponseError(RobotTransportError):
    """The robot returned a non-success response."""

    def __init__(self, status_code: int, detail: object) -> None:
        super().__init__(f"BioXP robot returned HTTP {status_code}")
        self.status_code = status_code
        self.detail = detail
