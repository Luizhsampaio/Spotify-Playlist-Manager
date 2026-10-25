import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import NotRequired, TypedDict

import httpx
import pytest
from fastapi.testclient import TestClient

from music_curator.config import ORIGIN, Settings
from music_curator.demo import PROTOTYPE_TRACKS, DemoCredentialStore
from music_curator.managed_registry import ManagedPlaylistRecord, ManagedPlaylistRegistry
from music_curator.models import Artist, SavedTrack
from music_curator.playlist_preview import artist_playlists
from music_curator.spotify import (
    AUTH_SCOPES,
    CREATE_PLAYLIST_URL,
    MANAGED_PLAYLIST_DESCRIPTION,
    PREVIOUS_MANAGED_PLAYLIST_DESCRIPTION,
    TOKEN_URL,
)
from music_curator.web import create_app

PAGE = "/app/playlists"
PREVIEW = "/app/playlists/por-artista/salvar"
HEADERS = {"Origin": ORIGIN}


class SyntheticPlaylist(TypedDict):
    name: str
    owner: str
    items: list[str]
    in_library: NotRequired[bool]
    public: NotRequired[bool]
    collaborative: NotRequired[bool]
    description: NotRequired[str | None]


def field(html: str, name: str) -> str:
    match = re.search(rf'name="{name}" value="([^"]+)"', html)
    assert match
    return match[1]


def enter_demo(browser: TestClient) -> None:
    browser.post(
        "/demo/entrar",
        data={"csrf": field(browser.get("/entrar").text, "csrf")},
        headers=HEADERS,
    )


def post_preview(browser: TestClient, keys: list[str], html: str | None = None) -> httpx.Response:
    html = html if html is not None else browser.get(PAGE).text
    response = browser.post(
        PREVIEW,
        data={
            "csrf": field(html, "csrf"),
            "revision": field(html, "revision"),
            "artists": keys,
        },
        headers=HEADERS,
    )
    assert isinstance(response, httpx.Response)
    return response


def playlist_json(playlist_id: str, playlist: SyntheticPlaylist) -> dict[str, object]:
    return {
        "id": playlist_id,
        "name": playlist["name"],
        "description": playlist.get("description"),
        "public": playlist.get("public", True),
        "collaborative": playlist.get("collaborative", False),
        "owner": {"id": playlist["owner"]},
    }


def synthetic_spotify_app(
    registry_path: Path,
    *,
    timeout_create_once: bool = False,
    timeout_remove_once: bool = False,
    tracks: list[SavedTrack] | None = None,
    override: Callable[[httpx.Request], httpx.Response | None] | None = None,
) -> tuple[TestClient, dict[str, SyntheticPlaylist], list[tuple[str, str]]]:
    state: dict[str, SyntheticPlaylist] = {}
    calls: list[tuple[str, str]] = []
    timeout_pending = timeout_create_once
    remove_timeout_pending = timeout_remove_once
    next_id = 0
    library_tracks = tracks if tracks is not None else PROTOTYPE_TRACKS

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal timeout_pending, remove_timeout_pending, next_id
        if str(request.url) != TOKEN_URL:
            assert request.headers["authorization"] == "Bearer " + "synthetic-" + "access"
        path = request.url.path
        calls.append((request.method, path))
        if override is not None:
            overridden = override(request)
            if overridden is not None:
                return overridden
        if str(request.url) == TOKEN_URL:
            return httpx.Response(
                200,
                json={
                    "access_token": "synthetic-access",
                    "refresh_token": "synthetic-refresh",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": AUTH_SCOPES,
                },
            )
        assert request.headers["authorization"] == "Bearer synthetic-access"
        assert request.headers["authorization"] == "Bearer synthetic-access"
        assert request.headers["authorization"] == "Bearer synthetic-access"
        if path == "/v1/me/tracks":
            return httpx.Response(
                200,
                json={
                    "items": [track.model_dump(mode="json") for track in library_tracks],
                    "next": None,
                },
            )
        if path == "/v1/me":
            return httpx.Response(200, json={"id": "syntheticAccount"})
        if path == "/v1/me/library/contains":
            assert request.method == "GET"
            ids = [
                uri.removeprefix("spotify:playlist:")
                for uri in request.url.params["uris"].split(",")
            ]
            assert len(ids) <= 40
            return httpx.Response(
                200, json=[value in state and state[value].get("in_library", True) for value in ids]
            )
        if str(request.url) == CREATE_PLAYLIST_URL:
            payload = json.loads(request.read())
            assert payload["public"] is True
            assert payload["collaborative"] is False
            assert payload["description"] == MANAGED_PLAYLIST_DESCRIPTION
            next_id += 1
            playlist_id = f"syntheticPlaylist{next_id}"
            while playlist_id in state:
                next_id += 1
                playlist_id = f"syntheticPlaylist{next_id}"
            state[playlist_id] = {
                "name": payload["name"],
                "owner": "syntheticAccount",
                "items": [],
                "description": payload["description"],
            }
            if timeout_pending:
                timeout_pending = False
                raise httpx.ReadTimeout("synthetic timeout after server-side create")
            return httpx.Response(201, json=playlist_json(playlist_id, state[playlist_id]))
        playlist_match = re.fullmatch(r"/v1/playlists/([A-Za-z0-9]+)(/items)?", path)
        if playlist_match:
            playlist_id, items_suffix = playlist_match.groups()
            if playlist_id not in state:
                return httpx.Response(404)
            playlist = state[playlist_id]
            if items_suffix is None:
                if request.method == "GET":
                    return httpx.Response(200, json=playlist_json(playlist_id, playlist))
                assert request.method == "PUT"
                payload = json.loads(request.read())
                playlist["name"] = payload["name"]
                if "description" in payload:
                    playlist["description"] = payload["description"]
                return httpx.Response(200)
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={
                        "items": [{"item": {"id": item_id}} for item_id in playlist["items"]],
                        "next": None,
                    },
                )
            payload = json.loads(request.read())
            uris = [uri.removeprefix("spotify:track:") for uri in payload["uris"]]
            if request.method == "PUT":
                playlist["items"] = uris
                return httpx.Response(200)
            assert request.method == "POST"
            playlist["items"].extend(uris)
            return httpx.Response(201)
        if path == "/v1/me/library":
            assert request.url.params["uris"].startswith("spotify:playlist:")
            assert request.method in {"PUT", "DELETE"}
            playlist_id = request.url.params["uris"].removeprefix("spotify:playlist:")
            if playlist_id in state:
                state[playlist_id]["in_library"] = request.method == "PUT"
            if request.method == "DELETE" and remove_timeout_pending:
                remove_timeout_pending = False
                raise httpx.ReadTimeout("synthetic timeout after library removal")
            return httpx.Response(200)
        raise AssertionError(f"Unexpected synthetic Spotify request: {request.method} {path}")

    store = DemoCredentialStore()
    store.save("synthetic-refresh")
    app = create_app(
        Settings(client_id="0" * 32),
        store=store,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        registry_path=registry_path,
    )
    return TestClient(app, base_url=ORIGIN), state, calls


def selected_keys(html: str) -> set[str]:
    return set(re.findall(r'name="artists" value="([^"]+)" checked', html))


