"""``GLib.Variant`` packing and unpacking for the ``Meetings1`` calls.

Kept free of widgets and of the bus so it runs, and is tested, on system Python without a display.
"""

from typing import Any

from gi.repository import Gio, GLib

from meeting_scribe_review.core.errors import ServiceError, error_from_dbus_name


def pack_args(signature: str, values: tuple[Any, ...]) -> GLib.Variant | None:
    """The call parameters as one tuple variant (``None`` for a call without arguments)."""
    if not signature:
        return None
    return GLib.Variant(f"({signature})", values)


def unpack_reply(reply: GLib.Variant) -> Any:
    """The single out argument as plain Python values (``a{sv}`` rows become dicts).

    ``ay`` is returned as ``bytes``: ``unpack`` would build a list of ints from a 0.5 MB sample.
    """
    child = reply.get_child_value(0)
    if child.get_type_string() == "ay":
        return bytes(child.get_data_as_bytes().get_data())
    return child.unpack()


def error_from_glib(error: GLib.Error) -> ServiceError:
    """A failed call as a ``ServiceError``. Only the error name is read, never its message."""
    name = Gio.DBusError.get_remote_error(error) if Gio.DBusError.is_remote_error(error) else ""
    return error_from_dbus_name(name or "")
