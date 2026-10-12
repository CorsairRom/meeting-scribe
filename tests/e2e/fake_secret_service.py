"""A fake ``org.freedesktop.secrets`` for the e2e tests: the real keyring is never contacted."""

from typing import Annotated

from dbus_fast import Variant
from dbus_fast.annotations import DBusSignature, DBusStr
from dbus_fast.service import ServiceInterface, dbus_method

NAME = "org.freedesktop.secrets"
PATH = "/org/freedesktop/secrets"
SESSION = "/org/freedesktop/secrets/session/s1"
ITEM = "/org/freedesktop/secrets/collection/login/1"
FAKE_SECRET = "test-key-not-a-secret"  # noqa: S105


class FakeSecretService(ServiceInterface):
    def __init__(self, *, unlocked: list[str], locked: list[str], secret: bytes) -> None:
        super().__init__("org.freedesktop.Secret.Service")
        self._unlocked, self._locked, self._secret = unlocked, locked, secret
        self.opened: list[str] = []
        self.searched: list[dict[str, str]] = []
        self.unlock_calls = 0

    @dbus_method()
    def OpenSession(  # noqa: N802
        self,
        algorithm: DBusStr,
        input: Annotated[Variant, DBusSignature("v")],  # noqa: A002, ARG002
    ) -> Annotated[tuple[Variant, str], DBusSignature("vo")]:
        self.opened.append(algorithm)
        return Variant("s", ""), SESSION

    @dbus_method()
    def SearchItems(  # noqa: N802
        self, attributes: Annotated[dict[str, str], DBusSignature("a{ss}")]
    ) -> Annotated[tuple[list[str], list[str]], DBusSignature("aoao")]:
        self.searched.append(dict(attributes))
        return list(self._unlocked), list(self._locked)

    @dbus_method()
    def Unlock(  # noqa: N802
        self,
        objects: Annotated[list[str], DBusSignature("ao")],  # noqa: ARG002
    ) -> Annotated[tuple[list[str], str], DBusSignature("aoo")]:
        self.unlock_calls += 1
        return [], "/"

    @dbus_method()
    def GetSecrets(  # noqa: N802
        self,
        items: Annotated[list[str], DBusSignature("ao")],
        session: Annotated[str, DBusSignature("o")],
    ) -> Annotated[dict[str, list[object]], DBusSignature("a{o(oayays)}")]:
        return {item: [session, b"", self._secret, "text/plain"] for item in items}
