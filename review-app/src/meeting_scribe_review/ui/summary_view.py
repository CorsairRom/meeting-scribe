"""The Summary pane: one button, progress and cancel, the stale banner, the text, copy and save.

Declarative glue: every label and every enabled flag comes from ``SummaryState``.
"""

from collections.abc import Callable
from dataclasses import dataclass

from gi.repository import Adw, Gtk

from meeting_scribe_review.core.errors import command_hint
from meeting_scribe_review.core.presenters.summary import SummaryPhase, SummaryState


@dataclass(frozen=True, slots=True)
class SummaryActions:
    generate: Callable[[], None]
    cancel: Callable[[], None]
    open_preferences: Callable[[], None]
    copy: Callable[[], None]
    save: Callable[[], None]


def _button(label: str, action: Callable[[], None], *css: str) -> Gtk.Button:
    button = Gtk.Button(label=label)
    for name in css:
        button.add_css_class(name)
    button.connect("clicked", lambda _button: action())
    return button


class SummaryView(Gtk.Box):
    def __init__(self, actions: SummaryActions) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for side in ("start", "end", "top", "bottom"):
            getattr(self, f"set_margin_{side}")(12)
        self._stale = Adw.Banner(use_markup=False)
        self._generate = _button("Generate summary", actions.generate, "suggested-action")
        self._cancel = _button("Cancel", actions.cancel)
        self._caption = Gtk.Label(xalign=0, wrap=True)
        self._caption.add_css_class("caption")
        self._caption.add_css_class("dim-label")
        buttons = Gtk.Box(spacing=8)
        for button in (self._generate, self._cancel):
            buttons.append(button)
        self._key_hint = Gtk.Label(xalign=0, wrap=True)
        self._open_preferences = _button("Open Preferences", actions.open_preferences, "flat")
        key = Gtk.Box(spacing=8)
        key.append(self._key_hint)
        key.append(self._open_preferences)
        self._key_row = key
        self._progress = Gtk.ProgressBar(show_text=True)
        self._message = Gtk.Label(xalign=0, wrap=True, selectable=False)
        self._source = Gtk.Label(xalign=0, wrap=True)
        self._title = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self._title.add_css_class("title-3")
        self._body = Gtk.Label(xalign=0, yalign=0, wrap=True, selectable=True)
        scroller = Gtk.ScrolledWindow(
            child=self._body, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER
        )
        self._copy = _button("Copy", actions.copy)
        self._save = _button("Save a copy…", actions.save)
        export = Gtk.Box(spacing=8)
        export.append(self._copy)
        export.append(self._save)
        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, vexpand=True)
        for child in (self._source, self._title, scroller, export):
            self._content.append(child)
        for child in (
            self._stale,
            buttons,
            self._caption,
            key,
            self._progress,
            self._message,
            self._content,
        ):
            self.append(child)

    def update(self, state: SummaryState) -> None:
        loading = state.phase is SummaryPhase.LOADING
        hide_generate = state.phase in {SummaryPhase.IDLE, SummaryPhase.LOADING, SummaryPhase.ERROR}
        self._generate.set_label(state.button_label)
        self._generate.set_sensitive(state.can_generate)
        self._generate.set_visible(not hide_generate)
        self._cancel.set_visible(state.can_cancel)
        self._caption.set_label(state.provider_caption)
        self._caption.set_visible(bool(state.provider_caption) and not hide_generate)
        self._key_hint.set_label(state.key_hint)
        self._key_row.set_visible(bool(state.key_hint))
        running = state.phase is SummaryPhase.RUNNING
        self._progress.set_visible(running)
        self._progress.set_text(state.progress_text)
        self._progress.set_fraction(state.progress_fraction or 0.0)
        self._stale.set_title(state.stale_text)
        self._stale.set_revealed(bool(state.stale_text))
        text = state.message or ("Loading the summary" if loading else "")
        hint = command_hint(state.hint)
        self._message.set_label(f"{text}\n{hint}" if hint else text)
        self._message.set_visible(bool(text))
        for name in ("error", "dim-label"):
            self._message.remove_css_class(name)
        failed = state.phase in {SummaryPhase.FAILED, SummaryPhase.ERROR}
        self._message.add_css_class("error" if failed else "dim-label")
        self._source.set_label(state.source_text)
        for name in ("warning", "dim-label"):
            self._source.remove_css_class(name)
        self._source.add_css_class("warning" if state.is_cloud else "dim-label")
        self._title.set_label(state.title)
        self._body.set_label(state.text)
        self._content.set_visible(state.can_copy)
        self._copy.set_sensitive(state.can_copy)
        self._save.set_sensitive(state.can_copy)
