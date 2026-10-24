import json
from pathlib import Path
from typing import Literal

import pytest

from music_curator.managed_registry import ManagedPlaylistRecord, ManagedPlaylistRegistry
from music_curator.models import AppError

KEY = "a" * 64


def record(
    *,
    playlist_id: str | None = "syntheticPlaylist",
    owner_id: str | None = "syntheticOwner",
    state: Literal[
        "pending_create", "creation_uncertain", "created", "active", "removed", "uncertain"
    ] = "active",
) -> ManagedPlaylistRecord:
    return ManagedPlaylistRecord(
        artist_key=KEY,
        artist_id="syntheticArtist",
        artist_name="Artista sintético",
        playlist_id=playlist_id,
        owner_id=owner_id,
        state=state,
    )


def test_registry_persists_records_by_account_and_artist(tmp_path: Path) -> None:
    path = tmp_path / "spotify" / "managed-playlists.json"
    registry = ManagedPlaylistRegistry(path)
    registry.save_record("accountOne", record())
    registry.save_record("accountTwo", record(playlist_id="otherPlaylist"))

    reopened = ManagedPlaylistRegistry(path)
    assert reopened.records_for("accountOne")[KEY].playlist_id == "syntheticPlaylist"
    assert reopened.records_for("accountTwo")[KEY].playlist_id == "otherPlaylist"
    assert reopened.records_for("unknownAccount") == {}
    content = path.read_text(encoding="utf-8")
    assert "synthetic-refresh" not in content
    assert "token" not in content.lower()


def test_registry_preserves_pending_creation_without_an_id(tmp_path: Path) -> None:
    registry = ManagedPlaylistRegistry(tmp_path / "managed.json")
    pending = record(playlist_id=None, owner_id=None, state="creation_uncertain")
    registry.save_record("accountOne", pending)

    assert registry.records_for("accountOne")[KEY].state == "creation_uncertain"


@pytest.mark.parametrize("contents", ["{broken", '{"version":2,"accounts":{}}'])
def test_corrupt_or_unknown_registry_blocks_mutations(tmp_path: Path, contents: str) -> None:
    path = tmp_path / "managed.json"
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(AppError, match="registro local"):
        ManagedPlaylistRegistry(path).records_for("accountOne")


def test_registry_rejects_mismatched_record_key(tmp_path: Path) -> None:
    path = tmp_path / "managed.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "accounts": {
                    "accountOne": {
                        "artists": {
                            "b" * 64: record().model_dump(mode="json"),
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(AppError, match="registro local"):
        ManagedPlaylistRegistry(path).records_for("accountOne")


def test_deleting_one_record_preserves_other_accounts(tmp_path: Path) -> None:
    registry = ManagedPlaylistRegistry(tmp_path / "managed.json")
    registry.save_record("accountOne", record())
    registry.save_record(
        "accountOne",
        record(
            playlist_id="anotherPlaylist",
            owner_id="syntheticOwner",
        ).model_copy(update={"artist_key": "b" * 64}),
    )
    registry.save_record("accountTwo", record(playlist_id="otherAccountPlaylist"))

    registry.delete_record("accountOne", KEY)

    assert set(registry.records_for("accountOne")) == {"b" * 64}
    assert KEY in registry.records_for("accountTwo")
