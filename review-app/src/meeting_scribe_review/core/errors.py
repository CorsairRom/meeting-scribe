"""Failures of a service call and the user message for each (AC-15); never quotes a name."""

from dataclasses import dataclass

from meeting_scribe_review.core.constants import ERROR_PREFIX

SERVICE_NOT_RUNNING = "SERVICE_NOT_RUNNING"
SERVICE_OUTDATED = "SERVICE_OUTDATED"
INVALID_REPLY = "INVALID_REPLY"
UNKNOWN_ERROR = "UNKNOWN_ERROR"

_DBUS_PREFIX = "org.freedesktop.DBus.Error."
_NOT_RUNNING_NAMES = frozenset({"ServiceUnknown", "NameHasNoOwner", "NoServer"})
START_COMMAND = "systemctl --user start meeting-scribe.service"


@dataclass(frozen=True, slots=True)
class ServiceError:
    """A failed call. ``code`` is a service error code or one of this module's local codes."""

    code: str


@dataclass(frozen=True, slots=True)
class UserMessage:
    text: str
    hint: str = ""


def error_from_dbus_name(name: str) -> ServiceError:
    """Map a D-Bus error name to a ``ServiceError``; the name is never kept when it is foreign."""
    if name.startswith(f"{ERROR_PREFIX}."):
        return ServiceError(name.removeprefix(f"{ERROR_PREFIX}."))
    if name.startswith(_DBUS_PREFIX):
        tail = name.removeprefix(_DBUS_PREFIX)
        if tail == "UnknownMethod":
            return ServiceError(SERVICE_OUTDATED)
        if tail in _NOT_RUNNING_NAMES or tail.startswith("Spawn."):
            return ServiceError(SERVICE_NOT_RUNNING)
    return ServiceError(UNKNOWN_ERROR)


_MESSAGES: dict[str, UserMessage] = {
    SERVICE_NOT_RUNNING: UserMessage("The Meeting Scribe service is not running", START_COMMAND),
    SERVICE_OUTDATED: UserMessage(
        "The service must be restarted to match this app version",
        "systemctl --user restart meeting-scribe.service",
    ),
    INVALID_REPLY: UserMessage("The service sent an answer this app does not understand"),
    "ENTITY_NOT_FOUND_MEETING": UserMessage("This meeting no longer exists."),
    "ENTITY_NOT_FOUND_SPEAKER": UserMessage(
        "This speaker no longer exists. The list was refreshed."
    ),
    "ENTITY_NOT_FOUND_SPEAKER_SAMPLE": UserMessage("No sample"),
    "ENTITY_NOT_FOUND_TRANSCRIPT": UserMessage("This meeting has no transcript. Reprocess it."),
    "MEETING_NOT_PROCESSED": UserMessage("This meeting is not processed yet."),
    "MEETING_SCHEMA_UNSUPPORTED": UserMessage(
        "This meeting was saved by a newer version of Meeting Scribe."
    ),
    "MEETING_CORRUPT": UserMessage("This meeting's transcript is damaged. Reprocess it."),
    "MEETING_STORAGE_FAILED": UserMessage("The meeting files could not be read or written."),
    "INVALID_VALUE": UserMessage("That name is not valid."),
    "SPEAKER_NAME_TAKEN": UserMessage("That name is already used by another speaker."),
    "SPEAKER_MERGE_INVALID": UserMessage("These speakers cannot be merged."),
}


def message_for(error: ServiceError) -> UserMessage:
    """The message for a code; an unmapped code gets a generic message that names the code."""
    known = _MESSAGES.get(error.code)
    if known is not None:
        return known
    return UserMessage(f"Something went wrong ({error.code}).")
