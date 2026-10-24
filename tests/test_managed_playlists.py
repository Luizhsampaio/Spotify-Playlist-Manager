import json
import re
import time
from pathlib import Path
from typing import TypedDict

import httpx
import pytest
from fastapi.testclient import TestClient

from music_curator.config import ORIGIN, Settings
from music_curator.demo import PROTOTYPE_TRACKS, DemoCredentialStore
from music_curator.managed_registry import ManagedPlaylistRecord, ManagedPlaylistRegistry
from music_curator.models import Artist, SavedTrack
from music_curator.playlist_preview import artist_playlists
from music_curator.spotify import AUTH_SCOPES, CREATE_PLAYLIST_URL, TOKEN_URL
from music_curator.web import create_app

PAGE = "/app/playlists"
PREVIEW = "/app/playlists/por-artista/salvar"
HEADERS = {"Origin": ORIGIN}


class SyntheticPlaylist(TypedDict):
    name: str
    owner: str
    items: list[str]


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
        "public": True,
        "collaborative": False,
        "owner": {"id": playlist["owner"]},
    }


def synthetic_spotify_app(
    registry_path: Path,
    *,
    timeout_create_once: bool = False,
    timeout_remove_once: bool = False,
    tracks: list[SavedTrack] | None = None,
) -> tuple[TestClient, dict[str, SyntheticPlaylist], list[tuple[str, str]]]:
    state: dict[str, SyntheticPlaylist] = {}
    calls: list[tuple[str, str]] = []
    timeout_pending = timeout_create_once
    remove_timeout_pending = timeout_remove_once
    library_tracks = tracks if tracks is not None else PROTOTYPE_TRACKS

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal timeout_pending, remove_timeout_pending
        if str(request.url) != TOKEN_URL:
            assert request.headers["authorization"] == "Bearer " + "synthetic-" + "access"
        path = request.url.path
        calls.append((request.method, path))
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
        if str(request.url) == CREATE_PLAYLIST_URL:
            payload = json.loads(request.read())
            assert payload["public"] is True
            assert payload["collaborative"] is False
            playlist_id = f"syntheticPlaylist{len(state) + 1}"
            state[playlist_id] = {
                "name": payload["name"],
                "owner": "syntheticAccount",
                "items": [],
            }
            if timeout_pending:
                timeout_pending = False
                raise httpx.ReadTimeout("synthetic timeout after server-side create")
            return httpx.Response(201, json=playlist_json(playlist_id, state[playlist_id]))
        playlist_match = re.fullmatch(r"/v1/playlists/([A-Za-z0-9]+)(/items)?", path)
        if playlist_match:
            playlist_id, items_suffix = playlist_match.groups()
            playlist = state[playlist_id]
            if items_suffix is None:
                if request.method == "GET":
                    return httpx.Response(200, json=playlist_json(playlist_id, playlist))
                assert request.method == "PUT"
                playlist["name"] = json.loads(request.read())["name"]
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
    assert "data-artist-save-submit>Salvar</button>" in response.text
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
        assert "removida de Sua Biblioteca" in removed.text
        assert ("DELETE", "/v1/me/library") in calls
        assert registry.records_for("syntheticAccount") == {}
        assert registry.selection_for("syntheticAccount") == set()
        assert playlist_id in playlists

        page = browser.get(PAGE)
        restored = post_preview(browser, [group.key], page.text)
        assert restored.status_code == 200
        assert "Todas as faixas foram confirmadas no Spotify." in restored.text
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
        assert selection.status_code == 200
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
        assert retry.status_code == 200
        assert "resultado incerto" in retry.text.lower()
        assert calls.count(("POST", "/v1/me/playlists")) == 1
