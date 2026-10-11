"""A full-pane message with an optional Retry button (loading, empty and error states)."""

from collections.abc import Callable

from gi.repository import Adw, Gtk


class StatusView(Gtk.Box):
    """A title, a description, and a Retry button when the user can try again.

    Wraps ``Adw.StatusPage`` because libadwaita declares it final (it cannot be subclassed).
    """

    def __init__(self, on_retry: Callable[[], None]) -> None:
        super().__init__()
        self._page = Adw.StatusPage(hexpand=True, vexpand=True)
        self.append(self._page)
        self._retry = Gtk.Button(label="Retry", halign=Gtk.Align.CENTER)
        self._retry.add_css_class("pill")
        self._retry.add_css_class("suggested-action")
        self._retry.connect("clicked", lambda _button: on_retry())
        self._page.set_child(self._retry)

    def show_message(
        self, title: str, description: str = "", icon: str = "", *, retry: bool = False
    ) -> None:
        self._page.set_title(title)
        self._page.set_description(description)
        self._page.set_icon_name(icon or None)
        self._retry.set_visible(retry)
