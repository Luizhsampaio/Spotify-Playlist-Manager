import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from music_curator.models import AppError


def default_registry_path() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    return project_root / ".local-data" / "spotify" / "managed-playlists.json"


class ManagedPlaylistLogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occurred_at: datetime
    status: Literal["completed", "partial", "failed"]
    message: str = Field(min_length=1, max_length=2000)


class ManagedPlaylistRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artist_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    artist_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9]+$", min_length=1, max_length=128
    )
    artist_name: str = Field(min_length=1, max_length=300)
    playlist_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9]+$", min_length=1, max_length=128
    )
    owner_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9]+$", min_length=1, max_length=128
    )
    state: Literal[
        "pending_create", "creation_uncertain", "created", "active", "removed", "uncertain"
    ]
    notes: list[ManagedPlaylistLogEntry] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def consistent_playlist_identity(self) -> "ManagedPlaylistRecord":
        has_playlist = self.playlist_id is not None and self.owner_id is not None
        if self.state in {"pending_create", "creation_uncertain"}:
            if self.playlist_id is not None or self.owner_id is not None:
                raise ValueError("Pending creations cannot have a playlist ID")
        elif not has_playlist:
            raise ValueError("Managed playlist states require a playlist and owner ID")
        return self


class AccountRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artists: dict[str, ManagedPlaylistRecord] = Field(default_factory=dict)
    selected_artist_keys: list[str] = Field(default_factory=list)
    last_playlist_sync_at: datetime | None = None

    @model_validator(mode="after")
    def validate_artist_keys(self) -> "AccountRegistry":
        if any(key != record.artist_key for key, record in self.artists.items()):
            raise ValueError("Artist registry keys must match record identities")
        if len(self.selected_artist_keys) != len(set(self.selected_artist_keys)):
            raise ValueError("Selected artist keys must be unique")
        if any(
            not len(key) == 64 or any(char not in "0123456789abcdef" for char in key)
            for key in self.selected_artist_keys
        ):
            raise ValueError("Selected artist keys are invalid")
        self.selected_artist_keys.sort()
        return self


class RegistryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    accounts: dict[str, AccountRegistry] = Field(default_factory=dict)

    @model_validator(mode="after")
    def account_ids_are_valid(self) -> "RegistryDocument":
        if any(
            not account_id.isascii() or not account_id.isalnum() or len(account_id) > 128
            for account_id in self.accounts
        ):
            raise ValueError("Invalid account identifier")
        return self


class ManagedPlaylistRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path

    def records_for(self, account_id: str) -> dict[str, ManagedPlaylistRecord]:
        document = self._load()
        account = document.accounts.get(account_id)
        return dict(account.artists) if account is not None else {}

    def selection_for(self, account_id: str) -> set[str]:
        document = self._load()
        account = document.accounts.get(account_id)
        return set(account.selected_artist_keys) if account is not None else set()

    def last_playlist_sync_at(self, account_id: str) -> datetime | None:
        document = self._load()
        account = document.accounts.get(account_id)
        return account.last_playlist_sync_at if account is not None else None

    def mark_playlists_synced(
        self, account_id: str, *, occurred_at: datetime | None = None
    ) -> datetime:
        document = self._load()
        account = document.accounts.setdefault(account_id, AccountRegistry())
        account.last_playlist_sync_at = occurred_at or datetime.now(UTC)
        self._save(document)
        return account.last_playlist_sync_at

    def save_selection(self, account_id: str, artist_keys: set[str]) -> None:
        document = self._load()
        account = document.accounts.setdefault(account_id, AccountRegistry())
        account.selected_artist_keys = sorted(artist_keys)
        try:
            account = AccountRegistry.model_validate(account.model_dump())
        except ValidationError as exc:
            raise AppError("A selecao de artistas nao e valida.") from exc
        document.accounts[account_id] = account
        self._save(document)

    def save_record(self, account_id: str, record: ManagedPlaylistRecord) -> None:
        document = self._load()
        account = document.accounts.setdefault(account_id, AccountRegistry())
        account.artists[record.artist_key] = record
        self._save(document)

    def append_note(
        self,
        account_id: str,
        artist_key: str,
        status: Literal["completed", "partial", "failed"],
        message: str,
        *,
        occurred_at: datetime | None = None,
    ) -> None:
        document = self._load()
        account = document.accounts.get(account_id)
        record = account.artists.get(artist_key) if account is not None else None
        if record is None:
            raise AppError("Nao foi possivel registrar a nota da playlist vinculada.")
        record.notes.append(
            ManagedPlaylistLogEntry(
                occurred_at=occurred_at or datetime.now(UTC),
                status=status,
                message=message,
            )
        )
        record.notes = record.notes[-10:]
        self._save(document)

    def delete_record(self, account_id: str, artist_key: str) -> None:
        document = self._load()
        account = document.accounts.get(account_id)
        if account is None or artist_key not in account.artists:
            return
        del account.artists[artist_key]
        if not account.artists and not account.selected_artist_keys:
            del document.accounts[account_id]
        self._save(document)

    def _load(self) -> RegistryDocument:
        try:
            contents = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return RegistryDocument()
        except (OSError, UnicodeError) as exc:
            raise AppError("Nao foi possivel ler o registro local de playlists.") from exc
        try:
            return RegistryDocument.model_validate_json(contents)
        except (ValidationError, ValueError) as exc:
            raise AppError(
                "O registro local de playlists esta invalido. Nenhuma alteracao foi feita."
            ) from exc

    def _save(self, document: RegistryDocument) -> None:
        temporary_path: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(document.model_dump_json(indent=2))
                temporary.write("\n")
            os.replace(temporary_path, self.path)
        except OSError as exc:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    raise AppError(
                        "Nao foi possivel limpar um arquivo temporario do registro local."
                    ) from cleanup_error
            raise AppError("Nao foi possivel salvar o registro local de playlists.") from exc