def test_grouping_includes_collaborations_without_duplicate_tracks() -> None:
    item = PROTOTYPE_TRACKS[0].model_copy(deep=True)
    assert item.track
    item.track.artists.extend(
        [
            Artist(name="Convidada", id="syntheticGuest"),
            Artist(name="Convidada", id="syntheticGuest"),
        ]
    )
    source = [item, item.model_copy(deep=True), PROTOTYPE_TRACKS[3]]
    before = [entry.model_dump() for entry in source]
    groups = artist_playlists(source)
    assert groups.skipped_tracks == 0
    assert [(group.name, len(group.tracks)) for group in groups.playlists] == [
        ("Banda Aurora", 2),
        ("Convidada", 1),
    ]
    assert len(groups.playlists[0].key) == 64
    assert [entry.model_dump() for entry in source] == before
    assert [group.key for group in artist_playlists(source).playlists] == [
        group.key for group in groups.playlists
    ]


def test_artist_identity_prevents_merging_different_artists_with_same_name() -> None:
    first, second = [entry.model_copy(deep=True) for entry in PROTOTYPE_TRACKS[:2]]
    assert first.track and second.track
    first.track.artists = [Artist(name="Mesmo nome", id="syntheticOne")]
    second.track.artists = [Artist(name="Mesmo nome", id="syntheticTwo")]
    groups = artist_playlists([first, second]).playlists
    assert len(groups) == 2
    assert len({group.key for group in groups}) == 2
    assert all(len(group.tracks) == 1 for group in groups)


def test_missing_metadata_is_reported_and_name_fallback_is_explicit() -> None:
    local, no_artists, unnamed, fallback = [
        PROTOTYPE_TRACKS[0].model_copy(deep=True) for _ in range(4)
    ]
    assert local.track and no_artists.track and unnamed.track and fallback.track
    local.track.id = None
    no_artists.track.artists = []
    unnamed.track.artists = [Artist(name=" ")]
    fallback.track.artists = [Artist(name="Artista sem ID")]
    result = artist_playlists([SavedTrack(track=None), local, no_artists, unnamed, fallback])
    assert result.skipped_tracks == 4
    assert len(result.playlists) == 1
    assert result.playlists[0].name == "Artista sem ID"
    assert result.playlists[0].artist_id is None


def test_page_and_preview_require_login(browser: TestClient) -> None:
    assert browser.get(PAGE).url.path == "/entrar"
    csrf = field(browser.get("/entrar").text, "csrf")
    assert (
        browser.post(
            PREVIEW, data={"csrf": csrf, "revision": "synthetic"}, headers=HEADERS
        ).url.path
        == "/entrar"
    )


def test_demo_page_saves_selection_without_external_preview(prototype: TestClient) -> None:
    enter_demo(prototype)
    page = prototype.get(PAGE)
    assert page.status_code == 200
    assert "<h1>Playlists Gerenciadas" in page.text
    assert 'href="/app/playlists" class="active" aria-current="page"' in page.text
    assert 'aria-haspopup="dialog"' in page.text
    assert 'id="artist-playlist-dialog"' in page.text
    assert "Somente prévia nesta etapa" not in page.text
    assert 'class="managed-policy"' not in page.text
    assert "A criação real depende de uma" not in page.text
    assert "playlist-preview-card" not in page.text
    groups = artist_playlists(PROTOTYPE_TRACKS).playlists
    assert len(re.findall(r'name="artists" value="', page.text)) == len(groups) == 4
    response = post_preview(prototype, [groups[0].key, groups[1].key], page.text)
    assert response.status_code == 200
    assert response.url.path == PAGE
    assert response.url.query == b""
    assert selected_keys(response.text) == {groups[0].key, groups[1].key}
    assert "playlist-preview" not in response.text
    assert "Prévia por artista" not in response.text
    assert "Visualizar prévia" not in response.text
    assert "data-artist-save-submit>Configurar</button>" in response.text
    assert prototype.get("/static/managed-playlists.js").status_code == 200
    assert selected_keys(prototype.get(PAGE).text) == {groups[0].key, groups[1].key}


def test_artist_card_has_configuration_label_without_music_icon(prototype: TestClient) -> None:
    enter_demo(prototype)
    html = prototype.get(PAGE).text
    card = re.search(r'<button class="managed-option".*?</button>', html, re.DOTALL)
    assert card
    assert "<strong>Por artista</strong>" in card[0]
    assert '<span class="managed-option-action">Configurar</span>' in card[0]
    assert "<svg" not in card[0]
    assert "Configurar prévia" not in card[0]
    assert prototype.get("/static/artist-playlist-background.svg").status_code == 200


def test_preview_uses_entire_import_not_song_filters(prototype: TestClient) -> None:
    enter_demo(prototype)
    songs = prototype.get("/app/musicas")
    prototype.post(
        "/app/musicas/filtrar",
        data={"csrf": field(songs.text, "csrf"), "year": "2022"},
        headers=HEADERS,
    )
    page = prototype.get(PAGE)
    assert page.text.count('name="artists" value="') == 4
    group = artist_playlists(PROTOTYPE_TRACKS).playlists[0]
    assert len(group.tracks) == 2
    assert selected_keys(post_preview(prototype, [group.key]).text) == {group.key}


@pytest.mark.parametrize("kind", ["forged", "duplicate", "extra", "stale", "unicode-revision"])
def test_invalid_or_stale_selections_are_explicit(prototype: TestClient, kind: str) -> None:
    enter_demo(prototype)
    page = prototype.get(PAGE)
    key = artist_playlists(PROTOTYPE_TRACKS).playlists[0].key
    data: dict[str, str | list[str]] = {
        "csrf": field(page.text, "csrf"),
        "revision": field(page.text, "revision"),
    }
    if kind == "forged":
        data["artists"] = ["forged"]
    elif kind == "duplicate":
        data["artists"] = [key, key]
    elif kind == "extra":
        data.update({"artists": [key], "write": "true"})
    elif kind == "stale":
        data["artists"] = [key]
        prototype.post("/app/musicas/importar", data={"csrf": data["csrf"]}, headers=HEADERS)
    elif kind == "unicode-revision":
        data.update({"artists": [key], "revision": "revisão inválida"})
    response = prototype.post(PREVIEW, data=data, headers=HEADERS)
    assert response.status_code == (409 if kind in {"stale", "unicode-revision"} else 400)
    assert 'role="alert"' in response.text
    assert "data-open-on-load" in response.text
    assert "playlist-preview-card" not in response.text


def test_preview_requires_origin_and_csrf(prototype: TestClient) -> None:
    enter_demo(prototype)
    page = prototype.get(PAGE)
    data = {
        "csrf": field(page.text, "csrf"),
        "revision": field(page.text, "revision"),
        "artists": [artist_playlists(PROTOTYPE_TRACKS).playlists[0].key],
    }
    assert prototype.post(PREVIEW, data=data).status_code == 403
    assert (
        prototype.post(PREVIEW, data={**data, "csrf": "invalid"}, headers=HEADERS).status_code
        == 403
    )
    assert (
        prototype.post(
            PREVIEW, data=data, headers={"Origin": "https://external.example"}
        ).status_code
        == 403
    )


def test_invalid_selection_does_not_replace_saved_configuration(prototype: TestClient) -> None:
    enter_demo(prototype)
    key = artist_playlists(PROTOTYPE_TRACKS).playlists[0].key
    assert selected_keys(post_preview(prototype, [key]).text) == {key}
    assert post_preview(prototype, ["forged"]).status_code == 400
    assert selected_keys(prototype.get(PAGE).text) == {key}


