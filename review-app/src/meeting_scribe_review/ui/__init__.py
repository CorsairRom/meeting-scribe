"""The only package that imports ``gi``: thin views and adapters over the presenters in ``core``.

The toolkit versions are pinned here once, so every module below can import from
``gi.repository`` directly.
"""

import contextlib

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")

with contextlib.suppress(ValueError):  # libsecret serves "Set API key"; the app runs without it
    gi.require_version("Secret", "1")
