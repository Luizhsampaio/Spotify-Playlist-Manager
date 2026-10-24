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
    def load_scopes(self) -> set[str] | None: ...
    def save_scopes(self, scopes: set[str]) -> None: ...
    def delete(self) -> None: ...


class WindowsCredentialStore:
    def __init__(self, client_id: str) -> None:
        self.username = client_id
        self.scopes_username = f"{client_id}:granted-scopes"
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

    def load_scopes(self) -> set[str] | None:
        self._check_backend()
        try:
            scopes = keyring.get_password(self.service, self.scopes_username)
        except VAULT_ERRORS as exc:
            raise AppError("Nao foi possivel verificar as permissoes salvas no Windows.") from exc
        if scopes is None:
            return None
        values = scopes.split()
        if not values or any(
            not scope.isascii() or not scope.replace("-", "").isalnum() for scope in values
        ):
            raise AppError("As permissoes salvas estao invalidas. Conecte sua conta novamente.")
        return set(values)

    def save_scopes(self, scopes: set[str]) -> None:
        self._check_backend()
        if not scopes or any(
            not scope.isascii() or not scope.replace("-", "").isalnum() for scope in scopes
        ):
            raise AppError("Nao foi possivel salvar as permissoes autorizadas.")
        try:
            keyring.set_password(self.service, self.scopes_username, " ".join(sorted(scopes)))
        except VAULT_ERRORS as exc:
            raise AppError("Nao foi possivel salvar as permissoes autorizadas no Windows.") from exc

    def delete(self) -> None:
        self._check_backend()
        try:
            for username in (self.username, self.scopes_username):
                if keyring.get_password(self.service, username) is not None:
                    keyring.delete_password(self.service, username)
        except VAULT_ERRORS as exc:
            raise AppError(
                "Nao foi possivel apagar a credencial no Windows. A desconexao nao foi concluida."
            ) from exc
