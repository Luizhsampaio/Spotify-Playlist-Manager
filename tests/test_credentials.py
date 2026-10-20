from collections.abc import Iterator
from unittest.mock import Mock

import keyring
import pytest
import win32ctypes.pywin32.pywintypes as windows_types
from keyring.errors import KeyringError

from music_curator.credentials import WindowsCredentialStore
from music_curator.models import AppError


@pytest.fixture
def vault(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    # Replace every credential operation: these tests must never touch the real vault.
    from keyring.backends.Windows import WinVaultKeyring

    data: dict[str, str] = {}

    def get_password(service: str, username: str) -> str | None:
        assert service == "music-curator.spotify"
        return data.get(username)

    def set_password(service: str, username: str, password: str) -> None:
        assert service == "music-curator.spotify"
        data[username] = password

    def delete_password(service: str, username: str) -> None:
        assert service == "music-curator.spotify"
        del data[username]

    monkeypatch.setattr(keyring, "get_keyring", lambda: Mock(spec=WinVaultKeyring))
    monkeypatch.setattr(keyring, "get_password", get_password)
    monkeypatch.setattr(keyring, "set_password", set_password)
    monkeypatch.setattr(keyring, "delete_password", delete_password)
    yield data


def test_secure_storage_lifecycle(vault: dict[str, str]) -> None:
    store = WindowsCredentialStore("synthetic-client")
    assert store.load() is None
    store.save("synthetic-refresh")
    assert store.load() == "synthetic-refresh"
    assert WindowsCredentialStore("different-synthetic-client").load() is None
    store.delete()
    store.delete()
    assert vault == {}


@pytest.mark.parametrize("operation", ["load", "save", "delete"])
def test_no_plaintext_backend(monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    monkeypatch.setattr(keyring, "get_keyring", lambda: object())
    store = WindowsCredentialStore("synthetic-client")
    with pytest.raises(AppError, match="Nenhum token"):
        if operation == "save":
            store.save("synthetic-refresh")
        elif operation == "load":
            store.load()
        else:
            store.delete()


@pytest.mark.parametrize("operation", ["load", "save", "delete"])
@pytest.mark.parametrize("error_type", [KeyringError, windows_types.error])
def test_vault_errors_are_explicit(
    vault: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    error_type: type[Exception],
) -> None:
    def unavailable(*args: object) -> None:
        raise error_type("synthetic-private-details")

    store = WindowsCredentialStore("synthetic-client")
    method = {"load": "get_password", "save": "set_password", "delete": "delete_password"}[
        operation
    ]
    monkeypatch.setattr(keyring, method, unavailable)
    vault["synthetic-client"] = "synthetic-refresh"
    with pytest.raises(AppError) as error:
        if operation == "save":
            store.save("synthetic-refresh")
        elif operation == "load":
            store.load()
        else:
            store.delete()
    assert "synthetic-private-details" not in str(error.value)
