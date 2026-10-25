import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest

from music_curator.managed_registry import (
    ManagedPlaylistRecord,
    ManagedPlaylistRegistry,
    default_registry_path,
)
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


def test_default_registry_path_is_independent_of_process_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = (
        Path(__file__).resolve().parents[1] / ".local-data" / "spotify" / ("managed-playlists.json")
    )
    original = default_registry_path()
    monkeypatch.chdir(tmp_path)

    assert original == expected
    assert default_registry_path() == original


def test_registry_preserves_pending_creation_without_an_id(tmp_path: Path) -> None:
    registry = ManagedPlaylistRegistry(tmp_path / "managed.json")
    pending = record(playlist_id=None, owner_id=None, state="creation_uncertain")
    registry.save_record("accountOne", pending)

    assert registry.records_for("accountOne")[KEY].state == "creation_uncertain"


def test_registry_appends_at_most_ten_playlist_notes_and_reads_legacy_records(
    tmp_path: Path,
) -> None:
    path = tmp_path / "managed.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "accounts": {
                    "accountOne": {
                        "artists": {KEY: record().model_dump(mode="json")},
                        "selected_artist_keys": [KEY],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    registry = ManagedPlaylistRegistry(path)
    assert registry.records_for("accountOne")[KEY].notes == []

    first_time = datetime(2026, 10, 25, 20, 0, tzinfo=UTC)
    for index in range(12):
        registry.append_note(
            "accountOne",
            KEY,
            "completed",
            f"Sincronizacao sintetica {index}",
            occurred_at=first_time,
        )

    notes = ManagedPlaylistRegistry(path).records_for("accountOne")[KEY].notes
    assert len(notes) == 10
    assert [note.message for note in notes] == [
        f"Sincronizacao sintetica {index}" for index in range(2, 12)
    ]
    assert all(note.occurred_at == first_time for note in notes)


def test_registry_persists_last_playlist_sync_time_per_account(tmp_path: Path) -> None:
    registry = ManagedPlaylistRegistry(tmp_path / "managed.json")
    assert registry.last_playlist_sync_at("accountOne") is None

    updated_at = datetime(2026, 10, 25, 22, 45, tzinfo=UTC)
    assert registry.mark_playlists_synced("accountOne", occurred_at=updated_at) == updated_at

    reopened = ManagedPlaylistRegistry(registry.path)
    assert reopened.last_playlist_sync_at("accountOne") == updated_at
    assert reopened.last_playlist_sync_at("accountTwo") is None


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


def test_selection_is_persisted_per_account_and_survives_record_deletion(
    tmp_path: Path,
) -> None:
    path = tmp_path / "managed.json"
    registry = ManagedPlaylistRegistry(path)
    other_key = "b" * 64
    registry.save_selection("accountOne", {other_key, KEY})
    registry.save_selection("accountTwo", {other_key})
    registry.save_record("accountOne", record())

    registry.delete_record("accountOne", KEY)

    reopened = ManagedPlaylistRegistry(path)
    assert reopened.selection_for("accountOne") == {KEY, other_key}
    assert reopened.selection_for("accountTwo") == {other_key}
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["accounts"]["accountOne"]["selected_artist_keys"] == [KEY, other_key]


def test_old_registry_defaults_to_empty_selection_and_invalid_keys_are_rejected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "managed.json"
    path.write_text(
        json.dumps({"version": 1, "accounts": {"accountOne": {"artists": {}}}}),
        encoding="utf-8",
    )
    registry = ManagedPlaylistRegistry(path)
    assert registry.selection_for("accountOne") == set()

    with pytest.raises(AppError, match="selecao"):
        registry.save_selection("accountOne", {"invalid"})

    assert registry.selection_for("accountOne") == set()
