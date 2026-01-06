# Copyright 2026 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Power operation policies including circuit breaker and retry logic."""

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
import time
from typing import Optional


class FailureType(Enum):
    """Classification of power operation failures."""

    CONFIGURATION = "configuration"  # Configuration error (no retry needed)
    TRANSIENT = "transient"  # Transient error (retry possible)
    UNKNOWN = "unknown"  # Unknown error


@dataclass
class RetryPolicy:
    """Defines retry behavior for power operations."""

    max_attempts: int
    initial_delay: timedelta
    max_delay: timedelta
    backoff_factor: float


# Default retry policy
DEFAULT_RETRY_POLICY = RetryPolicy(
    max_attempts=3,
    initial_delay=timedelta(seconds=10),
    max_delay=timedelta(minutes=2),
    backoff_factor=2.0,
)


@dataclass
class PowerOperationResult:
    """Result of a power operation attempt."""

    success: bool
    failure_type: Optional[FailureType]
    error_message: str
    attempts: int
    should_retry: bool


class PowerOperationCircuitBreaker:
    """Circuit breaker for power operations.

    Prevents repeated failures by temporarily blocking operations
    after consecutive failures.
    """

    def __init__(self, failure_threshold=3, recovery_timeout=300):
        """Initialize the circuit breaker.

        :param failure_threshold: Number of failures before tripping
        :param recovery_timeout: Seconds to wait before allowing retry
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._failures = {}  # node_id -> failure_count
        self._last_failure = {}  # node_id -> timestamp

    def record_failure(self, node_id, failure_type):
        """Record a failure for a node.

        Configuration errors immediately trip the breaker.

        :param node_id: The node's ID
        :param failure_type: The type of failure
        """
        if failure_type == FailureType.CONFIGURATION:
            # Configuration errors immediately trip the breaker
            self._failures[node_id] = self.failure_threshold
        else:
            self._failures[node_id] = self._failures.get(node_id, 0) + 1
        self._last_failure[node_id] = time.time()

    def record_success(self, node_id):
        """Record a successful operation, resetting the failure count.

        :param node_id: The node's ID
        """
        self._failures.pop(node_id, None)
        self._last_failure.pop(node_id, None)

    def should_allow(self, node_id):
        """Check if an operation should be allowed for a node.

        :param node_id: The node's ID
        :return: True if operation should proceed, False otherwise
        """
        if node_id not in self._failures:
            return True
        if self._failures[node_id] < self.failure_threshold:
            return True
        # Check if recovery timeout has passed
        if time.time() - self._last_failure.get(node_id, 0) > self.recovery_timeout:
            self._failures[node_id] = 0
            return True
        return False

    def get_state(self, node_id):
        """Get the circuit breaker state for a node.

        :param node_id: The node's ID
        :return: "CLOSED" (allowing), "OPEN" (blocking), or "HALF_OPEN"
        """
        if self.should_allow(node_id):
            if node_id in self._failures and self._failures[node_id] > 0:
                return "HALF_OPEN"
            return "CLOSED"
        return "OPEN"

    def get_failure_count(self, node_id):
        """Get the current failure count for a node.

        :param node_id: The node's ID
        :return: Number of consecutive failures
        """
        return self._failures.get(node_id, 0)

    def reset(self, node_id=None):
        """Reset the circuit breaker state.

        :param node_id: If provided, reset only this node. Otherwise reset all.
        """
        if node_id is not None:
            self._failures.pop(node_id, None)
            self._last_failure.pop(node_id, None)
        else:
            self._failures.clear()
            self._last_failure.clear()


def classify_power_error(exception):
    """Classify a power operation error.

    :param exception: The exception that occurred
    :return: FailureType indicating the error category
    """
    error_msg = str(exception).lower()

    # Configuration errors (no point retrying)
    config_patterns = [
        "no rack controllers can access",
        "no bmc is defined",
        "unconfigured power type",
        "unknown power type",
        "power type not set",
        "no power type",
        "bmc is not accessible",
        "cannot start commissioning",
    ]
    for pattern in config_patterns:
        if pattern in error_msg:
            return FailureType.CONFIGURATION

    # Transient errors (worth retrying)
    transient_patterns = [
        "timeout",
        "connection refused",
        "connection reset",
        "network unreachable",
        "host unreachable",
        "temporary failure",
        "try again",
        "temporarily unavailable",
    ]
    for pattern in transient_patterns:
        if pattern in error_msg:
            return FailureType.TRANSIENT

    return FailureType.UNKNOWN