def test_refresh_invalidates_preview_but_keeps_demo_selection(prototype: TestClient) -> None:
    enter_demo(prototype)
    keys = [group.key for group in artist_playlists(PROTOTYPE_TRACKS).playlists]
    assert selected_keys(post_preview(prototype, keys).text) == set(keys)
    with TestClient(prototype.app, base_url=ORIGIN) as other:
        enter_demo(other)
        assert selected_keys(other.get(PAGE).text) == set()
    page = prototype.get(PAGE)
    prototype.post(
        "/app/musicas/importar", data={"csrf": field(page.text, "csrf")}, headers=HEADERS
    )
    assert selected_keys(prototype.get(PAGE).text) == set(keys)
    assert post_preview(prototype, keys, page.text).status_code == 409


def test_real_selection_is_restored_for_a_new_browser_session(tmp_path: Path) -> None:
    registry_path = tmp_path / "managed.json"
    first_browser, _, _ = synthetic_spotify_app(registry_path)
    key = artist_playlists(PROTOTYPE_TRACKS).playlists[0].key
    with first_browser:
        result = post_preview(first_browser, [key])
        assert result.status_code == 200
        assert "synthetic-access" not in result.text
        assert "synthetic-refresh" not in result.text
        assert result.headers["cache-control"] == "no-store"
        assert ManagedPlaylistRegistry(registry_path).selection_for("syntheticAccount") == {key}

    second_browser, _, _ = synthetic_spotify_app(registry_path)
    with second_browser:
        assert selected_keys(second_browser.get(PAGE).text) == {key}


@pytest.mark.parametrize("kind", ["empty", "escaped", "unavailable"])
def test_empty_and_escaped_artist_collections(kind: str, tmp_path: Path) -> None:
    item = PROTOTYPE_TRACKS[0].model_dump(mode="json")
    item["track"]["artists"] = [{"name": "<script>synthetic-artist</script>"}]
    items = [] if kind == "empty" else [{"track": None}] if kind == "unavailable" else [item]

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            return httpx.Response(
                200,
                json={
                    "access_token": "synthetic-access",
                    "refresh_token": "synthetic-refresh",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": AUTH_SCOPES,
                },
            )
        if request.url.path == "/v1/me":
            return httpx.Response(200, json={"id": "syntheticAccount"})
        return httpx.Response(200, json={"items": items})

    store = DemoCredentialStore()
    store.save("synthetic-refresh")
    app = create_app(
        Settings(client_id="0" * 32),
        store=store,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        registry_path=tmp_path / "managed.json",
    )
    with TestClient(app, base_url=ORIGIN) as browser:
        page = browser.get(PAGE)
        assert page.status_code == 200
        if kind == "escaped":
            assert "&lt;script&gt;synthetic-artist&lt;/script&gt;" in page.text
            assert "<script>synthetic-artist</script>" not in page.text
            assert "Sem ID: agrupado por nome" in page.text
        else:
            assert "Nenhum artista disponível" in page.text
            assert 'data-open-artist-dialog aria-haspopup="dialog"' in page.text
            if kind == "unavailable":
                assert "1 item(ns)" in page.text


