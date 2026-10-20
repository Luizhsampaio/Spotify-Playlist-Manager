import asyncio
import base64
import hashlib
from collections.abc import Coroutine
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import ValidationError

from music_curator.config import CALLBACK, Settings
from music_curator.demo import DemoCredentialStore
from music_curator.models import AppError, ReconnectRequired
from music_curator.spotify import PLAYLISTS_URL, READ_SCOPE, TOKEN_URL, SpotifyClient

CLIENT_ID = "0" * 32
TOKEN = {
    "access_token": "synthetic-access",
    "refresh_token": "synthetic-refresh",
    "token_type": "Bearer",
    "expires_in": 3600,
    "scope": READ_SCOPE,
}


def run[T](task: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(task)


def test_pkce() -> None:
    store = DemoCredentialStore()
    with_http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
    client = SpotifyClient(CLIENT_ID, store, with_http)
    url, state, verifier = client.authorization()
    query = parse_qs(urlsplit(url).query)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert query["code_challenge"] == [challenge.decode()]
    assert query["code_challenge_method"] == ["S256"]
    assert query["scope"] == [READ_SCOPE]
    assert query["redirect_uri"] == [CALLBACK]
    assert query["state"] == [state]
    assert 43 <= len(verifier) <= 128
    assert client.authorization()[1] != state
    run(with_http.aclose())


def test_unconfigured_auth() -> None:
    http = httpx.AsyncClient()
    client = SpotifyClient("", DemoCredentialStore(), http)
    with pytest.raises(AppError, match="Client ID"):
        client.authorization()
    run(http.aclose())


def test_policy_cannot_be_opened() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"policy_mode": "allowed"})


def test_login_and_restart_refresh_rotation(store: DemoCredentialStore) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if str(request.url) == TOKEN_URL:
            if len(calls) == 1:
                form = parse_qs(request.content.decode())
                assert form["grant_type"] == ["authorization_code"]
                assert form["code_verifier"] == ["synthetic-verifier"]
                assert form["redirect_uri"] == [CALLBACK]
                assert "client_secret" not in form
                return httpx.Response(200, json=TOKEN)
            form = parse_qs(request.content.decode())
            assert form["refresh_token"] == ["synthetic-refresh"]
            return httpx.Response(200, json={**TOKEN, "refresh_token": "synthetic-rotated"})
        return httpx.Response(200, json={"items": [], "next": None})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            first = SpotifyClient(CLIENT_ID, store, http)
            await first.complete_login("synthetic-code", "synthetic-verifier")
            assert store.token == "synthetic-refresh"
            restarted = SpotifyClient(CLIENT_ID, store, http)
            assert restarted.saved_connection()
            assert await restarted.list_playlists() == []
            assert store.token == "synthetic-rotated"
            await restarted.disconnect()
            assert store.token is None
            with pytest.raises(ReconnectRequired):
                await restarted.list_playlists()

    run(scenario())
    assert [r.method for r in calls] == ["POST", "POST", "GET"]


def test_pagination_and_duplicates(store: DemoCredentialStore) -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json=TOKEN)
        assert request.headers["authorization"] == "Bearer synthetic-access"
        if str(request.url) == PLAYLISTS_URL:
            return httpx.Response(
                200,
                json={
                    "items": [{"id": "one", "name": "Lista sintetica 1"}],
                    "next": "https://api.spotify.com/v1/me/playlists?offset=1&limit=50",
                },
            )
        return httpx.Response(
            200,
            json={
                "items": [
                    {"id": "one", "name": "Lista sintetica 1"},
                    {"id": "two", "name": "Lista sintetica 2"},
                ],
                "next": None,
            },
        )

    async def scenario() -> None:
        store.save("synthetic-refresh")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = SpotifyClient(CLIENT_ID, store, http)
            result = await client.list_playlists()
            assert [p.id for p in result] == ["one", "two"]
            assert result[0].url == "https://open.spotify.com/playlist/one"

    run(scenario())
    assert len(urls) == 3


@pytest.mark.parametrize(
    "next_url",
    [
        "https://evil.example/steal",
        "http://api.spotify.com/v1/me/playlists?offset=1",
        "https://api.spotify.com.evil.example/v1/me/playlists",
        "https://api.spotify.com@evil.example/v1/me/playlists",
        "https://api.spotify.com/v1/me/tracks",
        "https://api.spotify.com/v1/me/playlists?secret=1",
        "https://api.spotify.com/v1/me/playlists?offset=bad",
        "https://[invalid-host/v1/me/playlists",
        PLAYLISTS_URL,
    ],
)
def test_invalid_pagination_never_receives_token(store: DemoCredentialStore, next_url: str) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json=TOKEN)
        return httpx.Response(200, json={"items": [], "next": next_url})

    async def scenario() -> None:
        store.save("synthetic-refresh")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = SpotifyClient(CLIENT_ID, store, http)
            with pytest.raises(AppError, match="paginacao"):
                await client.list_playlists()

    run(scenario())
    assert calls == [TOKEN_URL, PLAYLISTS_URL]


