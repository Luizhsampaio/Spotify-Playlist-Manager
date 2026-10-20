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
    Playlist,
    PlaylistPage,
    ReconnectRequired,
    TokenResponse,
)

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
PLAYLISTS_URL = "https://api.spotify.com/v1/me/playlists?limit=50"
READ_SCOPE = "playlist-read-private"


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
                "scope": READ_SCOPE,
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
            raise AppError("A autorizacao nao inclui leitura de playlists privadas. Reconecte.")
        if initial and tokens.refresh_token is None:
            raise AppError("O Spotify nao enviou a credencial para lembrar a conexao. Reconecte.")
        if tokens.refresh_token is not None:
            self.store.save(tokens.refresh_token.get_secret_value())
        self._access = tokens.access_token.get_secret_value()
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
                raise ReconnectRequired("Conecte sua conta ao Spotify para carregar as playlists.")
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

    @staticmethod
    def _validate_page(url: str) -> None:
        try:
            parsed = urlsplit(url)
        except ValueError as exc:
            raise AppError("O Spotify retornou um endereco de paginacao invalido.") from exc
        query = parse_qs(parsed.query, keep_blank_values=True)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "api.spotify.com"
            or parsed.path != "/v1/me/playlists"
            or parsed.fragment
            or not set(query).issubset({"limit", "offset"})
            or any(not value.isdecimal() for values in query.values() for value in values)
        ):
            raise AppError("O Spotify retornou um endereco de paginacao invalido.")

    async def list_playlists(self) -> list[Playlist]:
        async with self._lock:
            result: dict[str, Playlist] = {}
            seen: set[str] = set()
            url: str | None = PLAYLISTS_URL
            refreshed = False
            while url is not None:
                self._validate_page(url)
                if url in seen:
                    raise AppError("O Spotify retornou uma paginacao repetida. Tente novamente.")
                seen.add(url)
                token = await self._access_token()
                response = await self._request("GET", url, token=token)
                if response.status_code == 401 and not refreshed:
                    token = await self._access_token(force=True)
                    refreshed = True
                    response = await self._request("GET", url, token=token)
                if response.status_code == 401:
                    self._access = None
                    self.store.delete()
                    raise ReconnectRequired("A sessao Spotify expirou. Conecte novamente.")
                if response.status_code == 403:
                    raise AppError(
                        "Acesso negado pelo Spotify. Verifique Premium, permissoes e o acesso "
                        "da sua conta ao aplicativo no Developer Dashboard."
                    )
                if response.status_code != 200:
                    raise AppError(
                        "O Spotify nao conseguiu listar suas playlists. Tente novamente."
                    )
                try:
                    page = PlaylistPage.model_validate_json(response.content)
                except ValidationError as exc:
                    raise AppError("O Spotify retornou uma lista de playlists invalida.") from exc
                for playlist in page.items:
                    result[playlist.id] = playlist
                url = page.next
            return list(result.values())

    async def disconnect(self) -> None:
        async with self._lock:
            self._access = None
            self._expires_at = 0
            self.store.delete()
