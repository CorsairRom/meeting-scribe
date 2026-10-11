"""Rename, merge-target and error dialogs (``Adw.AlertDialog``), driven by presenter states.

A response closes an alert dialog, so each presenter state that needs input opens a fresh dialog
and each response is passed straight back to the session. A duplicate name offers the two ways out
of the spec: merge into that speaker, or edit the name.
"""

from collections.abc import Callable

from gi.repository import Adw, Gio, Gtk

from meeting_scribe_review.core.errors import command_hint
from meeting_scribe_review.core.presenters.merge import MergeState, MergeStatus
from meeting_scribe_review.core.presenters.rename import NameConflict, RenameState, RenameStatus
from meeting_scribe_review.core.presenters.review import ReviewState
from meeting_scribe_review.core.session import ReviewSession

_CANCEL = "cancel"
_OK = "ok"
_MERGE = "merge"
_EDIT = "edit"


class AlertHost:
    """Presents at most one alert dialog at a time over ``parent``."""

    def __init__(self, parent: Gtk.Widget) -> None:
        self._parent = parent
        self._open: Adw.AlertDialog | None = None

    def _present(
        self,
        dialog: Adw.AlertDialog,
        handlers: dict[str, Callable[[], None]],
        otherwise: Callable[[], None],
    ) -> None:
        self._dismiss()
        self._open = dialog

        def answered(source: Adw.AlertDialog, result: Gio.AsyncResult) -> None:
            response = source.choose_finish(result)
            if self._open is not source:
                return  # dismissed by a newer state; its answer is stale
            self._open = None
            handlers.get(response, otherwise)()

        dialog.choose(self._parent, None, answered)

    def _dismiss(self) -> None:
        dialog, self._open = self._open, None
        if dialog is not None:
            dialog.force_close()


class SpeakerDialogs(AlertHost):
    def __init__(self, parent: Gtk.Widget, session: ReviewSession) -> None:
        super().__init__(parent)
        self._session = session

    def show_rename(self, state: RenameState, review: ReviewState) -> None:
        if state.status is RenameStatus.EDITING:
            self._rename(state, review)
        elif state.status is RenameStatus.CONFLICT and state.conflict is not None:
            self._conflict(state.conflict)
        elif state.status is RenameStatus.IDLE:
            self._dismiss()

    def show_merge(self, state: MergeState, review: ReviewState) -> None:
        if state.status is MergeStatus.CHOOSING:
            self._choose_target(state, review)
        elif state.status is MergeStatus.ERROR:
            self._error(state)
        elif state.status is MergeStatus.IDLE:
            self._dismiss()

    def _rename(self, state: RenameState, review: ReviewState) -> None:
        speaker = next((s for s in review.speakers if s.id == state.speaker_id), None)
        shown = speaker.display if speaker else state.speaker_id
        entry = Gtk.Entry(text=state.draft, activates_default=True)
        entry.update_property([Gtk.AccessibleProperty.LABEL], ["Speaker name"])
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(entry)
        if state.problem:
            problem = Gtk.Label(label=state.problem, xalign=0, wrap=True)
            problem.add_css_class("error")
            box.append(problem)
        dialog = Adw.AlertDialog(
            heading="Rename speaker",
            body=f"Give {shown} a name. Leave it empty to go back to the default name.",
        )
        dialog.set_extra_child(box)
        dialog.add_response(_CANCEL, "Cancel")
        dialog.add_response(_OK, "Rename")
        dialog.set_response_appearance(_OK, Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response(_OK)
        dialog.set_close_response(_CANCEL)
        self._present(
            dialog,
            {_OK: lambda: self._session.submit_rename(entry.get_text())},
            self._session.cancel_rename,
        )
        entry.grab_focus()

    def _conflict(self, conflict: NameConflict) -> None:
        dialog = Adw.AlertDialog(
            heading="That name is already used",
            body=(
                f"{conflict.display} already has this name. If they are the same person, merge "
                "this speaker into them; otherwise change the name to tell them apart, for "
                "example with a surname."
            ),
        )
        dialog.add_response(_CANCEL, "Cancel")
        dialog.add_response(_EDIT, "Edit the name")
        handlers = {_EDIT: self._session.edit_again}
        if conflict.can_merge:
            dialog.add_response(_MERGE, f"Merge into {conflict.display}")
            dialog.set_response_appearance(_MERGE, Adw.ResponseAppearance.SUGGESTED)
            handlers[_MERGE] = self._session.merge_into_conflict
        dialog.set_default_response(_EDIT)
        dialog.set_close_response(_CANCEL)
        self._present(dialog, handlers, self._session.cancel_rename)

    def _choose_target(self, state: MergeState, review: ReviewState) -> None:
        source = next((s for s in review.speakers if s.id == state.source_id), None)
        shown = source.display if source else state.source_id
        choices = Gtk.DropDown.new_from_strings([target.display for target in state.targets])
        choices.update_property([Gtk.AccessibleProperty.LABEL], ["Speaker to merge into"])
        dialog = Adw.AlertDialog(
            heading="Merge speakers",
            body=f"Show {shown} as the speaker below. You can undo this later with Unmerge.",
        )
        dialog.set_extra_child(choices)
        dialog.add_response(_CANCEL, "Cancel")
        dialog.add_response(_MERGE, "Merge")
        dialog.set_response_appearance(_MERGE, Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response(_MERGE)
        dialog.set_close_response(_CANCEL)
        self._present(
            dialog,
            {_MERGE: lambda: self._confirm(state, choices.get_selected())},
            self._session.cancel_merge,
        )

    def _confirm(self, state: MergeState, index: int) -> None:
        """Merge into the chosen target; an empty or invalid choice cancels instead."""
        if 0 <= index < len(state.targets):
            self._session.confirm_merge(state.targets[index].id)
        else:
            self._session.cancel_merge()

    def _error(self, state: MergeState) -> None:
        body = state.message if not state.hint else f"{state.message}\n\n{command_hint(state.hint)}"
        dialog = Adw.AlertDialog(heading="That did not work", body=body)
        dialog.add_response(_CANCEL, "Close")
        dialog.set_close_response(_CANCEL)
        self._present(dialog, {}, self._session.cancel_merge)
