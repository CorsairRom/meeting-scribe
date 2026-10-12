"""An opaque session id for one summary job (sent to gateways that require session affinity)."""

import hashlib
import uuid

SESSION_ID_LENGTH = 32


def new_session_id() -> str:
    """A hash of a random UUID: never derived from a meeting id, a name or any content."""
    return hashlib.sha256(uuid.uuid4().bytes).hexdigest()[:SESSION_ID_LENGTH]
