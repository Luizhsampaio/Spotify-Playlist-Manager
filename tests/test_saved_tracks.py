import asyncio
from collections.abc import Coroutine
from typing import Any

import httpx
import pytest

from music_curator.demo import PROTOTYPE_TRACKS, DemoCredentialStore
from music_curator.models import AppError, ReconnectRequired
from music_curator.spotify import (
    AUTH_SCOPES,
    LIBRARY_SCOPE,
    READ_SCOPE,
    SAVED_TRACKS_URL,
    TOKEN_URL,
    SpotifyClient,
)

TOKEN = {
    "access_token": "synthetic-access",
    "refresh_token": "synthetic-refresh",
    "token_type": "Bearer",
    "expires_in": 3600,
    "scope": AUTH_SCOPES,
}
SYNTHETIC_ITEM = PROTOTYPE_TRACKS[0].model_dump(mode="json")


def run[T](task: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(task)


def test_imports_all_pages_in_order_including_unavailable_tracks() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json=TOKEN)
        assert request.method == "GET"
        assert request.url.path == "/v1/me/tracks"
        offset = int(request.url.params.get("offset", "0"))
        items = [
            {
                **SYNTHETIC_ITEM,
                "track": {
                    **SYNTHETIC_ITEM["track"],
                    "id": f"synthetic{index}",
                    "name": f"Musica sintetica {index}",
                },
            }
            for index in range(offset, min(offset + 50, 123))
        ]
        if offset == 100:
            items.append({"added_at": None, "track": None})
        return httpx.Response(
            200,
            json={
                "items": items,
                "next": f"https://api.spotify.com/v1/me/tracks?offset={offset + 50}&limit=50"
                if offset < 100
                else None,
            },
        )

    async def scenario() -> None:
        store = DemoCredentialStore()
        store.save("synthetic-refresh")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await SpotifyClient("0" * 32, store, http).list_saved_tracks()
            assert len(result) == 124
            assert [item.track.id for item in result if item.track] == [
                f"synthetic{index}" for index in range(123)
            ]
            assert result[-1].track is None
            assert result[0].track is not None
            assert result[0].track.duration == "3:33"
            assert result[0].track.url == "https://open.spotify.com/track/synthetic0"

    run(scenario())
    assert len(calls) == 4


@pytest.mark.parametrize(
    "next_url",
    [
        "https://evil.example/steal",
        "http://api.spotify.com/v1/me/tracks",
        "https://api.spotify.com.evil.example/v1/me/tracks",
        "https://api.spotify.com@evil.example/v1/me/tracks",
        "https://api.spotify.com/v1/me/playlists",
        "https://api.spotify.com/v1/me/tracks?offset=bad",
        "https://api.spotify.com/v1/me/tracks?secret=1",
        "https://api.spotify.com/v1/me/tracks#unsafe",
        "https://[invalid/v1/me/tracks",
        SAVED_TRACKS_URL,
    ],
)
def test_saved_tracks_reject_unsafe_or_repeated_pagination(next_url: str) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json=TOKEN)
        return httpx.Response(200, json={"items": [SYNTHETIC_ITEM], "next": next_url})

    async def scenario() -> None:
        store = DemoCredentialStore()
        store.save("synthetic-refresh")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(AppError, match="paginacao"):
                await SpotifyClient("0" * 32, store, http).list_saved_tracks()

    run(scenario())
    assert calls == [TOKEN_URL, SAVED_TRACKS_URL]


@pytest.mark.parametrize("status", [401, 403, 429, 500, 302])
def test_saved_tracks_errors_are_explicit_and_sanitized(status: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json=TOKEN)
        calls += 1
        return httpx.Response(status, text="synthetic-private-response")

    async def scenario() -> None:
        store = DemoCredentialStore()
        store.save("synthetic-refresh")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(AppError) as error:
                await SpotifyClient("0" * 32, store, http).list_saved_tracks()
            assert "synthetic-private-response" not in str(error.value)
            if status == 403:
                assert LIBRARY_SCOPE in str(error.value)
            if status == 401:
                assert store.token is None

    run(scenario())
    assert calls == (2 if status == 401 else 1)


