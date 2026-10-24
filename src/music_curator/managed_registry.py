import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from music_curator.models import AppError


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

    @model_validator(mode="after")
    def artist_keys_match_records(self) -> "AccountRegistry":
        if any(key != record.artist_key for key, record in self.artists.items()):
            raise ValueError("Artist registry keys must match record identities")
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

    def save_record(self, account_id: str, record: ManagedPlaylistRecord) -> None:
        document = self._load()
        account = document.accounts.setdefault(account_id, AccountRegistry())
        account.artists[record.artist_key] = record
        self._save(document)

    def delete_record(self, account_id: str, artist_key: str) -> None:
        document = self._load()
        account = document.accounts.get(account_id)
        if account is None or artist_key not in account.artists:
            return
        del account.artists[artist_key]
        if not account.artists:
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
