"""Failures of a service call and the user message for each (AC-15); never quotes a name."""

from dataclasses import dataclass

from meeting_scribe_review.core.constants import ERROR_PREFIX

SERVICE_NOT_RUNNING = "SERVICE_NOT_RUNNING"
SERVICE_OUTDATED = "SERVICE_OUTDATED"
INVALID_REPLY = "INVALID_REPLY"
UNKNOWN_ERROR = "UNKNOWN_ERROR"
KEYRING_UNAVAILABLE = "KEYRING_UNAVAILABLE"
KEYRING_STORE_FAILED = "KEYRING_STORE_FAILED"
KEYRING_REMOVE_FAILED = "KEYRING_REMOVE_FAILED"
RESTART_NOT_DONE = "RESTART_NOT_DONE"  # local: the restart was accepted but never happened

_DBUS_PREFIX = "org.freedesktop.DBus.Error."
_NOT_RUNNING_NAMES = frozenset({"ServiceUnknown", "NameHasNoOwner", "NoServer"})
RENAME_CALL = "rename_speaker"
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
    KEYRING_UNAVAILABLE: UserMessage(
        "The keyring service is not reachable. Check that gnome-keyring is running."
    ),
    KEYRING_STORE_FAILED: UserMessage(
        "The key could not be stored in the keyring. Unlock the login keyring and try again."
    ),
    KEYRING_REMOVE_FAILED: UserMessage(
        "The key could not be removed from the keyring. Unlock the login keyring and try again."
    ),
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
    "INVALID_VALUE": UserMessage("The request was not valid."),
    "SPEAKER_NAME_TAKEN": UserMessage("That name is already used by another speaker."),
    "SPEAKER_MERGE_INVALID": UserMessage("These speakers cannot be merged."),
    "ENTITY_NOT_FOUND_SUMMARY": UserMessage("This meeting has no summary yet."),
    "SUMMARY_MODELS_UNAVAILABLE": UserMessage(
        "The list of models could not be read. Check that the provider is reachable."
    ),
    "SUMMARY_FAILED": UserMessage("The summary could not be made. Try again."),
    "SUMMARY_NO_SPEECH": UserMessage("There is nothing to summarize: this meeting has no speech."),
    "SUMMARY_PROVIDER_UNAVAILABLE": UserMessage(
        "The summary model is not reachable. Start Ollama, or check the network for the cloud."
    ),
    "SUMMARY_MODEL_NOT_FOUND": UserMessage(
        "The configured summary model is not installed. Pull it with Ollama or change the model."
    ),
    "SUMMARY_AUTH_FAILED": UserMessage("The provider rejected the API key. Set the key again."),
    "SUMMARY_RATE_LIMITED": UserMessage("The provider is busy. Try again in a few minutes."),
    "SUMMARY_PROVIDER_REJECTED": UserMessage("The provider rejected the request."),
    "SUMMARY_TIMEOUT": UserMessage("The model took too long to answer. Try again."),
    "SUMMARY_CONTEXT_TOO_LARGE": UserMessage(
        "The transcript does not fit the model. Raise the Ollama context and "
        "SUMMARIES_LOCAL_CONTEXT_TOKENS together."
    ),
    "SUMMARY_OUTPUT_INVALID": UserMessage("The model did not return a usable summary. Try again."),
    "SUMMARY_CANCELLED": UserMessage("The summary was cancelled."),
    "SUMMARY_CLOUD_KEY_MISSING": UserMessage(
        "No API key is stored for the cloud provider. Set the API key, then try again."
    ),
    "SUMMARY_CLOUD_KEY_LOCKED": UserMessage(
        "The keyring is locked. Unlock the login keyring and try again."
    ),
    "SUMMARY_CLOUD_KEY_UNAVAILABLE": UserMessage(
        "The keyring service is not reachable. Check that gnome-keyring is running."
    ),
    "SUMMARY_CLOUD_DISABLED": UserMessage("The cloud provider is turned off in the settings."),
    "SUMMARY_CLOUD_NOT_CONFIRMED": UserMessage("Confirm sending to the cloud, then try again."),
    "SUMMARY_ALREADY_RUNNING": UserMessage("This meeting is already being summarized."),
    "SUMMARY_BUSY": UserMessage("Another meeting is being summarized. Wait for it or cancel it."),
    "SUMMARY_GPU_BUSY": UserMessage(
        "A recording or processing job is using the GPU. Try again when it ends."
    ),
    "SETTINGS_STORAGE_FAILED": UserMessage(
        "The settings could not be saved. Check disk space and the permissions of the "
        "config folder."
    ),
    "SETTINGS_FILE_INVALID": UserMessage(
        "The saved settings file could not be read, so it is ignored. Save again to replace it."
    ),
    "SETTINGS_RESTART_BUSY": UserMessage(
        "A recording, a processing job or a summary is running. Restart when it ends."
    ),
    "SETTINGS_RESTART_UNAVAILABLE": UserMessage(
        "The service could not be restarted from here.",
        "systemctl --user restart meeting-scribe.service",
    ),
    RESTART_NOT_DONE: UserMessage(
        "The service did not restart.", "systemctl --user restart meeting-scribe.service"
    ),
    "SETTINGS_APPLY_FAILED": UserMessage(
        "Your settings were saved, but a change could not be applied. Restart the service."
    ),
    "FIELD_UNKNOWN": UserMessage("This setting cannot be changed here."),
    "FIELD_LOCKED": UserMessage(
        "The environment sets this field. Remove that override to change it here."
    ),
    "FIELD_INVALID": UserMessage("This value is not valid."),
    "FIELD_FOLDER_NOT_WRITABLE": UserMessage("The meetings folder is not writable."),
    "SUMMARY_STORAGE_FAILED": UserMessage(
        "The summary could not be saved. Check disk space and permissions."
    ),
}


def message_for(error: ServiceError, call: str = "") -> UserMessage:
    """The message for a code, worded for the failed ``call`` when that matters.

    An unmapped code gets a generic message that names the code.
    """
    if error.code == "INVALID_VALUE" and call == RENAME_CALL:
        return UserMessage("That name is not valid.")
    known = _MESSAGES.get(error.code)
    if known is not None:
        return known
    return UserMessage(f"Something went wrong ({error.code}).")


def command_hint(hint: str) -> str:
    """The line shown under an error that names a command to run; empty when there is none."""
    return f"Run: {hint}" if hint else ""
