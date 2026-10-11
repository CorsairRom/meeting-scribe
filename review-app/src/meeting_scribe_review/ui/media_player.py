"""``Gtk.MediaFile`` implementation of ``SamplePlayer`` (spec section 7).

The sample is WAV bytes in memory, wrapped in a seekable ``Gio.MemoryInputStream``; nothing is
written to disk. One stream at a time; ``stop`` drops it. ``play`` must only be reached from a
press on a play button (AC-14). The only things logged are the error domain and code.
"""

import logging

from gi.repository import Gio, GLib, Gtk

from meeting_scribe_review.core.gateway import PlayerListener

_LOG = logging.getLogger(__name__)


def sample_stream(data: bytes) -> Gio.InputStream:
    """A seekable in-memory stream of the sample (``Gtk.MediaFile`` seeks while it probes)."""
    return Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data))


class MediaPlayer:
    def __init__(self) -> None:
        self._stream: Gtk.MediaFile | None = None
        self._handlers: list[int] = []
        self._listeners: list[PlayerListener] = []

    def watch(self, listener: PlayerListener) -> None:
        self._listeners.append(listener)

    def play(self, data: bytes) -> None:
        self.stop()
        stream = Gtk.MediaFile.new_for_input_stream(sample_stream(data))
        self._handlers = [
            stream.connect("notify::ended", self._on_ended),
            stream.connect("notify::error", self._on_error),
        ]
        self._stream = stream
        stream.play()

    def stop(self) -> None:
        """Stop and drop the stream; its events no longer reach the listeners."""
        stream, self._stream = self._stream, None
        if stream is None:
            return
        for handler in self._handlers:
            stream.disconnect(handler)
        self._handlers = []
        stream.pause()
        stream.clear()

    def _on_ended(self, stream: Gtk.MediaFile, _param: object) -> None:
        if stream.get_ended():
            for listener in list(self._listeners):
                listener.on_ended()

    def _on_error(self, stream: Gtk.MediaFile, _param: object) -> None:
        error = stream.get_error()
        if error is None:
            return
        _LOG.warning("playback failed: domain=%s code=%s", error.domain, error.code)
        for listener in list(self._listeners):
            listener.on_error()