def test_expired_preview_cannot_be_submitted(
    prototype: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enter_demo(prototype)
    page = prototype.get(PAGE)
    now = time.monotonic()
    monkeypatch.setattr("music_curator.web.time.monotonic", lambda: now + 9 * 60 * 60)
    keys = [group.key for group in artist_playlists(PROTOTYPE_TRACKS).playlists]
    assert post_preview(prototype, keys, page.text).status_code == 403


def test_configuration_has_no_global_toggle_and_saves_empty_selection(
    prototype: TestClient,
) -> None:
    enter_demo(prototype)
    page = prototype.get(PAGE)
    assert 'name="enabled"' not in page.text
    assert "data-artist-enabled" not in page.text
    assert "data-artist-settings-fields disabled" not in page.text
    response = prototype.post(
        PREVIEW,
        data={"csrf": field(page.text, "csrf"), "revision": field(page.text, "revision")},
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert selected_keys(response.text) == set()
    assert "Sem vínculo com Spotify no modo demonstração" in response.text


def test_unexpected_global_toggle_field_does_not_replace_saved_selection(
    prototype: TestClient,
) -> None:
    enter_demo(prototype)
    key = artist_playlists(PROTOTYPE_TRACKS).playlists[0].key
    page = post_preview(prototype, [key])
    response = prototype.post(
        PREVIEW,
        data={
            "csrf": field(page.text, "csrf"),
            "revision": field(page.text, "revision"),
            "enabled": "true",
        },
        headers=HEADERS,
    )
    assert response.status_code == 400
    assert selected_keys(response.text) == {key}


def test_active_save_creates_and_exactly_syncs_selected_playlists(tmp_path: Path) -> None:
    registry_path = tmp_path / "managed.json"
    browser, playlists, calls = synthetic_spotify_app(registry_path)
    groups = artist_playlists(PROTOTYPE_TRACKS).playlists
    selected = groups[:2]
    keys = [group.key for group in selected]
    with browser:
        page = browser.get(PAGE)
        assert "Sem playlist vinculada" in page.text
        assert "managed-modal-note" not in page.text
        assert "Ao salvar, a seleção fica salva para esta conta" not in page.text
        result = post_preview(browser, keys, page.text)
        assert result.status_code == 200
        assert "Todas as faixas foram confirmadas no Spotify." in result.text
        assert "Abrir playlist no Spotify (ID:" in result.text
        assert len(playlists) == 2
        registry = ManagedPlaylistRegistry(registry_path)
        records = registry.records_for("syntheticAccount")
        assert set(records) == set(keys)
        assert registry.selection_for("syntheticAccount") == set(keys)
        for group in selected:
            record = records[group.key]
            assert record.state == "active"
            assert record.playlist_id is not None
            expected_ids = [track.id for track in group.tracks]
            assert playlists[record.playlist_id]["items"] == expected_ids
            row_pattern = (
                rf'<label class="artist-option"[^>]*data-artist-name="'
                rf'{re.escape(group.name)}".*?</label>'
            )
            artist_row = re.search(
                row_pattern,
                browser.get(PAGE).text,
                re.DOTALL,
            )
            assert artist_row
            assert 'class="artist-playlist-reference"' in artist_row[0]
            assert f"Playlist ID: <span>{record.playlist_id}</span>" in artist_row[0]

        first_record = records[selected[0].key]
        assert first_record.playlist_id is not None
        playlists[first_record.playlist_id]["items"].append("syntheticManualTrack")
        page = browser.get(PAGE)
        result = post_preview(browser, keys, page.text)
        assert result.status_code == 200
        assert playlists[first_record.playlist_id]["items"] == [
            track.id for track in selected[0].tracks
        ]
        assert calls.count(("PUT", f"/v1/playlists/{first_record.playlist_id}/items")) == 2


def test_deselection_removes_from_library_and_reselection_creates_new_playlist(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "managed.json"
    browser, playlists, calls = synthetic_spotify_app(registry_path)
    group = artist_playlists(PROTOTYPE_TRACKS).playlists[0]
    with browser:
        result = post_preview(browser, [group.key])
        assert result.status_code == 200
        registry = ManagedPlaylistRegistry(registry_path)
        record = registry.records_for("syntheticAccount")[group.key]
        playlist_id = record.playlist_id
        assert playlist_id is not None

        page = browser.get(PAGE)
        removed = post_preview(browser, [], page.text)
        assert removed.status_code == 200
        assert "removida de Sua Biblioteca" in removed.text, re.findall(
            r'<li class="managed-result.*?</li>', removed.text, re.DOTALL
        )
        assert ("DELETE", "/v1/me/library") in calls
        assert registry.records_for("syntheticAccount") == {}
        assert registry.selection_for("syntheticAccount") == set()
        assert playlist_id in playlists

        page = browser.get(PAGE)
        restored = post_preview(browser, [group.key], page.text)
        assert restored.status_code == 200
        assert "Todas as faixas foram confirmadas no Spotify." in restored.text, re.findall(
            r'<li class="managed-result.*?</li>', restored.text, re.DOTALL
        )
        assert calls.count(("POST", "/v1/me/playlists")) == 2
        restored_record = registry.records_for("syntheticAccount")[group.key]
        assert restored_record.playlist_id != playlist_id
        assert restored_record.state == "active"


def test_uncertain_library_removal_keeps_id_and_blocks_recreation(tmp_path: Path) -> None:
    registry_path = tmp_path / "managed.json"
    browser, playlists, calls = synthetic_spotify_app(registry_path, timeout_remove_once=True)
    group = artist_playlists(PROTOTYPE_TRACKS).playlists[0]
    with browser:
        assert post_preview(browser, [group.key]).status_code == 200
        registry = ManagedPlaylistRegistry(registry_path)
        playlist_id = registry.records_for("syntheticAccount")[group.key].playlist_id
        assert playlist_id is not None

        removal = post_preview(browser, [])
        assert removal.status_code == 200
        assert "nao confirmou a remocao" in removal.text.lower()
        retained = registry.records_for("syntheticAccount")[group.key]
        assert retained.playlist_id == playlist_id
        assert retained.state == "uncertain"

        selection = post_preview(browser, [group.key])
        assert selection.status_code == 409
        assert "resultado incerto" in selection.text.lower()
        assert registry.records_for("syntheticAccount")[group.key].playlist_id == playlist_id
        assert calls.count(("POST", "/v1/me/playlists")) == 1
        assert playlist_id in playlists


def test_legacy_removed_record_is_not_reused_when_artist_is_selected(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "managed.json"
    registry = ManagedPlaylistRegistry(registry_path)
    legacy_record = ManagedPlaylistRecord(
        artist_key=artist_playlists(PROTOTYPE_TRACKS).playlists[0].key,
        artist_id="syntheticArtist",
        artist_name="Artista sintético",
        playlist_id="legacyPlaylist",
        owner_id="syntheticAccount",
        state="removed",
    )
    registry.save_record("syntheticAccount", legacy_record)
    browser, _, calls = synthetic_spotify_app(registry_path)

    with browser:
        result = post_preview(browser, [legacy_record.artist_key])

    assert result.status_code == 200
    assert "Todas as faixas foram confirmadas no Spotify." in result.text
    assert calls.count(("POST", "/v1/me/playlists")) == 1
    assert ("PUT", "/v1/me/library") not in calls
    recreated = registry.records_for("syntheticAccount")[legacy_record.artist_key]
    assert recreated.playlist_id != legacy_record.playlist_id
    assert recreated.state == "active"


def test_large_artist_playlist_uses_spotify_batches_of_one_hundred(tmp_path: Path) -> None:
    tracks: list[SavedTrack] = []
    for index in range(205):
        track = PROTOTYPE_TRACKS[0].model_copy(deep=True)
        assert track.track is not None
        track.track.id = f"syntheticBatch{index}"
        tracks.append(track)
    registry_path = tmp_path / "managed.json"
    browser, playlists, calls = synthetic_spotify_app(registry_path, tracks=tracks)
    group = artist_playlists(tracks).playlists[0]

    with browser:
        result = post_preview(browser, [group.key])
        assert result.status_code == 200
        assert "Todas as faixas foram confirmadas no Spotify." in result.text
        record = ManagedPlaylistRegistry(registry_path).records_for("syntheticAccount")[group.key]
        assert record.playlist_id is not None
        assert playlists[record.playlist_id]["items"] == [track.id for track in group.tracks]
        item_path = f"/v1/playlists/{record.playlist_id}/items"
        assert calls.count(("PUT", item_path)) == 1
        assert calls.count(("POST", item_path)) == 2


def test_uncertain_create_is_persisted_and_not_retried(tmp_path: Path) -> None:
    registry_path = tmp_path / "managed.json"
    browser, playlists, calls = synthetic_spotify_app(registry_path, timeout_create_once=True)
    group = artist_playlists(PROTOTYPE_TRACKS).playlists[0]
    with browser:
        result = post_preview(browser, [group.key])
        assert result.status_code == 200
        assert "a criacao pode ter ocorrido" in result.text.lower()
        registry = ManagedPlaylistRegistry(registry_path)
        record = registry.records_for("syntheticAccount")[group.key]
        assert record.state == "creation_uncertain"
        assert len(playlists) == 1

        page = browser.get(PAGE)
        retry = post_preview(browser, [group.key], page.text)
        assert retry.status_code == 409
        assert "resultado incerto" in retry.text.lower()
        assert calls.count(("POST", "/v1/me/playlists")) == 1


def confirm_replacement(
    browser: TestClient, page: httpx.Response, keys: list[str] | None = None
) -> httpx.Response:
    response = browser.post(
        PREVIEW,
        data={
            "csrf": field(page.text, "csrf"),
            "revision": field(page.text, "revision"),
            "confirmation": field(page.text, "confirmation"),
            "artists": sorted(selected_keys(page.text)) if keys is None else keys,
        },
        headers=HEADERS,
    )
    assert isinstance(response, httpx.Response)
    return response


def playlist_writes(calls: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(method, path) for method, path in calls if method != "GET" and path != "/api/token"]


@pytest.mark.parametrize("deleted", [False, True])
def test_missing_playlist_requires_choice_and_creates_only_after_confirmation(
    tmp_path: Path, deleted: bool
) -> None:
    path = tmp_path / "managed.json"
    browser, remote, calls = synthetic_spotify_app(path)
    group = artist_playlists(PROTOTYPE_TRACKS).playlists[0]
    registry = ManagedPlaylistRegistry(path)
    with browser:
        assert post_preview(browser, [group.key]).status_code == 200
        original_id = registry.records_for("syntheticAccount")[group.key].playlist_id
        assert original_id is not None
        if deleted:
            del remote[original_id]
        else:
            remote[original_id]["in_library"] = False
        before = path.read_bytes()
        calls.clear()
        conflict = post_preview(browser, [group.key])
        assert conflict.status_code == 409
        assert "Resolva os conflitos aqui" in conflict.text
        assert "data-open-on-load" in conflict.text
        assert "data-playlist-conflicts" in conflict.text
        assert selected_keys(conflict.text) == {group.key}
        assert f"Playlist ID: <span>{original_id}</span>" in conflict.text
        assert (
            "Identificamos que uma ou mais playlists gerenciadas foram excluídas" in conflict.text
        )
        assert "Caso não queira recriá-las, desmarque o artista" in conflict.text
        assert '<strong>"Confirma e Salvar"</strong>' in conflict.text
        assert "Playlists removidas:" in conflict.text
        assert "Voce pode confirmar a criacao" not in conflict.text
        assert "Depois que elas começarem" not in conflict.text
        assert original_id in conflict.text
        assert "Confirmar e Salvar" in conflict.text
        assert "Confirmar substituições e salvar" not in conflict.text
        assert "Ausente de Sua Biblioteca" in conflict.text
        dialog = re.search(r"<dialog\b.*?</dialog>", conflict.text, re.DOTALL)
        assert dialog
        assert dialog[0].count('type="submit"') == 2
        assert re.findall(r"data-artist-save-submit[^>]*>([^<]+)</button>", dialog[0]) == [
            "Confirmar e Salvar"
        ]
        assert "playlist-reconnect" not in dialog[0]
        assert playlist_writes(calls) == []
        assert path.read_bytes() == before

        result = confirm_replacement(browser, conflict)
        assert result.status_code == 200
        new_record = registry.records_for("syntheticAccount")[group.key]
        assert new_record.playlist_id is not None
        assert new_record.playlist_id != original_id
        assert remote[new_record.playlist_id]["items"] == [track.id for track in group.tracks]
        assert ("DELETE", "/v1/me/library") not in calls
        created_count = calls.count(("POST", "/v1/me/playlists"))
        replay = confirm_replacement(browser, conflict)
        assert replay.status_code == 409
        assert calls.count(("POST", "/v1/me/playlists")) == created_count


def test_absent_deselected_playlist_can_be_cleared_without_remote_mutation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "managed.json"
    browser, remote, calls = synthetic_spotify_app(path)
    group = artist_playlists(PROTOTYPE_TRACKS).playlists[0]
    registry = ManagedPlaylistRegistry(path)
    with browser:
        assert post_preview(browser, [group.key]).status_code == 200
        record = registry.records_for("syntheticAccount")[group.key]
        assert record.playlist_id is not None
        remote[record.playlist_id]["in_library"] = False
        calls.clear()
        conflict = post_preview(browser, [group.key])
        assert conflict.status_code == 409
        assert registry.selection_for("syntheticAccount") == {group.key}
        result = confirm_replacement(browser, conflict, [])
        assert result.status_code == 200
        assert registry.records_for("syntheticAccount") == {}
        assert registry.selection_for("syntheticAccount") == set()
        assert playlist_writes(calls) == []


@pytest.mark.parametrize("change", ["private", "collaborative", "owner", "items"])
def test_validation_failure_in_one_artist_blocks_entire_submission(
    tmp_path: Path, change: str
) -> None:
    fail_items = False

    def override(request: httpx.Request) -> httpx.Response | None:
        if fail_items and request.url.path == "/v1/playlists/syntheticPlaylist2/items":
            return httpx.Response(404)
        return None

    path = tmp_path / "managed.json"
    browser, remote, calls = synthetic_spotify_app(path, override=override)
    keys = [group.key for group in artist_playlists(PROTOTYPE_TRACKS).playlists]
    with browser:
        assert post_preview(browser, keys[:2]).status_code == 200
        before = path.read_bytes()
        if change == "private":
            remote["syntheticPlaylist2"]["public"] = False
        elif change == "collaborative":
            remote["syntheticPlaylist2"]["collaborative"] = True
        elif change == "owner":
            remote["syntheticPlaylist2"]["owner"] = "anotherAccount"
        else:
            fail_items = True
        remote["syntheticPlaylist1"]["items"].append("syntheticManualTrack")
        calls.clear()
        result = post_preview(browser, keys[:3])
        assert result.status_code == 409
        assert "Resolva os conflitos aqui" in result.text
        assert selected_keys(result.text) == set(keys[:3])
        assert 'name="confirmation"' not in result.text
        assert path.read_bytes() == before
        assert remote["syntheticPlaylist1"]["items"][-1] == "syntheticManualTrack"
        assert playlist_writes(calls) == []


@pytest.mark.parametrize("failure", ["timeout", "401", "403", "429", "500", "malformed"])
def test_library_verification_failure_never_authorizes_replacement(
    tmp_path: Path, failure: str
) -> None:
    failing = False

    def override(request: httpx.Request) -> httpx.Response | None:
        if not failing or request.url.path != "/v1/me/library/contains":
            return None
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic-private-error")
        if failure == "malformed":
            return httpx.Response(200, json=["false"])
        return httpx.Response(int(failure))

    path = tmp_path / "managed.json"
    browser, _, calls = synthetic_spotify_app(path, override=override)
    key = artist_playlists(PROTOTYPE_TRACKS).playlists[0].key
    with browser:
        assert post_preview(browser, [key]).status_code == 200
        before = path.read_bytes()
        failing = True
        calls.clear()
        result = post_preview(browser, [key])
        assert result.status_code == 502
        assert "Resolva os conflitos aqui" in result.text
        assert selected_keys(result.text) == {key}
        assert "Playlist ID: <span>syntheticPlaylist1</span>" in result.text
        assert "data-open-on-load" in result.text
        assert 'name="confirmation"' not in result.text
        assert "synthetic-private-error" not in result.text
        assert playlist_writes(calls) == []
        assert path.read_bytes() == before


@pytest.mark.parametrize("status", [403, 404, 500])
def test_inaccessible_details_do_not_prove_absence(tmp_path: Path, status: int) -> None:
    failing = False

    def override(request: httpx.Request) -> httpx.Response | None:
        if failing and request.url.path == "/v1/playlists/syntheticPlaylist1":
            return httpx.Response(status)
        return None

    path = tmp_path / "managed.json"
    browser, _, calls = synthetic_spotify_app(path, override=override)
    key = artist_playlists(PROTOTYPE_TRACKS).playlists[0].key
    with browser:
        assert post_preview(browser, [key]).status_code == 200
        before = path.read_bytes()
        failing = True
        calls.clear()
        result = post_preview(browser, [key])
        assert result.status_code == 409
        assert "sera necessario criar uma nova" in result.text
        assert 'name="confirmation"' not in result.text
        assert playlist_writes(calls) == []
        assert path.read_bytes() == before


@pytest.mark.parametrize("change", ["restored", "registry", "cancel", "collection", "expiry"])
def test_replacement_confirmation_is_invalidated_by_changed_context(
    tmp_path: Path, change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "managed.json"
    browser, remote, calls = synthetic_spotify_app(path)
    key = artist_playlists(PROTOTYPE_TRACKS).playlists[0].key
    registry = ManagedPlaylistRegistry(path)
    with browser:
        assert post_preview(browser, [key]).status_code == 200
        remote["syntheticPlaylist1"]["in_library"] = False
        conflict = post_preview(browser, [key])
        assert conflict.status_code == 409
        if change == "restored":
            remote["syntheticPlaylist1"]["in_library"] = True
        elif change == "registry":
            registry.save_selection("syntheticAccount", set())
        elif change == "cancel":
            assert browser.get(PAGE).status_code == 200
        elif change == "collection":
            browser.post(
                "/app/musicas/importar",
                data={"csrf": field(conflict.text, "csrf")},
                headers=HEADERS,
            )
        else:
            now = time.monotonic()
            monkeypatch.setattr("music_curator.web.time.monotonic", lambda: now + 601)
        before = path.read_bytes()
        calls.clear()
        result = confirm_replacement(browser, conflict)
        assert result.status_code in {409, 502}
        assert playlist_writes(calls) == []
        assert path.read_bytes() == before


def test_two_browser_confirmations_cannot_create_two_replacements(tmp_path: Path) -> None:
    path = tmp_path / "managed.json"
    browser, remote, calls = synthetic_spotify_app(path)
    key = artist_playlists(PROTOTYPE_TRACKS).playlists[0].key
    with browser, TestClient(browser.app, base_url=ORIGIN) as other:
        assert post_preview(browser, [key]).status_code == 200
        remote["syntheticPlaylist1"]["in_library"] = False
        first_conflict = post_preview(browser, [key])
        second_conflict = post_preview(other, [key])
        assert first_conflict.status_code == second_conflict.status_code == 409
        calls.clear()
        assert confirm_replacement(browser, first_conflict).status_code == 200
        assert confirm_replacement(other, second_conflict).status_code == 502
        assert calls.count(("POST", "/v1/me/playlists")) == 1


def test_known_absence_and_unresolved_conflict_do_not_offer_confirmation(tmp_path: Path) -> None:
    path = tmp_path / "managed.json"
    browser, remote, calls = synthetic_spotify_app(path)
    keys = [group.key for group in artist_playlists(PROTOTYPE_TRACKS).playlists[:2]]
    with browser:
        assert post_preview(browser, keys).status_code == 200
        remote["syntheticPlaylist1"]["in_library"] = False
        remote["syntheticPlaylist2"]["public"] = False
        before = path.read_bytes()
        calls.clear()
        result = post_preview(browser, keys)
        assert result.status_code == 409
        assert "nao esta em Sua Biblioteca" in result.text
        assert "deixou de ser publica" in result.text
        assert 'name="confirmation"' not in result.text
        assert playlist_writes(calls) == []
        assert path.read_bytes() == before


def test_management_permissions_are_checked_before_creating_any_playlist(tmp_path: Path) -> None:
    def override(request: httpx.Request) -> httpx.Response | None:
        if str(request.url) == TOKEN_URL:
            return httpx.Response(
                200,
                json={
                    "access_token": "synthetic-access",
                    "refresh_token": "synthetic-refresh",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": "user-library-read user-read-private",
                },
            )
        return None

    path = tmp_path / "managed.json"
    browser, _, calls = synthetic_spotify_app(path, override=override)
    key = artist_playlists(PROTOTYPE_TRACKS).playlists[0].key
    with browser:
        result = post_preview(browser, [key])
        assert result.status_code == 502
        assert "playlist-modify-public" in result.text
        assert "Se precisar reconectar o Spotify" in result.text
        assert playlist_writes(calls) == []
        assert not path.exists()


def test_duplicate_registered_destination_blocks_all_changes(tmp_path: Path) -> None:
    path = tmp_path / "managed.json"
    browser, _, calls = synthetic_spotify_app(path)
    groups = artist_playlists(PROTOTYPE_TRACKS).playlists[:2]
    keys = [group.key for group in groups]
    registry = ManagedPlaylistRegistry(path)
    with browser:
        assert post_preview(browser, keys).status_code == 200
        records = registry.records_for("syntheticAccount")
        registry.save_record(
            "syntheticAccount",
            records[keys[1]].model_copy(update={"playlist_id": records[keys[0]].playlist_id}),
        )
        before = path.read_bytes()
        calls.clear()
        result = post_preview(browser, keys)
        assert result.status_code == 409
        assert "mais de um artista" in result.text
        assert 'name="confirmation"' not in result.text
        assert playlist_writes(calls) == []
        assert path.read_bytes() == before


@pytest.mark.parametrize("invalid", ["token", "csrf", "origin"])
def test_replacement_confirmation_rejects_forged_requests(tmp_path: Path, invalid: str) -> None:
    path = tmp_path / "managed.json"
    browser, remote, calls = synthetic_spotify_app(path)
    key = artist_playlists(PROTOTYPE_TRACKS).playlists[0].key
    with browser:
        assert post_preview(browser, [key]).status_code == 200
        remote["syntheticPlaylist1"]["in_library"] = False
        conflict = post_preview(browser, [key])
        data: dict[str, str | list[str]] = {
            "csrf": field(conflict.text, "csrf"),
            "revision": field(conflict.text, "revision"),
            "confirmation": field(conflict.text, "confirmation"),
            "artists": [key],
        }
        if invalid == "token":
            data["confirmation"] = "forged"
        elif invalid == "csrf":
            data["csrf"] = "forged"
        before = path.read_bytes()
        calls.clear()
        result = browser.post(
            PREVIEW,
            data=data,
            headers={"Origin": "https://external.example"} if invalid == "origin" else HEADERS,
        )
        assert result.status_code == (409 if invalid == "token" else 403)
        assert playlist_writes(calls) == []
        assert path.read_bytes() == before


def test_confirmation_uses_current_modal_selection_and_cleans_deselected_missing_binding(
    tmp_path: Path,
) -> None:
    path = tmp_path / "managed.json"
    browser, remote, calls = synthetic_spotify_app(path)
    keys = [group.key for group in artist_playlists(PROTOTYPE_TRACKS).playlists[:2]]
    registry = ManagedPlaylistRegistry(path)
    with browser:
        assert post_preview(browser, keys).status_code == 200
        remote["syntheticPlaylist1"]["in_library"] = False
        remote["syntheticPlaylist2"]["in_library"] = False
        conflict = post_preview(browser, keys)
        assert conflict.status_code == 409
        calls.clear()
        result = confirm_replacement(browser, conflict, [keys[0]])
        assert result.status_code == 200
        assert registry.selection_for("syntheticAccount") == {keys[0]}
        records = registry.records_for("syntheticAccount")
        assert set(records) == {keys[0]}
        assert records[keys[0]].playlist_id == "syntheticPlaylist3"
        assert calls.count(("POST", "/v1/me/playlists")) == 1
        assert ("DELETE", "/v1/me/library") not in calls


def test_cancel_conflicts_restores_saved_selection_without_mutation(tmp_path: Path) -> None:
    path = tmp_path / "managed.json"
    browser, remote, calls = synthetic_spotify_app(path)
    keys = [group.key for group in artist_playlists(PROTOTYPE_TRACKS).playlists[:2]]
    with browser:
        assert post_preview(browser, keys[:1]).status_code == 200
        remote["syntheticPlaylist1"]["in_library"] = False
        before = path.read_bytes()
        calls.clear()
        conflict = post_preview(browser, keys)
        assert conflict.status_code == 409
        assert selected_keys(conflict.text) == set(keys)
        cancelled = browser.get(PAGE)
        assert selected_keys(cancelled.text) == set(keys[:1])
        assert "data-playlist-conflicts" not in cancelled.text
        assert confirm_replacement(browser, conflict).status_code == 409
        assert playlist_writes(calls) == []
        assert path.read_bytes() == before


def refresh_artists(
    browser: TestClient, html: str, keys: list[str], *, as_json: bool = False
) -> httpx.Response:
    response = browser.post(
        "/app/playlists/por-artista/atualizar",
        data={
            "csrf": field(html, "csrf"),
            "revision": field(html, "revision"),
            "artists": keys,
        },
        headers={**HEADERS, "Accept": "application/json"} if as_json else HEADERS,
    )
    assert isinstance(response, httpx.Response)
    return response


def test_refresh_button_is_between_cancel_and_configure_and_demo_stays_synthetic(
    prototype: TestClient,
) -> None:
    enter_demo(prototype)
    html = prototype.get(PAGE).text
    footer = re.search(r'<div class="playlist-dialog-footer">.*?</form>', html, re.DOTALL)
    assert footer
    assert re.findall(r"<button[^>]*>([^<]+)</button>", footer[0]) == [
        "Cancelar",
        "Atualizar",
        "Configurar",
    ]
    key = artist_playlists(PROTOTYPE_TRACKS).playlists[0].key
    result = refresh_artists(prototype, html, [key])
    assert result.status_code == 200
    assert "data-open-on-load" in result.text
    assert selected_keys(result.text) == {key}
    assert "ainda nao foram salvas" in result.text


def test_refresh_updates_all_tracks_and_artists_without_saving_or_spotify_writes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "managed.json"
    tracks = [PROTOTYPE_TRACKS[0].model_copy(deep=True)]
    browser, _, calls = synthetic_spotify_app(path, tracks=tracks)
    original = artist_playlists(tracks).playlists[0]
    with browser:
        html = browser.get(PAGE).text
        tracks.extend(item.model_copy(deep=True) for item in PROTOTYPE_TRACKS[1:4])
        calls.clear()
        result = refresh_artists(browser, html, [original.key])
        assert result.status_code == 200
        assert "data-open-on-load" in result.text
        assert selected_keys(result.text) == {original.key}
        assert field(result.text, "revision") != field(html, "revision")
        assert result.text.count('name="artists" value="') == len(
            artist_playlists(tracks).playlists
        )
        assert "2 músicas" in result.text
        assert calls.count(("GET", "/v1/me/tracks")) == 1
        assert playlist_writes(calls) == []
        assert not path.exists()
        assert f"{len(tracks)} músicas importadas" in browser.get("/app/musicas").text


def test_refresh_preserves_unsaved_marks_and_existing_ids_until_configure(
    tmp_path: Path,
) -> None:
    path = tmp_path / "managed.json"
    tracks = [item.model_copy(deep=True) for item in PROTOTYPE_TRACKS]
    browser, remote, calls = synthetic_spotify_app(path, tracks=tracks)
    keys = [group.key for group in artist_playlists(tracks).playlists[:2]]
    registry = ManagedPlaylistRegistry(path)
    with browser:
        saved = post_preview(browser, keys[:1])
        before = path.read_bytes()
        calls.clear()
        updated = refresh_artists(browser, saved.text, keys[1:])
        assert updated.status_code == 200
        assert selected_keys(updated.text) == set(keys[1:])
        assert "Playlist ID: <span>syntheticPlaylist1</span>" in updated.text
        assert registry.selection_for("syntheticAccount") == set(keys[:1])
        assert path.read_bytes() == before
        assert len(remote) == 1
        assert playlist_writes(calls) == []
        assert selected_keys(browser.get(PAGE).text) == set(keys[:1])


def test_refresh_warns_about_disappeared_artist_without_removing_playlist(
    tmp_path: Path,
) -> None:
    path = tmp_path / "managed.json"
    tracks = [PROTOTYPE_TRACKS[0].model_copy(deep=True)]
    browser, _, calls = synthetic_spotify_app(path, tracks=tracks)
    key = artist_playlists(tracks).playlists[0].key
    with browser:
        saved = post_preview(browser, [key])
        before = path.read_bytes()
        tracks.clear()
        calls.clear()
        result = refresh_artists(browser, saved.text, [key])
        assert result.status_code == 200
        assert "1 artista(s) marcado(s) nao estao mais na colecao" in result.text
        assert selected_keys(result.text) == set()
        assert 'name="artists"' not in result.text
        assert path.read_bytes() == before
        assert playlist_writes(calls) == []


def test_refresh_failure_keeps_previous_complete_collection_and_draft(
    tmp_path: Path,
) -> None:
    failing = False

    def override(request: httpx.Request) -> httpx.Response | None:
        if failing and request.url.path == "/v1/me/tracks":
            if "offset" in request.url.params:
                return httpx.Response(500, text="synthetic-private-response")
            partial = PROTOTYPE_TRACKS[0].model_dump(mode="json")
            partial["track"]["name"] = "syntheticPartialNotPublished"
            return httpx.Response(
                200,
                json={"items": [partial], "next": "https://api.spotify.com/v1/me/tracks?offset=1"},
            )
        return None

    path = tmp_path / "managed.json"
    browser, _, calls = synthetic_spotify_app(path, override=override)
    key = artist_playlists(PROTOTYPE_TRACKS).playlists[0].key
    with browser:
        saved = post_preview(browser, [key])
        before = path.read_bytes()
        failing = True
        calls.clear()
        result = refresh_artists(browser, saved.text, [key])
        assert result.status_code == 502
        assert "data-open-on-load" in result.text
        assert 'role="alert"' in result.text
        assert "syntheticPartialNotPublished" not in result.text
        assert "synthetic-private-response" not in result.text
        assert selected_keys(result.text) == {key}
        assert "Playlist ID: <span>syntheticPlaylist1</span>" in result.text
        assert path.read_bytes() == before
        assert playlist_writes(calls) == []


def test_refresh_invalidates_old_replacement_confirmation_and_uses_new_tracks_on_save(
    tmp_path: Path,
) -> None:
    path = tmp_path / "managed.json"
    tracks = [PROTOTYPE_TRACKS[0].model_copy(deep=True)]
    browser, remote, calls = synthetic_spotify_app(path, tracks=tracks)
    key = artist_playlists(tracks).playlists[0].key
    with browser:
        assert post_preview(browser, [key]).status_code == 200
        remote["syntheticPlaylist1"]["in_library"] = False
        conflict = post_preview(browser, [key])
        assert conflict.status_code == 409
        tracks.append(PROTOTYPE_TRACKS[3].model_copy(deep=True))
        calls.clear()
        refreshed = refresh_artists(browser, conflict.text, [key])
        assert refreshed.status_code == 200
        assert 'name="confirmation"' not in refreshed.text
        assert playlist_writes(calls) == []
        assert confirm_replacement(browser, conflict).status_code == 409
        new_conflict = post_preview(browser, [key], refreshed.text)
        assert new_conflict.status_code == 409
        result = confirm_replacement(browser, new_conflict)
        assert result.status_code == 200
        assert remote["syntheticPlaylist2"]["items"] == [
            track.id for track in artist_playlists(tracks).playlists[0].tracks
        ]


@pytest.mark.parametrize("invalid", ["origin", "csrf", "revision", "artist", "extra"])
def test_modal_refresh_rejects_invalid_forms_without_importing(
    tmp_path: Path, invalid: str
) -> None:
    browser, _, calls = synthetic_spotify_app(tmp_path / "managed.json")
    with browser:
        html = browser.get(PAGE).text
        data: dict[str, str | list[str]] = {
            "csrf": field(html, "csrf"),
            "revision": field(html, "revision"),
        }
        if invalid in {"csrf", "revision"}:
            data[invalid] = "forged"
        elif invalid == "artist":
            data["artists"] = ["forged"]
        elif invalid == "extra":
            data["confirmation"] = "forged"
        calls.clear()
        result = browser.post(
            "/app/playlists/por-artista/atualizar",
            data=data,
            headers={"Origin": "https://external.example"} if invalid == "origin" else HEADERS,
        )
        assert result.status_code == (
            403 if invalid in {"origin", "csrf"} else 409 if invalid == "revision" else 400
        )
        assert calls == []


def test_background_refresh_returns_updated_html_and_saved_selection_without_redirect(
    tmp_path: Path,
) -> None:
    path = tmp_path / "managed.json"
    tracks = [PROTOTYPE_TRACKS[0].model_copy(deep=True)]
    browser, _, calls = synthetic_spotify_app(path, tracks=tracks)
    key = artist_playlists(tracks).playlists[0].key
    with browser:
        saved = post_preview(browser, [key])
        before = path.read_bytes()
        tracks.extend(item.model_copy(deep=True) for item in PROTOTYPE_TRACKS[1:4])
        calls.clear()
        result = refresh_artists(browser, saved.text, [], as_json=True)
        assert result.status_code == 200
        assert result.headers["content-type"] == "application/json"
        assert result.history == []
        data = result.json()
        assert data["success"] is True
        assert data["selected_artists"] == [key]
        assert selected_keys(data["html"]) == set()
        assert data["revision"] == field(data["html"], "revision")
        assert "2 músicas" in data["html"]
        assert "connect-src 'self'" in result.headers["content-security-policy"]
        assert result.headers["cache-control"] == "no-store"
        assert path.read_bytes() == before
        assert playlist_writes(calls) == []


def test_background_refresh_failure_reports_error_and_retains_complete_collection(
    tmp_path: Path,
) -> None:
    failing = False

    def override(request: httpx.Request) -> httpx.Response | None:
        if failing and request.url.path == "/v1/me/tracks":
            return httpx.Response(500, text="synthetic-private-response")
        return None

    path = tmp_path / "managed.json"
    browser, _, calls = synthetic_spotify_app(path, override=override)
    key = artist_playlists(PROTOTYPE_TRACKS).playlists[0].key
    with browser:
        saved = post_preview(browser, [key])
        before = path.read_bytes()
        failing = True
        calls.clear()
        result = refresh_artists(browser, saved.text, [key], as_json=True)
        assert result.status_code == 502
        assert result.history == []
        data = result.json()
        assert data["success"] is False
        assert isinstance(data["message"], str)
        assert selected_keys(data["html"]) == {key}
        assert data["revision"] == field(saved.text, "revision")
        assert "synthetic-private-response" not in result.text
        assert path.read_bytes() == before
        assert playlist_writes(calls) == []


@pytest.mark.parametrize(
    "description", [None, "Null", "", "  ", "null", PREVIOUS_MANAGED_PLAYLIST_DESCRIPTION]
)
def test_configure_fills_managed_description_and_preserves_playlist_id(
    tmp_path: Path, description: str | None
) -> None:
    path = tmp_path / "managed.json"
    browser, remote, calls = synthetic_spotify_app(path)
    group = artist_playlists(PROTOTYPE_TRACKS).playlists[0]
    with browser:
        assert post_preview(browser, [group.key]).status_code == 200
        remote["syntheticPlaylist1"]["description"] = description
        calls.clear()
        assert browser.get(PAGE).status_code == 200
        assert remote["syntheticPlaylist1"]["description"] == description
        assert playlist_writes(calls) == []
        result = post_preview(browser, [group.key])
        assert result.status_code == 200
        assert "Todas as faixas foram confirmadas" in result.text
        assert remote["syntheticPlaylist1"]["description"] == MANAGED_PLAYLIST_DESCRIPTION
        assert len(remote) == 1
        assert calls.count(("PUT", "/v1/playlists/syntheticPlaylist1")) == 1
        record = ManagedPlaylistRegistry(path).records_for("syntheticAccount")[group.key]
        assert record.playlist_id == "syntheticPlaylist1"
        calls.clear()
        assert post_preview(browser, [group.key]).status_code == 200
        assert playlist_writes(calls) == []


def test_custom_description_is_preserved_even_when_name_is_updated(tmp_path: Path) -> None:
    browser, remote, calls = synthetic_spotify_app(tmp_path / "managed.json")
    group = artist_playlists(PROTOTYPE_TRACKS).playlists[0]
    with browser:
        assert post_preview(browser, [group.key]).status_code == 200
        remote["syntheticPlaylist1"]["description"] = "Texto personalizado pelo usuario"
        calls.clear()
        unchanged = post_preview(browser, [group.key])
        assert unchanged.status_code == 200
        assert playlist_writes(calls) == []
        remote["syntheticPlaylist1"]["name"] = "Outro nome"
        renamed = post_preview(browser, [group.key])
        assert renamed.status_code == 200
        assert "Todas as faixas foram confirmadas" in renamed.text
        assert remote["syntheticPlaylist1"]["name"] == group.name
        assert remote["syntheticPlaylist1"]["description"] == "Texto personalizado pelo usuario"


@pytest.mark.parametrize(
    "outcome",
    ["rejected", "timeout", "timeout_applied", "not_applied", "verify_failed", "owner_changed"],
)
def test_description_update_is_verified_without_blind_retry(tmp_path: Path, outcome: str) -> None:
    updating = False
    metadata_sent = False

    def override(request: httpx.Request) -> httpx.Response | None:
        nonlocal metadata_sent
        if not updating or request.url.path != "/v1/playlists/syntheticPlaylist1":
            return None
        if request.method == "PUT":
            metadata_sent = True
            if outcome == "rejected":
                return httpx.Response(400)
            if outcome == "not_applied":
                return httpx.Response(200)
            if outcome in {"timeout", "timeout_applied"}:
                if outcome == "timeout_applied":
                    remote["syntheticPlaylist1"]["description"] = MANAGED_PLAYLIST_DESCRIPTION
                    remote["syntheticPlaylist1"]["name"] = group.name
                raise httpx.ReadTimeout("synthetic private error, never display")
            if outcome == "owner_changed":
                remote["syntheticPlaylist1"]["owner"] = "otherAccount"
        elif metadata_sent and outcome == "verify_failed":
            return httpx.Response(500)
        return None

    path = tmp_path / "managed.json"
    browser, remote, calls = synthetic_spotify_app(path, override=override)
    group = artist_playlists(PROTOTYPE_TRACKS).playlists[0]
    with browser:
        assert post_preview(browser, [group.key]).status_code == 200
        remote["syntheticPlaylist1"]["description"] = None
        remote["syntheticPlaylist1"]["name"] = "Nome anterior"
        remote["syntheticPlaylist1"]["items"].append("syntheticManualTrack")
        updating = True
        calls.clear()
        result = post_preview(browser, [group.key])
        assert result.status_code == 200
        assert calls.count(("PUT", "/v1/playlists/syntheticPlaylist1")) == 1
        assert ("POST", "/v1/me/playlists") not in calls
        assert "synthetic private error" not in result.text
        if outcome == "timeout_applied":
            assert "Todas as faixas foram confirmadas" in result.text
            assert remote["syntheticPlaylist1"]["description"] == MANAGED_PLAYLIST_DESCRIPTION
        else:
            assert "Parcial" in result.text
            assert "Todas as faixas foram confirmadas" not in result.text
            assert remote["syntheticPlaylist1"]["items"][-1] == "syntheticManualTrack"
            assert ("PUT", "/v1/playlists/syntheticPlaylist1/items") not in calls
        assert ManagedPlaylistRegistry(path).records_for("syntheticAccount")[
            group.key
        ].playlist_id == ("syntheticPlaylist1")


def test_creation_with_null_description_preserves_id_and_confirms_metadata(tmp_path: Path) -> None:
    cleared = False

    def override(request: httpx.Request) -> httpx.Response | None:
        nonlocal cleared
        if (
            request.method == "GET"
            and request.url.path == "/v1/playlists/syntheticPlaylist1"
            and not cleared
        ):
            cleared = True
            remote["syntheticPlaylist1"]["description"] = None
        return None

    browser, remote, calls = synthetic_spotify_app(tmp_path / "managed.json", override=override)
    key = artist_playlists(PROTOTYPE_TRACKS).playlists[0].key
    with browser:
        result = post_preview(browser, [key])
        assert result.status_code == 200
        assert "Todas as faixas foram confirmadas" in result.text
        assert calls.count(("POST", "/v1/me/playlists")) == 1
        assert calls.count(("PUT", "/v1/playlists/syntheticPlaylist1")) == 1
        assert remote["syntheticPlaylist1"]["description"] == MANAGED_PLAYLIST_DESCRIPTION
