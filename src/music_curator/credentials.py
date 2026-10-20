import sys
from typing import Protocol

import keyring
from keyring.errors import KeyringError

from music_curator.models import AppError

VAULT_ERRORS: tuple[type[Exception], ...] = (KeyringError, OSError, UnicodeError)
if sys.platform == "win32":
    import win32ctypes.pywin32.pywintypes as windows_types

    VAULT_ERRORS += (windows_types.error,)


class CredentialStore(Protocol):
    def load(self) -> str | None: ...
    def save(self, refresh_token: str) -> None: ...
    def delete(self) -> None: ...


class WindowsCredentialStore:
    def __init__(self, client_id: str) -> None:
        self.username = client_id
        self.service = "music-curator.spotify"

    def _check_backend(self) -> None:
        if sys.platform != "win32":
            raise AppError("Esta versao requer o armazenamento seguro do Windows.")
        from keyring.backends.Windows import WinVaultKeyring

        try:
            backend = keyring.get_keyring()
        except VAULT_ERRORS as exc:
            raise AppError("Nao foi possivel iniciar o armazenamento seguro do Windows.") from exc
        if not isinstance(backend, WinVaultKeyring):
            raise AppError(
                "Armazenamento seguro do Windows indisponivel. Nenhum token sera salvo em arquivo."
            )

    def load(self) -> str | None:
        self._check_backend()
        try:
            return keyring.get_password(self.service, self.username)
        except VAULT_ERRORS as exc:
            raise AppError("Nao foi possivel ler a credencial segura do Windows.") from exc

    def save(self, refresh_token: str) -> None:
        self._check_backend()
        try:
            keyring.set_password(self.service, self.username, refresh_token)
        except VAULT_ERRORS as exc:
            raise AppError(
                "Nao foi possivel salvar a conexao no Windows. Tente novamente."
            ) from exc

    def delete(self) -> None:
        self._check_backend()
        try:
            if keyring.get_password(self.service, self.username) is not None:
                keyring.delete_password(self.service, self.username)
        except VAULT_ERRORS as exc:
            raise AppError(
                "Nao foi possivel apagar a credencial no Windows. A desconexao nao foi concluida."
            ) from exc
