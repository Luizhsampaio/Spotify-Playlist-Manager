import asyncio
import base64
import hashlib
import secrets
import time
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from pydantic import ValidationError

from music_curator.config import CALLBACK
from music_curator.credentials import CredentialStore
from music_curator.models import (
    AppError,
    CollectionPage,
    Playlist,
    PlaylistPage,
    ReconnectRequired,
    SavedTrack,
    SavedTrackPage,
    TokenResponse,
)

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
PLAYLISTS_URL = "https://api.spotify.com/v1/me/playlists?limit=50"
SAVED_TRACKS_URL = "https://api.spotify.com/v1/me/tracks?limit=50"
READ_SCOPE = "playlist-read-private"
LIBRARY_SCOPE = "user-library-read"
AUTH_SCOPES = f"{READ_SCOPE} {LIBRARY_SCOPE}"


class SpotifyClient:
    def __init__(
        self,
        client_id: str,
        store: CredentialStore,
        http: httpx.AsyncClient,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.client_id = client_id
        self.store = store
        self.http = http
        self.clock = clock
        self.sleep = sleep
        self._access: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()
        self._scopes: set[str] | None = None

    def authorization(self) -> tuple[str, str, str]:
        if not self.client_id:
            raise AppError("Configure o Client ID antes de conectar ao Spotify.")
        # Refuse login before sending the user away if secure storage cannot be used.
        self.store.load()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        query = urlencode(
            {
                "client_id": self.client_id,
                "response_type": "code",
                "redirect_uri": CALLBACK,
                "scope": AUTH_SCOPES,
                "state": state,
                "code_challenge_method": "S256",
                "code_challenge": challenge.rstrip(b"=").decode(),
            }
        )
        return f"{AUTHORIZE_URL}?{query}", state, verifier

    def saved_connection(self) -> bool:
        return bool(self.client_id and self.store.load())

    async def _request(
        self, method: str, url: str, *, data: dict[str, str] | None = None, token: str | None = None
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        for attempt in range(3):
            try:
                response = await self.http.request(method, url, data=data, headers=headers)
            except httpx.TimeoutException as exc:
                raise AppError("O Spotify demorou a responder. Tente novamente.") from exc
            except httpx.RequestError as exc:
                raise AppError(
                    "Nao foi possivel acessar o Spotify. Verifique sua conexao."
                ) from exc
            if response.status_code != 429:
                return response
            retry = response.headers.get("Retry-After", "")
            if len(retry) > 2 or not retry.isdecimal() or int(retry) > 10 or attempt == 2:
                raise AppError(
                    "Limite de consultas do Spotify atingido. Aguarde antes de tentar novamente."
                )
            await self.sleep(int(retry) + secrets.randbelow(250) / 1000)
        raise AppError("O Spotify nao conseguiu concluir a consulta.")

    async def _tokens(self, data: dict[str, str], *, initial: bool) -> None:
        response = await self._request("POST", TOKEN_URL, data=data)
        if response.status_code in (400, 401):
            self._access = None
            self._expires_at = 0
            if not initial:
                self.store.delete()
            raise ReconnectRequired(
                "Autorizacao expirada ou revogada. Conecte sua conta novamente."
            )
        if response.status_code != 200:
            raise AppError(
                "O Spotify nao concluiu a autorizacao. Confira o aplicativo e tente novamente."
            )
        try:
            tokens = TokenResponse.model_validate_json(response.content)
        except ValidationError as exc:
            raise AppError("O Spotify retornou uma autorizacao invalida.") from exc
        if tokens.scope is not None and READ_SCOPE not in tokens.scope.split():
            raise ReconnectRequired(
                "A autorizacao nao inclui leitura de playlists privadas. Reconecte."
            )
        if initial and tokens.scope is not None and LIBRARY_SCOPE not in tokens.scope.split():
            raise ReconnectRequired(
                "Autorize a leitura das Musicas Curtidas (user-library-read). Reconecte o Spotify."
            )
        if initial and tokens.refresh_token is None:
            raise AppError("O Spotify nao enviou a credencial para lembrar a conexao. Reconecte.")
        if tokens.refresh_token is not None:
            self.store.save(tokens.refresh_token.get_secret_value())
        self._access = tokens.access_token.get_secret_value()
        self._scopes = set(tokens.scope.split()) if tokens.scope is not None else None
        self._expires_at = self.clock() + max(0, tokens.expires_in - 30)

    async def complete_login(self, code: str, verifier: str) -> None:
        async with self._lock:
            await self._tokens(
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": CALLBACK,
                    "client_id": self.client_id,
                    "code_verifier": verifier,
                },
                initial=True,
            )

    async def _access_token(self, *, force: bool = False) -> str:
        if force or self._access is None or self.clock() >= self._expires_at:
            refresh = self.store.load()
            if not refresh:
                raise ReconnectRequired(
                    "Conecte sua conta ao Spotify para carregar sua biblioteca."
                )
            await self._tokens(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": refresh,
                    "client_id": self.client_id,
                },
                initial=False,
            )
        if self._access is None:
            raise ReconnectRequired("Conecte sua conta novamente.")
        return self._access

    def _check_library_scope(self) -> None:
        if self._scopes is not None and LIBRARY_SCOPE not in self._scopes:
            raise ReconnectRequired(
                "Autorize a leitura das Musicas Curtidas (user-library-read). Reconecte o Spotify."
            )

    @staticmethod
    def _validate_page(url: str, path: str) -> None:
        try:
            parsed = urlsplit(url)
        except ValueError as exc:
            raise AppError("O Spotify retornou um endereco de paginacao invalido.") from exc
        query = parse_qs(parsed.query, keep_blank_values=True)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "api.spotify.com"
            or parsed.path != path
            or parsed.fragment
            or not set(query).issubset({"limit", "offset"})
            or any(not value.isdecimal() for values in query.values() for value in values)
        ):
            raise AppError("O Spotify retornou um endereco de paginacao invalido.")

    async def _read_collection[T](
        self, url: str, path: str, page_type: type[CollectionPage[T]]
    ) -> list[T]:
        async with self._lock:
            result: list[T] = []
            seen: set[str] = set()
            next_url: str | None = url
            refreshed = False
            while next_url is not None:
                self._validate_page(next_url, path)
                if next_url in seen:
                    raise AppError("O Spotify retornou uma paginacao repetida. Tente novamente.")
                seen.add(next_url)
                token = await self._access_token()
                if path == "/v1/me/tracks":
                    self._check_library_scope()
                response = await self._request("GET", next_url, token=token)
                if response.status_code == 401 and not refreshed:
                    token = await self._access_token(force=True)
                    refreshed = True
                    if path == "/v1/me/tracks":
                        self._check_library_scope()
                    response = await self._request("GET", next_url, token=token)
                if response.status_code == 401:
                    self._access = None
                    self.store.delete()
                    raise ReconnectRequired("A sessao Spotify expirou. Conecte novamente.")
                if response.status_code == 403:
                    if path == "/v1/me/tracks":
                        raise ReconnectRequired(
                            "Acesso as Musicas Curtidas negado. Reconecte o Spotify e autorize "
                            "user-library-read. Confira tambem Premium e o acesso ao aplicativo."
                        )
                    raise AppError(
                        "Acesso negado pelo Spotify. Verifique Premium, permissoes e o acesso "
                        "da sua conta ao aplicativo no Developer Dashboard."
                    )
                if response.status_code != 200:
                    raise AppError(
                        "O Spotify nao conseguiu listar sua biblioteca. Tente novamente."
                    )
                try:
                    page = page_type.model_validate_json(response.content)
                except ValidationError as exc:
                    raise AppError("O Spotify retornou uma lista invalida.") from exc
                result.extend(page.items)
                next_url = page.next
            return result

    async def list_playlists(self) -> list[Playlist]:
        items = await self._read_collection(PLAYLISTS_URL, "/v1/me/playlists", PlaylistPage)
        return list({playlist.id: playlist for playlist in items}.values())

    async def list_saved_tracks(self) -> list[SavedTrack]:
        return await self._read_collection(SAVED_TRACKS_URL, "/v1/me/tracks", SavedTrackPage)

    async def disconnect(self) -> None:
        async with self._lock:
            self._access = None
            self._expires_at = 0
            self._scopes = None
            self.store.delete()
