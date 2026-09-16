"""P2's protected broker — the protocol layer, and nothing else (ADR-0025 D1, ADR-0026).

`broker-request` and `broker-response` were published with no writer; this package holds the writer
and the reader. The name is deliberate: `airoot.broker` is the *conversation*, not the service. There
is no elevated process, no named pipe, no client authentication and no trust boundary here — P2's
service is expected to import this module rather than describe the wire a second time.
"""

from __future__ import annotations

from .protocol import (
    ADDITIONAL_REQUIREMENT_REASONS,
    ADDITIONAL_REQUIREMENTS,
    OPERATIONS,
    PROTOCOL_VERSION,
    REQUIRED_BY_OPERATION,
    broker_unavailable,
    build_request,
    parse_response,
    required_fields,
)

__all__ = [
    "ADDITIONAL_REQUIREMENTS",
    "ADDITIONAL_REQUIREMENT_REASONS",
    "OPERATIONS",
    "PROTOCOL_VERSION",
    "REQUIRED_BY_OPERATION",
    "broker_unavailable",
    "build_request",
    "parse_response",
    "required_fields",
]
