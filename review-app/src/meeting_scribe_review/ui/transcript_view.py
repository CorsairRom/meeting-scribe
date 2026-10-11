"""The transcript: a ``Gtk.ListView`` of timestamp, speaker display name and text."""

from collections.abc import Sequence

from gi.repository import Gio, GObject, Gtk

from meeting_scribe_review.core.transcript import TranscriptLine


class _Line(GObject.Object):
    """The model item: a GObject wrapper because a ``Gio.ListStore`` holds only GObjects."""

    def __init__(self, line: TranscriptLine) -> None:
        super().__init__()
        self.line = line


class TranscriptView(Gtk.ScrolledWindow):
    def __init__(self) -> None:
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self._store = Gio.ListStore.new(_Line)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup)
        factory.connect("bind", self._bind)
        self._view = Gtk.ListView(model=Gtk.NoSelection(model=self._store), factory=factory)
        self._view.add_css_class("transcript")
        self.set_child(self._view)

    def update(self, lines: Sequence[TranscriptLine]) -> None:
        self._store.splice(0, self._store.get_n_items(), [_Line(line) for line in lines])

    @staticmethod
    def _setup(_factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(12)
        box.set_margin_end(12)
        header = Gtk.Box(spacing=8)
        stamp = Gtk.Label(xalign=0)
        stamp.add_css_class("dim-label")
        stamp.add_css_class("numeric")
        who = Gtk.Label(xalign=0)
        who.add_css_class("heading")
        header.append(stamp)
        header.append(who)
        text = Gtk.Label(xalign=0, wrap=True, selectable=True)
        box.append(header)
        box.append(text)
        item.set_child(box)

    @staticmethod
    def _bind(_factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        line = item.get_item().line
        box = item.get_child()
        header = box.get_first_child()
        stamp = header.get_first_child()
        stamp.set_label(line.timestamp)
        stamp.get_next_sibling().set_label(line.speaker_name)
        header.get_next_sibling().set_label(line.text)
