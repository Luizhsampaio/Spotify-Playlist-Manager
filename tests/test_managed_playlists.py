import re
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from music_curator.config import ORIGIN, Settings
from music_curator.demo import PROTOTYPE_TRACKS, DemoCredentialStore, demo_response
from music_curator.models import Artist, SavedTrack
from music_curator.playlist_preview import artist_playlists
from music_curator.spotify import TOKEN_URL
from music_curator.web import create_app

PAGE = "/app/playlists"
PREVIEW = "/app/playlists/por-artista/salvar"
HEADERS = {"Origin": ORIGIN}


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
            "enabled": "true",
        },
        headers=HEADERS,
    )
    assert isinstance(response, httpx.Response)
    return response


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


@pytest.mark.parametrize(
    "kind", ["none", "forged", "duplicate", "extra", "stale", "unicode-revision"]
)
def test_invalid_or_stale_selections_are_explicit(prototype: TestClient, kind: str) -> None:
    enter_demo(prototype)
    page = prototype.get(PAGE)
    key = artist_playlists(PROTOTYPE_TRACKS).playlists[0].key
    data: dict[str, str | list[str]] = {
        "enabled": "true",
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


def test_refresh_invalidates_preview_and_selection_is_session_local(prototype: TestClient) -> None:
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
    assert selected_keys(prototype.get(PAGE).text) == set()
    assert post_preview(prototype, keys, page.text).status_code == 409


def test_real_mode_preview_makes_no_writes_or_additional_spotify_calls() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        assert str(request.url) == TOKEN_URL or (
            request.method == "GET" and request.url.path == "/v1/me/tracks"
        )
        return demo_response(request)

    store = DemoCredentialStore()
    store.save("synthetic-refresh")
    app = create_app(
        Settings(client_id="0" * 32),
        store=store,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with TestClient(app, base_url=ORIGIN) as browser:
        page = browser.get(PAGE)
        assert calls == [("POST", "/api/token"), ("GET", "/v1/me/tracks")]
        calls.clear()
        keys = [group.key for group in artist_playlists(PROTOTYPE_TRACKS).playlists]
        preview = post_preview(browser, keys, page.text)
        assert preview.status_code == 200
        assert selected_keys(preview.text) == set(keys)
        assert "playlist-preview" not in preview.text
        assert calls == []
        assert "synthetic-access" not in preview.text
        assert "synthetic-refresh" not in preview.text
        assert preview.headers["cache-control"] == "no-store"
        browser.post("/disconnect", data={"csrf": field(preview.text, "csrf")}, headers=HEADERS)
        assert browser.get(PAGE).url.path == "/entrar"


@pytest.mark.parametrize("kind", ["empty", "escaped", "unavailable"])
def test_empty_and_escaped_artist_collections(kind: str) -> None:
    item = PROTOTYPE_TRACKS[0].model_dump(mode="json")
    item["track"]["artists"] = [{"name": "<script>synthetic-artist</script>"}]
    items = [] if kind == "empty" else [{"track": None}] if kind == "unavailable" else [item]

    def handler(request: httpx.Request) -> httpx.Response:
        return (
            demo_response(request)
            if str(request.url) == TOKEN_URL
            else httpx.Response(200, json={"items": items})
        )

    store = DemoCredentialStore()
    store.save("synthetic-refresh")
    app = create_app(
        Settings(client_id="0" * 32),
        store=store,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
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
            assert "disabled" in page.text
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


def is_enabled(html: str) -> bool:
    toggle = re.search(r"<input[^>]+data-artist-enabled[^>]*>", html)
    assert toggle
    return "checked" in toggle[0]


def test_configuration_starts_disabled_and_can_save_inactive_without_artists(
    prototype: TestClient,
) -> None:
    enter_demo(prototype)
    page = prototype.get(PAGE)
    assert not is_enabled(page.text)
    assert "data-artist-settings-fields disabled" in page.text
    response = prototype.post(
        PREVIEW,
        data={"csrf": field(page.text, "csrf"), "revision": field(page.text, "revision")},
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert not is_enabled(response.text)
    assert selected_keys(response.text) == set()


def test_disable_preserves_selection_and_reenable_restores_editing(prototype: TestClient) -> None:
    enter_demo(prototype)
    key = artist_playlists(PROTOTYPE_TRACKS).playlists[0].key
    page = post_preview(prototype, [key])
    assert is_enabled(page.text)
    assert "data-artist-settings-fields disabled" not in page.text
    page = prototype.post(
        PREVIEW,
        data={"csrf": field(page.text, "csrf"), "revision": field(page.text, "revision")},
        headers=HEADERS,
    )
    assert page.status_code == 200
    assert not is_enabled(page.text)
    assert selected_keys(page.text) == {key}
    assert "data-artist-settings-fields disabled" in page.text
    page = post_preview(prototype, [key])
    assert is_enabled(page.text)
    assert selected_keys(page.text) == {key}
    assert is_enabled(prototype.get(PAGE).text)


@pytest.mark.parametrize("value", ["false", "yes", "", ["true", "true"]])
def test_invalid_enabled_value_does_not_change_settings(
    prototype: TestClient,
    value: str | list[str],
) -> None:
    enter_demo(prototype)
    key = artist_playlists(PROTOTYPE_TRACKS).playlists[0].key
    page = post_preview(prototype, [key])
    response = prototype.post(
        PREVIEW,
        data={
            "csrf": field(page.text, "csrf"),
            "revision": field(page.text, "revision"),
            "enabled": value,
        },
        headers=HEADERS,
    )
    assert response.status_code == 400
    assert is_enabled(response.text)
    assert selected_keys(response.text) == {key}
