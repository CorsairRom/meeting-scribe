"""The confirmation every cloud request needs before it is sent (AC-17, safety rule 3).

A request is sent only by ``confirm``; closing, Escape and Cancel send nothing. Each request asks
again: nothing remembers an earlier answer.
"""

from collections.abc import Callable
from dataclasses import dataclass

CONFIRM_LABEL = "Send to cloud"
CANCEL_LABEL = "Cancel"
HEADING = "Send this transcript to the cloud?"


@dataclass(frozen=True, slots=True)
class ConfirmationRequest:
    meeting_id: str
    provider_id: str
    provider_label: str  # worded by the service; it says "cloud"


@dataclass(frozen=True, slots=True)
class ConfirmationState:
    open: bool = False
    heading: str = ""
    body: str = ""
    confirm_label: str = CONFIRM_LABEL
    cancel_label: str = CANCEL_LABEL


class CloudConfirmationPresenter:
    def __init__(
        self,
        on_change: Callable[[ConfirmationState], None],
        on_confirmed: Callable[[ConfirmationRequest], None],
    ) -> None:
        self._on_change = on_change
        self._on_confirmed = on_confirmed
        self._request: ConfirmationRequest | None = None

    def ask(self, request: ConfirmationRequest) -> None:
        self._request = request
        self._on_change(
            ConfirmationState(
                open=True,
                heading=HEADING,
                body=(
                    "The transcript of this meeting, including the speaker names, will leave this "
                    f"computer and be sent to {request.provider_label}."
                ),
            )
        )

    def confirm(self) -> None:
        request, self._request = self._request, None
        if request is None:
            return
        self._on_change(ConfirmationState())
        self._on_confirmed(request)

    def cancel(self) -> None:
        if self._request is None:
            return
        self._request = None
        self._on_change(ConfirmationState())
