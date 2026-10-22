import re
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from music_curator.config import ORIGIN, Settings
from music_curator.demo import DemoCredentialStore, demo_response
from music_curator.models import AppError
from music_curator.web import create_app

HEADERS = {"Origin": ORIGIN}


def csrf(browser: TestClient) -> str:
    page = browser.get("/entrar")
    match = re.search(r'name="csrf" value="([^"]+)"', page.text)
    assert match
    return match[1]


def login(browser: TestClient) -> None:
    response = browser.post(
        "/connect", data={"csrf": csrf(browser)}, headers=HEADERS, follow_redirects=False
    )
    response = browser.get(callback_url(response.headers["location"]))
    assert "Conexao salva" in response.text


def callback_url(location: str) -> str:
    state = parse_qs(urlsplit(location).query)["state"][0]
    return f"/callback?state={state}&code=synthetic-code"


def test_initial_screen_and_cookie(browser: TestClient) -> None:
    response = browser.get("/")
    assert response.status_code == 200
    assert 'href="/entrar"' in response.text
    assert "Continuar com Spotify" in browser.get("/entrar").text
    assert "Carregar playlists</button>" not in response.text
    assert "Demonstração" not in response.text
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "same-origin"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert browser.get("/static/prototype.css").status_code == 200


@pytest.mark.parametrize("signed_in", [False, True])
def test_local_home_omits_removed_sections(browser: TestClient, signed_in: bool) -> None:
    if signed_in:
        login(browser)
    page = browser.get("/")
    assert page.status_code == 200
    for removed in (
        'id="como-funciona"',
        'id="seu-espaco"',
        'href="#seu-espaco"',
        "Da sua conta ao seu espaço.",
        "Entre com Spotify",
        "Conheça seu espaço",
        "Continue no controle",
        "Um espaço seu.",
        "Acessar meu espaço",
        'class="prototype-strip"',
        "Seu espaço local",
        "Entrada pelo Spotify. Nenhuma playlist é consultada ou alterada neste painel.",
    ):
        assert removed not in page.text
    assert 'href="#como-funciona">Como funciona</a>' in page.text
    assert "Conheça a ideia" in page.text
    assert 'class="hero wrap"' in page.text
    assert '<footer class="site-footer wrap">' in page.text
    assert "Feito para ouvir sua coleção de um jeito novo." in page.text
    assert page.text.count('href="/app"' if signed_in else 'href="/entrar"') == 2


def test_full_synthetic_flow_and_disconnect(
    browser: TestClient, store: DemoCredentialStore
) -> None:
    login(browser)
    response = browser.post("/playlists", data={"csrf": csrf(browser)}, headers=HEADERS)
    assert response.status_code == 200
    assert "Horizontes de guitarra" in response.text
    assert "3 encontradas" in response.text
    assert "synthetic-access" not in response.text
    assert "synthetic-refresh" not in response.text
    assert store.token is not None
    response = browser.post("/disconnect", data={"csrf": csrf(browser)}, headers=HEADERS)
    assert response.status_code == 200
    assert store.token is None
    assert 'href="/entrar"' in response.text
    response = browser.post("/playlists", data={"csrf": csrf(browser)}, headers=HEADERS)
    assert response.status_code == 502
    assert "Conecte sua conta" in response.text