@pytest.mark.parametrize("status", [401, 403, 500, 302])
def test_api_errors_are_explicit(store: DemoCredentialStore, status: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json=TOKEN)
        return httpx.Response(status, text="private-response-do-not-display")

    async def scenario() -> None:
        store.save("synthetic-refresh")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = SpotifyClient(CLIENT_ID, store, http)
            with pytest.raises(AppError) as error:
                await client.list_playlists()
            assert "private-response" not in str(error.value)
            if status == 401:
                assert store.token is None

    run(scenario())
    assert calls == (4 if status == 401 else 2)


def test_401_is_refreshed_once(store: DemoCredentialStore) -> None:
    gets = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal gets
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json=TOKEN)
        gets += 1
        if gets == 1:
            return httpx.Response(401)
        return httpx.Response(200, json={"items": [], "next": None})

    async def scenario() -> None:
        store.save("synthetic-refresh")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = SpotifyClient(CLIENT_ID, store, http)
            assert await client.list_playlists() == []

    run(scenario())
    assert gets == 2


@pytest.mark.parametrize("status", [400, 401])
def test_revoked_refresh_forgets_saved_connection(store: DemoCredentialStore, status: int) -> None:
    async def scenario() -> None:
        store.save("synthetic-refresh")
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(status))
        ) as http:
            client = SpotifyClient(CLIENT_ID, store, http)
            with pytest.raises(ReconnectRequired):
                await client.list_playlists()
            assert store.token is None

    run(scenario())


def test_refresh_without_rotation_preserves_credential(store: DemoCredentialStore) -> None:
    token = {k: v for k, v in TOKEN.items() if k != "refresh_token"}

    async def scenario() -> None:
        store.save("synthetic-old-refresh")

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == TOKEN_URL:
                return httpx.Response(200, json=token)
            return httpx.Response(200, json={"items": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = SpotifyClient(CLIENT_ID, store, http)
            assert await client.list_playlists() == []
            assert store.token == "synthetic-old-refresh"

    run(scenario())


def test_expired_access_token_is_refreshed(store: DemoCredentialStore) -> None:
    now = [0.0]
    grants: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            grants.append(parse_qs(request.content.decode())["grant_type"][0])
            return httpx.Response(200, json=TOKEN)
        return httpx.Response(200, json={"items": []})

    async def scenario() -> None:
        store.save("synthetic-refresh")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = SpotifyClient(CLIENT_ID, store, http, clock=lambda: now[0])
            await client.list_playlists()
            await client.list_playlists()
            assert len(grants) == 1
            now[0] = 3600
            await client.list_playlists()
            assert len(grants) == 2

    run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {**TOKEN, "expires_in": -1},
        {**TOKEN, "token_type": "Basic"},
        {**TOKEN, "access_token": ""},
        {**TOKEN, "refresh_token": ""},
        {**TOKEN, "scope": "user-read-email"},
        {k: v for k, v in TOKEN.items() if k != "refresh_token"},
    ],
)
def test_invalid_token_responses_are_not_saved(
    store: DemoCredentialStore, payload: dict[str, object]
) -> None:
    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        ) as http:
            client = SpotifyClient(CLIENT_ID, store, http)
            with pytest.raises(AppError):
                await client.complete_login("synthetic-code", "synthetic-verifier")
            assert store.token is None

    run(scenario())


@pytest.mark.parametrize("retry_after", ["0", "100", "invalid"])
def test_rate_limit_bounded_and_respected(store: DemoCredentialStore, retry_after: str) -> None:
    calls = 0
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json=TOKEN)
        calls += 1
        return httpx.Response(429, headers={"Retry-After": retry_after})

    async def scenario() -> None:
        store.save("synthetic-refresh")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = SpotifyClient(CLIENT_ID, store, http, sleep=sleep)
            with pytest.raises(AppError, match="Limite"):
                await client.list_playlists()

    run(scenario())
    assert calls == (3 if retry_after == "0" else 1)
    assert len(sleeps) == (2 if retry_after == "0" else 0)


def test_rate_limit_can_recover(store: DemoCredentialStore) -> None:
    calls = 0
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json=TOKEN)
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"items": []})

    async def scenario() -> None:
        store.save("synthetic-refresh")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            assert await SpotifyClient(CLIENT_ID, store, http, sleep=sleep).list_playlists() == []

    run(scenario())
    assert 2 <= waits[0] < 2.25


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError])
def test_network_error_is_sanitized(
    store: DemoCredentialStore, error_type: type[httpx.RequestError]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error_type("synthetic-secret-in-network-error", request=request)

    async def scenario() -> None:
        store.save("synthetic-refresh")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = SpotifyClient(CLIENT_ID, store, http)
            with pytest.raises(AppError) as error:
                await client.list_playlists()
            assert "synthetic-secret" not in str(error.value)

    run(scenario())


def test_bad_json_and_invalid_playlist_id(store: DemoCredentialStore) -> None:
    async def scenario(payload: object) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == TOKEN_URL:
                return httpx.Response(200, json=TOKEN)
            return httpx.Response(200, json=payload)

        store.save("synthetic-refresh")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(AppError, match="lista"):
                await SpotifyClient(CLIENT_ID, store, http).list_playlists()

    run(scenario("not-a-page"))
    run(scenario({"items": [{"id": "../unsafe", "name": "Synthetic"}]}))
