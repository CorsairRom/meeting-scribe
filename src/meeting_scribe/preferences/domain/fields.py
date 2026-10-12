"""The exposed-field registry and the value types around it (spec ``preferences.md`` section 2).

Only plain data lives here: the validation rules stay in the settings classes (AC-3), which the
composition root hands to the use cases. Labels live in the app, never in the service.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

type FieldKind = Literal["choice", "text", "path"]
type ApplyMode = Literal["live", "restart"]
type SourceName = Literal["default", "file", "env", "dotenv"]


class FieldCode(StrEnum):
    """Per-field codes inside a ``Set`` reply (not D-Bus errors)."""

    UNKNOWN = "FIELD_UNKNOWN"
    LOCKED = "FIELD_LOCKED"
    INVALID = "FIELD_INVALID"
    FOLDER_NOT_WRITABLE = "FIELD_FOLDER_NOT_WRITABLE"


@dataclass(frozen=True)
class FieldSpec:
    key: str  # "<section>.<name>": the section is a settings group, the name its attribute
    kind: FieldKind
    apply: ApplyMode
    choices: tuple[str, ...] = ()

    @property
    def section(self) -> str:
        return self.key.split(".", 1)[0]

    @property
    def name(self) -> str:
        return self.key.split(".", 1)[1]


# The API key is deliberately absent: it is not a setting (BR-005).
FIELD_REGISTRY: tuple[FieldSpec, ...] = (
    FieldSpec("summaries.provider", "choice", "live", ("local", "cloud")),
    FieldSpec("summaries.local_model", "text", "live"),
    # "auto" or any lowercase ISO code: the validator is the settings rule, the list is a hint.
    FieldSpec("summaries.language", "choice", "live", ("auto",)),
    FieldSpec("summaries.cloud_model", "text", "live"),
    FieldSpec("summaries.cloud_provider", "choice", "live", ("opencode-go",)),
    FieldSpec("storage.meetings_dir", "path", "restart"),
)


def field_spec(key: str) -> FieldSpec | None:
    return next((spec for spec in FIELD_REGISTRY if spec.key == key), None)


@dataclass(frozen=True)
class FieldProblem:
    """One invalid field: a key, a stable code and a message that never holds a value."""

    key: str
    code: FieldCode
    message: str


@dataclass(frozen=True)
class FieldSource:
    """The layer that supplies a field's effective value; the environment layers lock it."""

    source: SourceName = "default"
    lock_reason: str = ""  # empty, or a code such as LEGACY_SETTING

    @property
    def locked(self) -> bool:
        return self.source in {"env", "dotenv"}


@dataclass(frozen=True)
class FieldRow:
    """One ``GetAll`` row: the spec plus where the effective value comes from."""

    key: str
    kind: FieldKind
    value: str
    default: str
    source: SourceName
    locked: bool
    lock_reason: str
    apply: ApplyMode
    choices: tuple[str, ...]
    file_problem: str  # empty, or a code such as SETTINGS_FILE_INVALID (the same on every row)
    apply_problem: str = ""  # empty, or SETTINGS_APPLY_FAILED until a later apply succeeds