@pytest.mark.parametrize("path", ["/connect", "/playlists", "/disconnect"])
def test_csrf_required(browser: TestClient, path: str) -> None:
    token = csrf(browser)
    assert browser.post(path, data={"csrf": token}).status_code == 403
    assert browser.post(path, data={"csrf": "invalid"}, headers=HEADERS).status_code == 403
    assert (
        browser.post(
            path, data={"csrf": token}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )


def test_host_and_cross_site_blocked(browser: TestClient) -> None:
    assert browser.get("/", headers={"Host": "evil.example:8765"}).status_code == 400
    assert browser.get("/", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert (
        browser.get(
            "/",
            headers={
                "Sec-Fetch-Site": "cross-site",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Dest": "document",
            },
        ).status_code
        == 200
    )


@pytest.mark.parametrize("path", ["/", "/entrar", "/app"])
def test_returning_navigation_can_reach_product_pages(browser: TestClient, path: str) -> None:
    login(browser)
    headers = {
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    }
    assert browser.get(path, headers=headers).status_code == 200
    assert browser.get(path, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert browser.post("/disconnect", headers=headers).status_code == 403


def test_callback_invalid_and_replay(browser: TestClient) -> None:
    token = csrf(browser)
    response = browser.post(
        "/connect", data={"csrf": token}, headers=HEADERS, follow_redirects=False
    )
    callback = callback_url(response.headers["location"])
    callback_response = browser.get(
        "/callback?state=wrong&code=synthetic-code", follow_redirects=False
    )
    assert callback_response.headers["referrer-policy"] == "no-referrer"
    response = browser.get("/entrar")
    assert "Autorizacao invalida" in response.text
    assert "Autorizacao invalida" in browser.get(callback).text
    assert "Continuar com Spotify" in browser.get("/entrar").text
    login(browser)
    assert "Autorizacao invalida" in browser.get(callback).text


def test_callback_cancelled_and_missing_code(browser: TestClient) -> None:
    for suffix in ["error=access_denied", ""]:
        token = csrf(browser)
        response = browser.post(
            "/connect", data={"csrf": token}, headers=HEADERS, follow_redirects=False
        )
        state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
        page = browser.get(f"/callback?state={state}&{suffix}")
        assert "Continuar com Spotify" in page.text
        assert ("cancelada" if suffix else "autorizacao valida") in page.text


def test_callback_expiry(browser: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    token = csrf(browser)
    response = browser.post(
        "/connect", data={"csrf": token}, headers=HEADERS, follow_redirects=False
    )
    import time

    original = time.monotonic()
    monkeypatch.setattr("music_curator.web.time.monotonic", lambda: original + 601)
    page = browser.get(callback_url(response.headers["location"]))
    assert "Autorizacao invalida ou expirada" in page.text


def test_callback_without_session(browser: TestClient) -> None:
    assert browser.get("/callback?state=none").status_code == 400


def test_successful_callback_is_single_use(browser: TestClient) -> None:
    response = browser.post(
        "/connect", data={"csrf": csrf(browser)}, headers=HEADERS, follow_redirects=False
    )
    callback = callback_url(response.headers["location"])
    assert "Conexao salva com seguranca" in browser.get(callback).text
    assert "Autorizacao invalida" in browser.get(callback).text


def test_disconnect_invalidates_pending_login(browser: TestClient) -> None:
    login(browser)
    response = browser.post(
        "/connect", data={"csrf": csrf(browser)}, headers=HEADERS, follow_redirects=False
    )
    callback = callback_url(response.headers["location"])
    browser.post("/disconnect", data={"csrf": csrf(browser)}, headers=HEADERS)
    assert "Conexao salva com seguranca" not in browser.get(callback).text


def test_no_configuration_hides_setup_banner_and_blocks_connection() -> None:
    app = create_app(Settings(), store=DemoCredentialStore())
    with TestClient(app, base_url=ORIGIN) as client:
        page = client.get("/entrar")
        assert page.status_code == 200
        assert "Antes de conectar" not in page.text
        assert "Configure o Client ID" not in page.text
        assert "SPOTIFY_CLIENT_ID" not in page.text
        assert "http://127.0.0.1:8765/callback" not in page.text
        assert "Não é necessário Client Secret." not in page.text
        assert "Você entra pelo site oficial do Spotify." in page.text
        assert "Não pedimos sua senha Spotify." in page.text
        assert "Continuar com Spotify" in page.text
        assert "disabled" in page.text
        response = client.post("/connect", data={"csrf": csrf(client)}, headers=HEADERS)
        assert response.status_code == 400
        assert "Client ID" in response.text


def test_real_mode_uses_official_oauth_url(store: DemoCredentialStore) -> None:
    app = create_app(Settings(client_id="0" * 32), store=store)
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.post(
            "/connect", data={"csrf": csrf(client)}, headers=HEADERS, follow_redirects=False
        )
        assert response.headers["location"].startswith("https://accounts.spotify.com/authorize?")
        assert "playlist-modify" not in response.headers["location"]


@pytest.mark.parametrize("empty", [False, True])
def test_rendering_escapes_names_and_handles_empty(store: DemoCredentialStore, empty: bool) -> None:
    store.save("synthetic-refresh")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/token":
            return demo_response(request)
        return httpx.Response(
            200,
            json={"items": [] if empty else [{"id": "safeId", "name": "<script>fake</script>"}]},
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(Settings(client_id="0" * 32), store=store, http=http)
    with TestClient(app, base_url=ORIGIN) as client:
        page = client.post("/playlists", data={"csrf": csrf(client)}, headers=HEADERS)
        assert page.status_code == 200
        if empty:
            assert "Nenhuma playlist disponível" in page.text
        else:
            assert "&lt;script&gt;" in page.text
            assert "<script>fake</script>" not in page.text
            assert "https://open.spotify.com/playlist/safeId" in page.text


def test_api_failure_is_not_empty_list() -> None:
    store = DemoCredentialStore()
    store.save("synthetic-refresh")
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(403)))
    app = create_app(Settings(client_id="0" * 32), store=store, http=http)
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.post("/playlists", data={"csrf": csrf(client)}, headers=HEADERS)
        assert response.status_code == 502
        assert "Nenhuma playlist disponível" not in response.text
        assert "Horizontes de guitarra" not in response.text


def test_real_dashboard_requires_saved_connection(browser: TestClient) -> None:
    response = browser.get("/app", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/entrar"
    page = browser.get("/entrar")
    assert 'action="/connect"' in page.text
    assert 'type="password"' not in page.text
    assert 'type="email"' not in page.text
    assert "simula a entrada" not in page.text


def test_real_login_loads_songs_once_and_navigation_reuses_collection(
    store: DemoCredentialStore,
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path in {"/api/token", "/v1/me/tracks"}
        calls.append(request.url.path)
        return demo_response(request)

    app = create_app(
        Settings(client_id="0" * 32),
        store=store,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/").status_code == 200
        assert client.get("/app").url.path == "/entrar"
        token = csrf(client)
        response = client.post(
            "/connect", data={"csrf": token}, headers=HEADERS, follow_redirects=False
        )
        response = client.get(callback_url(response.headers["location"]))
        assert response.url.path == "/app"
        assert "Conexao salva com seguranca" in response.text
        assert response.headers["referrer-policy"] == "same-origin"
        songs = client.get("/app/musicas")
        assert "6 músicas importadas" in songs.text
        assert "Atualizar músicas" in songs.text
        for path in ["/", "/entrar", "/app"]:
            page = client.get(path)
            assert page.status_code == 200
            assert 'action="/playlists"' not in page.text
            assert "Carregar playlists" not in page.text
            assert "collection-card" not in page.text
            assert "synthetic-access" not in page.text
            assert "synthetic-refresh" not in page.text
        dashboard = client.get("/app")
        assert "Spotify · conexão salva" in dashboard.text
        assert "Playlists e automações ainda não estão disponíveis" in dashboard.text
        assert "não comprova" in dashboard.text
        assert "Conta de demonstração" not in dashboard.text
        assert "Modo protótipo" not in dashboard.text
        assert "Desconectar Spotify" in dashboard.text
        assert re.findall(r'<form[^>]+action="([^"]+)"', dashboard.text) == ["/disconnect"]
        assert client.get("/entrar", follow_redirects=False).headers["location"] == "/app"
        assert "Abrir painel" in client.get("/").text
        assert client.post("/disconnect", data={"csrf": token}, headers=HEADERS).status_code == 403
        response = client.post("/disconnect", data={"csrf": csrf(client)}, headers=HEADERS)
        assert response.url.path == "/"
        assert "Abrir painel" not in response.text
        assert store.token is None
        assert client.get("/app").url.path == "/entrar"
    assert calls == ["/api/token", "/v1/me/tracks"]


def test_saved_connection_opens_dashboard_and_loads_library_once(
    store: DemoCredentialStore,
) -> None:
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
    with TestClient(app, base_url=ORIGIN) as client:
        assert "Abrir painel" in client.get("/").text
        assert calls == []
        assert client.get("/entrar").url.path == "/app"
        assert "conexão salva" in client.get("/app").text
        assert "6 músicas importadas" in client.get("/app/musicas").text
        assert calls == ["/api/token", "/v1/me/tracks"]


@pytest.mark.parametrize("status", [400, 500])
def test_failed_token_exchange_stays_on_login(store: DemoCredentialStore, status: int) -> None:
    app = create_app(
        Settings(client_id="0" * 32),
        store=store,
        http=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status))),
    )
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.post(
            "/connect", data={"csrf": csrf(client)}, headers=HEADERS, follow_redirects=False
        )
        page = client.get(callback_url(response.headers["location"]))
        assert page.url.path == "/entrar"
        assert 'role="alert"' in page.text
        assert "Conexao salva" not in page.text
        assert store.token is None
        assert client.get("/app").url.path == "/entrar"


@pytest.mark.parametrize("path", ["/", "/entrar", "/app"])
def test_product_storage_failure_is_explicit(path: str) -> None:
    class UnavailableStore(DemoCredentialStore):
        def load(self) -> str | None:
            raise AppError("Armazenamento seguro indisponivel.")

    app = create_app(Settings(client_id="0" * 32), store=UnavailableStore())
    with TestClient(app, base_url=ORIGIN) as client:
        page = client.get(path)
        assert page.status_code == 503
        assert 'role="alert"' in page.text
        assert "Armazenamento seguro indisponivel" in page.text
        assert "Spotify · conexão salva" not in page.text


def test_failed_disconnect_keeps_dashboard_and_connection() -> None:
    class CannotDeleteStore(DemoCredentialStore):
        def delete(self) -> None:
            raise AppError("Nao foi possivel apagar a credencial.")

    store = CannotDeleteStore()
    store.save("synthetic-refresh")
    app = create_app(Settings(client_id="0" * 32), store=store)
    with TestClient(app, base_url=ORIGIN) as client:
        page = client.post("/disconnect", data={"csrf": csrf(client)}, headers=HEADERS)
        assert page.status_code == 503
        assert 'role="alert"' in page.text
        assert "Nao foi possivel apagar" in page.text
        assert "Desconectar Spotify" in page.text
        assert store.token is not None
        assert "Spotify · conexão salva" in client.get("/app").text
