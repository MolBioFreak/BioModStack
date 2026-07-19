"""Canonical BMS-side BioXP connection and command domain."""

from .command_coordinator import CommandCoordinator
from .command_models import CommandRequest, parse_command_request
from .command_registry import DEFAULT_COMMAND_REGISTRY
from .connection import BioXpConnectionService
from .errors import (
    BioXpError,
    ConnectionStateError,
    ProfileStoreError,
    RobotResponseError,
    RobotTransportError,
    TargetPolicyError,
)
from .models import (
    BioXpProfile,
    BioXpSnapshot,
    CommandRecord,
    ControlDecision,
    EmergencyStopResult,
)
from .profile_store import BioXpProfileStore
from .robot_client import BioXpRobotClient
from .target_policy import BioXpTargetPolicy, ValidatedBioXpTarget

__all__ = [
    "BioXpConnectionService",
    "BioXpError",
    "BioXpProfile",
    "BioXpProfileStore",
    "BioXpRobotClient",
    "BioXpSnapshot",
    "BioXpTargetPolicy",
    "CommandCoordinator",
    "CommandRecord",
    "CommandRequest",
    "ConnectionStateError",
    "ControlDecision",
    "DEFAULT_COMMAND_REGISTRY",
    "EmergencyStopResult",
    "ProfileStoreError",
    "RobotResponseError",
    "RobotTransportError",
    "TargetPolicyError",
    "ValidatedBioXpTarget",
    "parse_command_request",
]
