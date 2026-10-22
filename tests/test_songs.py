import asyncio
import re
import time
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from music_curator.config import ORIGIN, Settings
from music_curator.demo import PROTOTYPE_TRACKS, DemoCredentialStore, demo_response
from music_curator.spotify import TOKEN_URL
from music_curator.web import create_app

HEADERS = {"Origin": ORIGIN}
IMPORT = "/app/musicas/importar"


def csrf(browser: TestClient, page: str = "/app/musicas") -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', browser.get(page).text)
    assert match
    return match[1]


def enter_demo(browser: TestClient) -> None:
    browser.post("/demo/entrar", data={"csrf": csrf(browser, "/entrar")}, headers=HEADERS)


def test_songs_require_connection(browser: TestClient) -> None:
    page = browser.get("/app/musicas", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/entrar"
    response = browser.post(IMPORT, data={"csrf": csrf(browser, "/entrar")}, headers=HEADERS)
    assert response.url.path == "/entrar"


def test_page_loads_once_automatically_with_saved_connection(store: DemoCredentialStore) -> None:
    store.save("synthetic-refresh")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return demo_response(request)

    app = create_app(
        Settings(client_id="0" * 32),
        store=store,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with TestClient(app, base_url=ORIGIN) as browser:
        page = browser.get("/app/musicas")
        assert page.status_code == 200
        assert "<h1>Minhas Músicas" in page.text
        assert 'href="/app/musicas" class="active" aria-current="page"' in page.text
        assert "Atualizar músicas" in page.text
        assert "6 músicas importadas" in page.text
        assert browser.get("/app").status_code == 200
        assert "6 músicas importadas" in browser.get("/app/musicas").text
        assert calls == ["/api/token", "/v1/me/tracks"]


@pytest.mark.parametrize("empty", [False, True])
def test_automatic_loading_is_once_per_session_even_with_empty_library(empty: bool) -> None:
    reads = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal reads
        if str(request.url) == TOKEN_URL:
            return demo_response(request)
        assert request.url.path == "/v1/me/tracks"
        reads += 1
        return httpx.Response(200, json={"items": []}) if empty else demo_response(request)

    store = DemoCredentialStore()
    store.save("synthetic-refresh")
    app = create_app(
        Settings(client_id="0" * 32),
        store=store,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with TestClient(app, base_url=ORIGIN) as browser:
        assert browser.get("/").status_code == 200
        assert reads == 0
        assert browser.get("/entrar").status_code == 200
        assert reads == 1
        for path in ["/app/musicas", "/app", "/entrar", "/app/musicas"]:
            assert browser.get(path).status_code == 200
        assert reads == 1
        page = browser.get("/app/musicas")
        assert ("0 músicas importadas" if empty else "6 músicas importadas") in page.text
        assert "Atualizar músicas" in page.text


@pytest.mark.parametrize("status", [401, 403, 500])
def test_failed_auto_import_is_explicit_and_does_not_retry_on_navigation(status: int) -> None:
    calls: list[str] = []
    fail = True

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if str(request.url) == TOKEN_URL:
            return demo_response(request)
        return (
            httpx.Response(status, text="synthetic-private-response")
            if fail
            else demo_response(request)
        )

    store = DemoCredentialStore()
    store.save("synthetic-refresh")
    app = create_app(
        Settings(client_id="0" * 32),
        store=store,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with TestClient(app, base_url=ORIGIN) as browser:
        page = browser.get("/app")
        assert page.status_code == (403 if status in (401, 403) else 502)
        assert "A importação não foi concluída" in page.text
        assert 'role="alert"' in page.text
        assert "0 músicas importadas" not in page.text
        assert "synthetic-private-response" not in page.text
        if status in (401, 403):
            assert "Reconectar Spotify" in page.text
        before = list(calls)
        for path in ["/app/musicas", "/app", "/app/musicas"]:
            page = browser.get(path)
            assert page.status_code == (403 if status in (401, 403) else 502)
            assert "A importação não foi concluída" in page.text
        assert calls == before
        if status == 500:
            fail = False
            page = browser.post(IMPORT, data={"csrf": csrf(browser)}, headers=HEADERS)
            assert page.status_code == 200
            assert "6 músicas importadas" in page.text
            assert "A importação não foi concluída" not in page.text
            assert len(calls) == len(before) + 1
            assert browser.get("/app").status_code == 200


def test_update_button_fetches_changes_after_automatic_loading() -> None:
    reads = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal reads
        if str(request.url) == TOKEN_URL:
            return demo_response(request)
        reads += 1
        item = PROTOTYPE_TRACKS[0].model_dump(mode="json")
        item["track"]["name"] = (
            "Primeira musica sintetica" if reads == 1 else "Nova musica sintetica"
        )
        return httpx.Response(200, json={"items": [item]})

    store = DemoCredentialStore()
    store.save("synthetic-refresh")
    app = create_app(
        Settings(client_id="0" * 32),
        store=store,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with TestClient(app, base_url=ORIGIN) as browser:
        browser.get("/app")
        assert reads == 1
        assert "Primeira musica sintetica" in browser.get("/app/musicas").text
        assert reads == 1
        page = browser.post(IMPORT, data={"csrf": csrf(browser)}, headers=HEADERS)
        assert page.status_code == 200
        assert "Nova musica sintetica" in page.text
        assert "Primeira musica sintetica" not in page.text
        assert reads == 2


def test_concurrent_initial_navigation_only_imports_once() -> None:
    reads = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal reads
        if str(request.url) == TOKEN_URL:
            return demo_response(request)
        reads += 1
        await asyncio.sleep(0.02)
        return demo_response(request)

    async def scenario() -> None:
        store = DemoCredentialStore()
        store.save("synthetic-refresh")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            app = create_app(Settings(client_id="0" * 32), store=store, http=http)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url=ORIGIN
            ) as browser:
                await browser.get("/")
                first, second = await asyncio.gather(
                    browser.get("/app"), browser.get("/app/musicas")
                )
                assert first.status_code == second.status_code == 200
                assert "6 músicas importadas" in second.text
                assert reads == 1
                assert "6 músicas importadas" in (await browser.get("/app/musicas")).text
                assert reads == 1

    asyncio.run(scenario())


def test_automatic_load_after_oauth_failure_keeps_connection_and_offers_retry() -> None:
    reads = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal reads
        if str(request.url) == TOKEN_URL:
            return demo_response(request)
        reads += 1
        return httpx.Response(500)

    store = DemoCredentialStore()
    app = create_app(
        Settings(client_id="0" * 32),
        store=store,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with TestClient(app, base_url=ORIGIN) as browser:
        response = browser.post(
            "/connect",
            data={"csrf": csrf(browser, "/entrar")},
            headers=HEADERS,
            follow_redirects=False,
        )
        state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
        page = browser.get(f"/callback?state={state}&code=synthetic-code")
        assert page.status_code == 502
        assert "<h1>Minhas Músicas" in page.text
        assert "A importação não foi concluída" in page.text
        assert "Atualizar músicas" in page.text
        assert store.token is not None
        assert reads == 1
        assert browser.get("/app/musicas").status_code == 502
        assert reads == 1


def test_expired_session_does_not_receive_inflight_automatic_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = time.monotonic()
    expired = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal expired
        if str(request.url) != TOKEN_URL:
            expired = True
        return demo_response(request)

    async def scenario() -> None:
        store = DemoCredentialStore()
        store.save("synthetic-refresh")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            app = create_app(Settings(client_id="0" * 32), store=store, http=http)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url=ORIGIN
            ) as browser:
                await browser.get("/")
                page = await browser.get("/app")
                assert page.status_code == 409
                assert "Sessao ou conexao alterada" in page.text
                assert "6 músicas importadas" not in page.text

    monkeypatch.setattr(
        "music_curator.web.time.monotonic", lambda: now + (9 * 60 * 60 if expired else 0)
    )
    asyncio.run(scenario())


@pytest.mark.parametrize("demo", [False, True])
@pytest.mark.parametrize("imported", [False, True])
def test_songs_omits_removed_banners(demo: bool, imported: bool) -> None:
    store = DemoCredentialStore()
    if not demo:
        store.save("synthetic-refresh")
    app = create_app(
        Settings(client_id="0" * 32),
        demo=demo,
        store=store,
        http=httpx.AsyncClient(transport=httpx.MockTransport(demo_response)),
    )
    with TestClient(app, base_url=ORIGIN) as browser:
        if demo:
            enter_demo(browser)
        if imported:
            response = browser.post(IMPORT, data={"csrf": csrf(browser)}, headers=HEADERS)
            assert response.status_code == 200
        page = browser.get("/app/musicas")
        assert page.status_code == 200
        assert 'class="library-source"' not in page.text
        assert "<strong>Músicas Curtidas</strong>" not in page.text
        assert "A coleção nativa do Spotify, não uma playlist criada por você." not in page.text
        assert 'class="demo-banner"' not in page.text
        assert "Importamos somente quando você solicita." not in page.text
        assert "Modo protótipo." not in page.text
        assert "Ver a Home" not in page.text
        assert "<h1>Minhas Músicas" in page.text
        assert 'action="/app/musicas/importar"' in page.text
        assert "6 músicas importadas" in page.text
        assert 'class="songs-table"' in page.text
        assert "Atualizar músicas" in page.text
        dashboard = browser.get("/app")
        assert dashboard.status_code == 200
        assert 'class="demo-banner"' in dashboard.text
        assert "Ver a Home" in dashboard.text


def test_import_lists_tracks_and_survives_navigation(
    browser: TestClient, store: DemoCredentialStore
) -> None:
    store.save("synthetic-refresh")
    page = browser.post(IMPORT, data={"csrf": csrf(browser)}, headers=HEADERS)
    assert page.status_code == 200
    assert page.url.path == "/app/musicas"
    assert "6 músicas importadas" in page.text
    for item in PROTOTYPE_TRACKS:
        assert item.track is not None
        assert item.track.name in page.text
        assert item.track.album.name in page.text
    assert "3:33" in page.text
    assert "18/08/2023" in page.text
    assert "Adicionada em" not in page.text
    assert "https://open.spotify.com/track/syntheticTrack1" in page.text
    assert "synthetic-access" not in page.text
    assert "synthetic-refresh" not in page.text
    assert page.headers["cache-control"] == "no-store"
    assert "Atualizar músicas" in page.text
    browser.get("/app")
    assert "6 músicas importadas" in browser.get("/app/musicas").text
    browser.post("/disconnect", data={"csrf": csrf(browser)}, headers=HEADERS)
    store.save("synthetic-new-account")
    page = browser.get("/app/musicas")
    assert "6 músicas importadas" in page.text
    assert "Atualizar músicas" in page.text


def test_screen_contains_every_song_across_multiple_pages() -> None:
    pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            return demo_response(request)
        offset = int(request.url.params.get("offset", "0"))
        pages.append(offset)
        items = []
        for index in range(offset, min(offset + 50, 125)):
            item = PROTOTYPE_TRACKS[0].model_dump(mode="json")
            item["track"]["id"] = f"synthetic{index}"
            item["track"]["name"] = f"Faixa sintetica {index}"
            items.append(item)
        return httpx.Response(
            200,
            json={
                "items": items,
                "next": f"https://api.spotify.com/v1/me/tracks?offset={offset + 50}&limit=50"
                if offset < 100
                else None,
            },
        )

    store = DemoCredentialStore()
    store.save("synthetic-refresh")
    app = create_app(
        Settings(client_id="0" * 32),
        store=store,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with TestClient(app, base_url=ORIGIN) as browser:
        page = browser.get("/app/musicas")
        assert page.status_code == 200
        assert "125 músicas importadas" in page.text
        assert page.text.count('class="song-row"') == 125
        for index in range(125):
            assert f">Faixa sintetica {index}</a>" in page.text
    assert pages == [0, 50, 100]


def test_reauthorizing_clears_imports_in_all_browser_sessions(
    browser: TestClient, store: DemoCredentialStore
) -> None:
    store.save("synthetic-refresh")
    browser.post(IMPORT, data={"csrf": csrf(browser)}, headers=HEADERS)
    filter_songs(browser, year="2024")
    with TestClient(browser.app, base_url=ORIGIN) as other:
        other.post(IMPORT, data={"csrf": csrf(other)}, headers=HEADERS)
        assert "6 músicas importadas" in other.get("/app/musicas").text
        response = browser.post(
            "/connect", data={"csrf": csrf(browser)}, headers=HEADERS, follow_redirects=False
        )
        state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
        assert browser.get(f"/callback?state={state}&code=synthetic-code").url.path == "/app"
        for session in (browser, other):
            assert "songs-table" in session.get("/app/musicas").text
            assert "Mostrando 6 de 6 músicas" in session.get("/app/musicas").text


@pytest.mark.parametrize("demo", [False, True])
def test_import_requires_csrf_and_same_origin(
    browser: TestClient, store: DemoCredentialStore, prototype: TestClient, demo: bool
) -> None:
    if demo:
        browser = prototype
        enter_demo(browser)
    else:
        store.save("synthetic-refresh")
    token = csrf(browser)
    assert browser.post(IMPORT, data={"csrf": token}).status_code == 403
    assert browser.post(IMPORT, data={"csrf": "invalid"}, headers=HEADERS).status_code == 403
    assert (
        browser.post(
            IMPORT, data={"csrf": token}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )


def test_demo_import_is_synthetic_and_isolated(prototype: TestClient) -> None:
    enter_demo(prototype)
    page = prototype.get("/app/musicas")
    assert "6 músicas importadas · Dados fictícios" in page.text
    assert "Horizonte de papel" in page.text
    assert "open.spotify.com/track/" not in page.text
    with TestClient(prototype.app, base_url=ORIGIN) as other:
        enter_demo(other)
        assert "6 músicas importadas" in other.get("/app/musicas").text
    prototype.post("/demo/sair", data={"csrf": csrf(prototype)}, headers=HEADERS)
    enter_demo(prototype)
    assert "6 músicas importadas" in prototype.get("/app/musicas").text


def test_import_is_blocked_before_demo_login(prototype: TestClient) -> None:
    response = prototype.post(IMPORT, data={"csrf": csrf(prototype, "/entrar")}, headers=HEADERS)
    assert response.url.path == "/entrar"


@pytest.mark.parametrize("kind", ["empty", "unavailable", "escaped"])
def test_songs_render_empty_unavailable_and_escaped_values(kind: str) -> None:
    item = PROTOTYPE_TRACKS[0].model_dump(mode="json")
    if kind == "escaped":
        item["track"]["name"] = "<script>synthetic-song</script>"
        item["track"]["artists"] = [{"name": "<script>synthetic-artist</script>"}]
        item["track"]["album"]["name"] = "<script>synthetic-album</script>"
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
        page = browser.post(IMPORT, data={"csrf": csrf(browser)}, headers=HEADERS)
        assert page.status_code == 200
        if kind == "empty":
            assert "Nenhuma música curtida encontrada" in page.text
            assert "0 músicas importadas" in page.text
        elif kind == "unavailable":
            assert "Faixa indisponível" in page.text
            assert "1 música importada" in page.text
            assert "Não informada" in page.text
        else:
            for value in ["song", "artist", "album"]:
                assert f"&lt;script&gt;synthetic-{value}&lt;/script&gt;" in page.text
                assert f"<script>synthetic-{value}</script>" not in page.text


def test_failed_refresh_keeps_previous_complete_import_without_partial_data() -> None:
    reads = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal reads
        if str(request.url) == TOKEN_URL:
            return demo_response(request)
        reads += 1
        if reads == 1:
            return demo_response(request)
        if reads == 2:
            item = PROTOTYPE_TRACKS[0].model_dump(mode="json")
            item["track"]["name"] = "synthetic-partial-song"
            return httpx.Response(
                200,
                json={"items": [item], "next": "https://api.spotify.com/v1/me/tracks?offset=1"},
            )
        return httpx.Response(500, text="synthetic-private-response")

    store = DemoCredentialStore()
    store.save("synthetic-refresh")
    app = create_app(
        Settings(client_id="0" * 32),
        store=store,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with TestClient(app, base_url=ORIGIN) as browser:
        assert "6 músicas importadas" in browser.get("/app/musicas").text
        page = browser.post(IMPORT, data={"csrf": csrf(browser)}, headers=HEADERS)
        assert page.status_code == 502
        assert 'role="alert"' in page.text
        assert "A atualização falhou" in page.text
        assert "6 músicas importadas" in page.text
        assert "synthetic-partial-song" not in page.text
        assert "synthetic-private-response" not in page.text


@pytest.mark.parametrize("status", [403, 500])
def test_failed_first_import_is_not_empty_success(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return demo_response(request) if str(request.url) == TOKEN_URL else httpx.Response(status)

    store = DemoCredentialStore()
    store.save("synthetic-refresh")
    app = create_app(
        Settings(client_id="0" * 32),
        store=store,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with TestClient(app, base_url=ORIGIN) as browser:
        page = browser.post(IMPORT, data={"csrf": csrf(browser)}, headers=HEADERS)
        assert page.status_code == (403 if status == 403 else 502)
        assert "A importação não foi concluída" in page.text
        assert "Nenhuma música curtida encontrada" not in page.text
        assert "0 músicas importadas" not in page.text
        if status == 403:
            assert 'action="/connect"' in page.text
            assert "Reconectar Spotify" in page.text
            assert "user-library-read" in page.text


def test_session_expiry_discards_imported_songs(
    prototype: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    enter_demo(prototype)
    prototype.post(IMPORT, data={"csrf": csrf(prototype)}, headers=HEADERS)
    now = time.monotonic()
    monkeypatch.setattr("music_curator.web.time.monotonic", lambda: now + 9 * 60 * 60)
    assert prototype.get("/app/musicas").url.path == "/entrar"
    enter_demo(prototype)
    assert "6 músicas importadas" in prototype.get("/app/musicas").text


def filter_songs(browser: TestClient, **fields: str) -> httpx.Response:
    response = browser.post(
        "/app/musicas/filtrar", data={"csrf": csrf(browser), **fields}, headers=HEADERS
    )
    assert isinstance(response, httpx.Response)
    return response


def test_requested_columns_and_partial_release_dates(prototype: TestClient) -> None:
    enter_demo(prototype)
    page = prototype.post(IMPORT, data={"csrf": csrf(prototype)}, headers=HEADERS)
    assert re.findall(r'<th scope="col">([^<]+)</th>', page.text) == [
        "Música",
        "Artista",
        "Álbum",
        "Data de Lançamento",
        "Duração",
    ]
    assert "Adicionada em" not in page.text
    assert 'class="track-number"' not in page.text
    assert 'class="track-artists">Banda Aurora</td>' in page.text
    assert 'class="track-release">18/08/2023</td>' in page.text
    assert 'class="track-release">04/2024</td>' in page.text
    assert 'class="track-release">2022</td>' in page.text
    assert "01/01/2022" not in page.text


@pytest.mark.parametrize("no_results", [False, True])
def test_filters_and_results_share_one_collection_card(
    prototype: TestClient, no_results: bool
) -> None:
    class CardParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.inside: list[bool] = []
            self.components: dict[str, bool] = {}
            self.cards = 0

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag != "div":
                return
            classes = (dict(attrs).get("class") or "").split()
            card = "songs-collection-card" in classes
            self.cards += int(card)
            self.inside.append(card or bool(self.inside and self.inside[-1]))
            for name in ("songs-filter-panel", "songs-table-wrap", "empty-workspace"):
                if name in classes:
                    self.components[name] = self.inside[-1]

        def handle_endtag(self, tag: str) -> None:
            if tag == "div" and self.inside:
                self.inside.pop()

    enter_demo(prototype)
    page = prototype.post(IMPORT, data={"csrf": csrf(prototype)}, headers=HEADERS)
    if no_results:
        page = filter_songs(prototype, q="nenhuma faixa sintetica corresponde")
    parser = CardParser()
    parser.feed(page.text)
    assert parser.cards == 1
    assert parser.components["songs-filter-panel"]
    assert parser.components["empty-workspace" if no_results else "songs-table-wrap"]


def test_combined_filters_persist_and_clear_without_losing_original(prototype: TestClient) -> None:
    enter_demo(prototype)
    prototype.post(IMPORT, data={"csrf": csrf(prototype)}, headers=HEADERS)
    page = filter_songs(
        prototype, q="TRANQUILO", artist="Banda Aurora", album="Novos caminhos", year="2023"
    )
    assert page.status_code == 200
    assert page.url.path == "/app/musicas"
    assert page.url.query == b""
    assert 'name="q" maxlength="200" value="TRANQUILO"' in page.text
    assert "Mostrando 1 de 6 músicas" in page.text
    assert "6 músicas importadas" in page.text
    assert page.text.count('class="song-row"') == 1
    assert "Um lugar tranquilo" in page.text
    assert "Horizonte de papel" not in page.text
    assert '<option value="Clara do Sol"' in page.text
    prototype.get("/app")
    assert "Mostrando 1 de 6 músicas" in prototype.get("/app/musicas").text
    page = prototype.post(IMPORT, data={"csrf": csrf(prototype)}, headers=HEADERS)
    assert "Mostrando 1 de 6 músicas" in page.text
    page = filter_songs(prototype)
    assert "Mostrando 6 de 6 músicas" in page.text
    assert page.text.count('class="song-row"') == 6
    assert 'name="q" maxlength="200" value=""' in page.text
    assert "Horizonte de papel" in page.text


def test_no_matches_is_distinct_from_empty_import(prototype: TestClient) -> None:
    enter_demo(prototype)
    prototype.post(IMPORT, data={"csrf": csrf(prototype)}, headers=HEADERS)
    page = filter_songs(prototype, q="sem resultado sintetico")
    assert "Mostrando 0 de 6 músicas" in page.text
    assert "Nenhuma música corresponde aos filtros" in page.text
    assert "Nenhuma música curtida encontrada" not in page.text
    assert "songs-table" not in page.text
    assert "6 músicas importadas" in page.text
    assert "Mostrando 6 de 6 músicas" in filter_songs(prototype).text


def test_accented_query_is_decoded_and_searched(prototype: TestClient) -> None:
    enter_demo(prototype)
    prototype.post(IMPORT, data={"csrf": csrf(prototype)}, headers=HEADERS)
    page = filter_songs(prototype, q="CAFÉ")
    assert "Mostrando 1 de 6 músicas" in page.text
    assert "Café na varanda" in page.text
    assert page.url.query == b""


def test_filters_require_login_and_work_after_automatic_loading(prototype: TestClient) -> None:
    page = prototype.post(
        "/app/musicas/filtrar",
        data={"csrf": csrf(prototype, "/entrar"), "q": "cafe"},
        headers=HEADERS,
    )
    assert page.url.path == "/entrar"
    enter_demo(prototype)
    page = filter_songs(prototype, q="cafe")
    assert page.status_code == 200
    assert "Mostrando 1 de 6 músicas" in page.text
    assert "Café na varanda" in page.text


def test_filtering_real_mode_never_contacts_spotify_again() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return demo_response(request)

    store = DemoCredentialStore()
    store.save("synthetic-refresh")
    app = create_app(
        Settings(client_id="0" * 32),
        store=store,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with TestClient(app, base_url=ORIGIN) as browser:
        browser.get("/app/musicas")
        assert calls == ["/api/token", "/v1/me/tracks"]
        calls.clear()
        for fields in ({"q": "cafe"}, {"artist": "Clara do Sol"}, {"year": "2022"}, {}):
            page = filter_songs(browser, **fields)
            assert page.status_code == 200
            assert page.url.query == b""
        assert calls == []
        assert "Mostrando 6 de 6 músicas" in browser.get("/app/musicas").text


@pytest.mark.parametrize("demo", [False, True])
def test_filters_require_csrf_and_local_origin(
    browser: TestClient, store: DemoCredentialStore, prototype: TestClient, demo: bool
) -> None:
    if demo:
        browser = prototype
        enter_demo(browser)
    else:
        store.save("synthetic-refresh")
    browser.post(IMPORT, data={"csrf": csrf(browser)}, headers=HEADERS)
    token = csrf(browser)
    assert (
        browser.post("/app/musicas/filtrar", data={"csrf": token, "q": "cafe"}).status_code == 403
    )
    assert (
        browser.post(
            "/app/musicas/filtrar", data={"csrf": "invalid", "q": "cafe"}, headers=HEADERS
        ).status_code
        == 403
    )
    assert (
        browser.post(
            "/app/musicas/filtrar",
            data={"csrf": token, "q": "cafe"},
            headers={"Origin": "https://external.example"},
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "fields", [{"year": "invalid"}, {"q": "x" * 201}, {"artist": "x" * 513}, {"unknown": "value"}]
)
def test_invalid_filters_show_error_and_preserve_last_valid_selection(
    prototype: TestClient, fields: dict[str, str]
) -> None:
    enter_demo(prototype)
    prototype.post(IMPORT, data={"csrf": csrf(prototype)}, headers=HEADERS)
    filter_songs(prototype, year="2024")
    page = filter_songs(prototype, **fields)
    assert page.status_code == 400
    assert 'role="alert"' in page.text
    assert "Filtros invalidos" in page.text
    assert "Mostrando 2 de 6 músicas" in page.text
    assert "A atualização falhou" not in page.text


def test_duplicate_filter_fields_are_rejected(prototype: TestClient) -> None:
    enter_demo(prototype)
    prototype.post(IMPORT, data={"csrf": csrf(prototype)}, headers=HEADERS)
    page = prototype.post(
        "/app/musicas/filtrar",
        content=f"csrf={csrf(prototype)}&q=cafe&q=",
        headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert page.status_code == 400
    assert "Envie um unico valor por filtro" in page.text
    assert "Mostrando 6 de 6 músicas" in page.text


def test_filter_query_is_escaped_and_not_put_in_redirect_url(prototype: TestClient) -> None:
    enter_demo(prototype)
    prototype.post(IMPORT, data={"csrf": csrf(prototype)}, headers=HEADERS)
    page = filter_songs(prototype, q='"><script>synthetic-search</script>')
    assert page.url.query == b""
    assert "<script>synthetic-search</script>" not in page.text
    assert "&lt;script&gt;synthetic-search&lt;/script&gt;" in page.text
    assert page.headers["cache-control"] == "no-store"


def test_filters_are_isolated_and_reset_when_session_ends(prototype: TestClient) -> None:
    enter_demo(prototype)
    prototype.post(IMPORT, data={"csrf": csrf(prototype)}, headers=HEADERS)
    filter_songs(prototype, year="2024")
    with TestClient(prototype.app, base_url=ORIGIN) as other:
        enter_demo(other)
        other.post(IMPORT, data={"csrf": csrf(other)}, headers=HEADERS)
        assert "Mostrando 6 de 6 músicas" in other.get("/app/musicas").text
    assert "Mostrando 2 de 6 músicas" in prototype.get("/app/musicas").text
    prototype.post("/demo/sair", data={"csrf": csrf(prototype)}, headers=HEADERS)
    enter_demo(prototype)
    prototype.post(IMPORT, data={"csrf": csrf(prototype)}, headers=HEADERS)
    assert "Mostrando 6 de 6 músicas" in prototype.get("/app/musicas").text
