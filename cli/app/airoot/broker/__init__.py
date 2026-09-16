"""P2's protected broker — the wire layer and its first decision, and nothing else (ADR-0025 D1, ADR-0026, ADR-0041).

`broker-request` and `broker-response` were published with no writer; `protocol` is the writer and the
reader. `policy` is the one decision the service makes before it does anything else: who is asking, and
may they ask at all — taken over the **observed** caller token, never over the request's `client` block,
which is a claim (draft §114, ADR-0041). The name stays deliberate: `airoot.broker` is the *conversation
and its admission decision*, not the service. There is still no elevated process, no named pipe, no
observation of any caller and no trust boundary here — P2's service is expected to import these modules
rather than describe the wire or the decision a second time.
"""

from __future__ import annotations

from .policy import (
    INTEGRITY_LADDER,
    REFUSAL_REASONS,
    REQUIRED_FACTS,
    CallerExpectation,
    admit_caller,
    required_facts,
)
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
from .transport import (
    FRAME_HEADER_BYTES,
    MAX_FRAME_BYTES,
    decode_frame,
    encode_frame,
    read_frame,
)

__all__ = [
    "ADDITIONAL_REQUIREMENTS",
    "ADDITIONAL_REQUIREMENT_REASONS",
    "FRAME_HEADER_BYTES",
    "INTEGRITY_LADDER",
    "MAX_FRAME_BYTES",
    "OPERATIONS",
    "PROTOCOL_VERSION",
    "REFUSAL_REASONS",
    "REQUIRED_BY_OPERATION",
    "REQUIRED_FACTS",
    "CallerExpectation",
    "admit_caller",
    "broker_unavailable",
    "build_request",
    "decode_frame",
    "encode_frame",
    "parse_response",
    "read_frame",
    "required_facts",
    "required_fields",
]