@pytest.mark.parametrize("scope", [READ_SCOPE, LIBRARY_SCOPE])
def test_new_login_requires_both_read_scopes(scope: str) -> None:
    async def scenario() -> None:
        store = DemoCredentialStore()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={**TOKEN, "scope": scope})
            )
        ) as http:
            with pytest.raises(AppError):
                await SpotifyClient("0" * 32, store, http).complete_login(
                    "synthetic-code", "synthetic-verifier"
                )
            assert store.token is None

    run(scenario())


def test_old_connection_needs_library_permission_without_reading_tracks() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert str(request.url) == TOKEN_URL
        return httpx.Response(200, json={**TOKEN, "scope": READ_SCOPE})

    async def scenario() -> None:
        store = DemoCredentialStore()
        store.save("synthetic-refresh")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(ReconnectRequired, match="user-library-read"):
                await SpotifyClient("0" * 32, store, http).list_saved_tracks()

    run(scenario())
    assert calls == [TOKEN_URL]


@pytest.mark.parametrize(
    "payload",
    [
        "not-a-page",
        {"items": [{"track": {"id": "../unsafe"}}]},
        {"items": [{"track": {**SYNTHETIC_ITEM["track"], "id": "../unsafe"}}]},
        {"items": [{"track": {**SYNTHETIC_ITEM["track"], "duration_ms": -1}}]},
        {"items": [{"track": {**SYNTHETIC_ITEM["track"], "artists": "invalid"}}]},
        {"items": [{"track": SYNTHETIC_ITEM["track"], "added_at": "invalid"}]},
        {
            "items": [
                {
                    "track": {
                        **SYNTHETIC_ITEM["track"],
                        "album": {"name": "Album sintetico", "release_date": "2023-02-29"},
                    }
                }
            ]
        },
        {
            "items": [
                {
                    "track": {
                        **SYNTHETIC_ITEM["track"],
                        "album": {
                            "name": "Album sintetico",
                            "release_date": "2024",
                            "release_date_precision": "day",
                        },
                    }
                }
            ]
        },
    ],
)
def test_invalid_saved_tracks_are_not_successful_imports(payload: object) -> None:
    async def scenario() -> None:
        store = DemoCredentialStore()
        store.save("synthetic-refresh")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=TOKEN if str(request.url) == TOKEN_URL else payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(AppError, match="lista invalida"):
                await SpotifyClient("0" * 32, store, http).list_saved_tracks()

    run(scenario())


def test_empty_collection_and_missing_track_id_are_supported() -> None:
    async def scenario(items: list[object]) -> None:
        store = DemoCredentialStore()
        store.save("synthetic-refresh")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=TOKEN if str(request.url) == TOKEN_URL else {"items": items}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await SpotifyClient("0" * 32, store, http).list_saved_tracks()
            assert len(result) == len(items)
            if result:
                assert result[0].track is not None
                assert result[0].track.url is None

    run(scenario([]))
    run(scenario([{"track": {**SYNTHETIC_ITEM["track"], "id": None}}]))


def test_saved_tracks_refresh_once_after_401_and_complete_import() -> None:
    gets = 0
    tokens = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal gets, tokens
        if str(request.url) == TOKEN_URL:
            tokens += 1
            return httpx.Response(200, json=TOKEN)
        gets += 1
        return (
            httpx.Response(401)
            if gets == 1
            else httpx.Response(200, json={"items": [SYNTHETIC_ITEM]})
        )

    async def scenario() -> None:
        store = DemoCredentialStore()
        store.save("synthetic-refresh")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await SpotifyClient("0" * 32, store, http).list_saved_tracks()
            assert len(result) == 1

    run(scenario())
    assert gets == tokens == 2


def test_forced_refresh_checks_library_scope_again_before_retry() -> None:
    gets = 0
    tokens = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal gets, tokens
        if str(request.url) == TOKEN_URL:
            tokens += 1
            return httpx.Response(
                200, json={**TOKEN, "scope": AUTH_SCOPES if tokens == 1 else READ_SCOPE}
            )
        gets += 1
        return httpx.Response(401)

    async def scenario() -> None:
        store = DemoCredentialStore()
        store.save("synthetic-refresh")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(ReconnectRequired, match="user-library-read"):
                await SpotifyClient("0" * 32, store, http).list_saved_tracks()

    run(scenario())
    assert gets == 1
    assert tokens == 2
