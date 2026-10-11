"""The speakers section: one row per speaker with play, rename, merge and unmerge actions.

Declarative glue over ``SpeakerRow`` and ``PlaybackState`` from core. Rows are rebuilt when the
review changes and updated in place when only playback changes, so a button is never destroyed
inside its own click handler.
"""

from collections.abc import Callable
from dataclasses import dataclass

from gi.repository import Adw, Gtk

from meeting_scribe_review.core.presenters.playback import PlaybackState, PlaybackStatus
from meeting_scribe_review.core.presenters.review import ReviewState, SpeakerRow

_PLAY_ICON = "media-playback-start-symbolic"
_STOP_ICON = "media-playback-stop-symbolic"


@dataclass(frozen=True, slots=True)
class SpeakerActions:
    play: Callable[[str], None]
    rename: Callable[[str], None]
    merge: Callable[[str], None]
    unmerge: Callable[[str], None]


@dataclass(slots=True)
class _Built:
    model: SpeakerRow
    widget: Adw.ActionRow
    play: Gtk.Button | None


def _icon_button(icon: str, tooltip: str, label: str, handler: Callable[[], None]) -> Gtk.Button:
    button = Gtk.Button.new_from_icon_name(icon)
    button.set_valign(Gtk.Align.CENTER)
    button.add_css_class("flat")
    button.set_tooltip_text(tooltip)
    button.update_property([Gtk.AccessibleProperty.LABEL], [label])
    button.connect("clicked", lambda _button: handler())
    return button


def _text_button(text: str, handler: Callable[[], None]) -> Gtk.Button:
    button = Gtk.Button(label=text, valign=Gtk.Align.CENTER)
    button.add_css_class("flat")
    button.connect("clicked", lambda _button: handler())
    return button


class SpeakersSection(Gtk.Box):
    def __init__(self, actions: SpeakerActions) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._actions = actions
        self._groups: list[Adw.PreferencesGroup] = []
        self._built: list[_Built] = []
        self._playback = PlaybackState()
        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(12)
        self.set_margin_end(12)

    def update(self, review: ReviewState) -> None:
        for group in self._groups:
            self.remove(group)
        self._groups, self._built = [], []
        self._add_group("Speakers", review.active, review.editable)
        if review.merged:
            self._add_group("Merged", review.merged, review.editable)
        self.update_playback(self._playback)

    def update_playback(self, playback: PlaybackState) -> None:
        self._playback = playback
        for built in self._built:
            status = playback.status_of(built.model.id)
            playing = status in (PlaybackStatus.PLAYING, PlaybackStatus.LOADING)
            if built.play is not None:
                built.play.set_icon_name(_STOP_ICON if playing else _PLAY_ICON)
                built.play.set_tooltip_text("Stop" if playing else "Play sample")
            built.widget.set_subtitle(self._subtitle(built.model, playback))

    def _add_group(self, title: str, rows: tuple[SpeakerRow, ...], editable: bool) -> None:
        group = Adw.PreferencesGroup(title=title)
        for model in rows:
            built = self._build(model, editable=editable)
            group.add(built.widget)
            self._built.append(built)
        self.append(group)
        self._groups.append(group)

    def _build(self, model: SpeakerRow, *, editable: bool) -> _Built:
        widget = Adw.ActionRow(title=model.display)
        widget.set_title_lines(1)
        play = None
        if model.can_play:
            play = _icon_button(
                _PLAY_ICON,
                "Play sample",
                f"Play the voice sample of {model.display}",
                lambda: self._actions.play(model.id),
            )
            widget.add_prefix(play)
        if editable and model.can_unmerge:
            widget.add_suffix(_text_button("Unmerge", lambda: self._actions.unmerge(model.id)))
        if editable and model.can_merge:
            widget.add_suffix(_text_button("Merge…", lambda: self._actions.merge(model.id)))
        if editable and model.can_rename:
            widget.add_suffix(
                _icon_button(
                    "document-edit-symbolic",
                    "Rename",
                    f"Rename {model.display}",
                    lambda: self._actions.rename(model.id),
                )
            )
        return _Built(model, widget, play)

    @staticmethod
    def _subtitle(model: SpeakerRow, playback: PlaybackState) -> str:
        parts = []
        if model.merged_into_display:
            parts.append(f"Merged into {model.merged_into_display}")
        elif model.detail:
            parts.append(model.detail)
        parts.append(model.summary)
        note = playback.text_of(model.id) or model.sample_text
        if note:
            parts.append(note)
        return " · ".join(parts)
