import asyncio
import base64
import hashlib
import re
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
    CurrentUser,
    ManagedPlaylistDetails,
    MutationOutcomeUnknown,
    Playlist,
    PlaylistItem,
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
CURRENT_USER_URL = "https://api.spotify.com/v1/me"
PLAYLISTS_API_URL = "https://api.spotify.com/v1/playlists"
CREATE_PLAYLIST_URL = "https://api.spotify.com/v1/me/playlists"
LIBRARY_API_URL = "https://api.spotify.com/v1/me/library"
READ_SCOPE = "playlist-read-private"
LIBRARY_SCOPE = "user-library-read"
PUBLIC_PLAYLIST_SCOPE = "playlist-modify-public"
LIBRARY_MODIFY_SCOPE = "user-library-modify"
PROFILE_SCOPE = "user-read-private"
AUTH_SCOPES = (
    f"{READ_SCOPE} {LIBRARY_SCOPE} {PUBLIC_PLAYLIST_SCOPE} {LIBRARY_MODIFY_SCOPE} {PROFILE_SCOPE}"
)
REQUIRED_SCOPES = set(AUTH_SCOPES.split())


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
        self,
        method: str,
        url: str,
        *,
        data: dict[str, str] | None = None,
        payload: dict[str, object] | None = None,
        token: str | None = None,
        mutation: bool = False,
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        for attempt in range(3):
            try:
                response = await self.http.request(
                    method, url, data=data, json=payload, headers=headers
                )
            except httpx.TimeoutException as exc:
                if mutation:
                    raise MutationOutcomeUnknown(
                        "O Spotify nao confirmou o resultado da alteracao. "
                        "Verifique a playlist antes de tentar novamente."
                    ) from exc
                raise AppError("O Spotify demorou a responder. Tente novamente.") from exc
            except httpx.RequestError as exc:
                if mutation:
                    raise MutationOutcomeUnknown(
                        "A conexao caiu durante uma alteracao. "
                        "Verifique a playlist antes de tentar novamente."
                    ) from exc
                raise AppError(
                    "Nao foi possivel acessar o Spotify. Verifique sua conexao."
                ) from exc
            if response.status_code != 429 or mutation:
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
        previous_scopes = self._scopes
        granted_scopes: set[str] | None
        if tokens.scope is not None:
            granted_scopes = set(tokens.scope.split())
        elif initial:
            granted_scopes = set(AUTH_SCOPES.split())
        else:
            granted_scopes = previous_scopes or self.store.load_scopes()
        if initial and (granted_scopes is None or not REQUIRED_SCOPES.issubset(granted_scopes)):
            raise ReconnectRequired(
                "Autorize as permissoes solicitadas para ler e gerenciar playlists e reconecte."
            )
        if initial and tokens.refresh_token is None:
            raise AppError("O Spotify nao enviou a credencial para lembrar a conexao. Reconecte.")
        if granted_scopes is not None and granted_scopes != previous_scopes:
            self.store.save_scopes(granted_scopes)
        if tokens.refresh_token is not None:
            self.store.save(tokens.refresh_token.get_secret_value())
        self._access = tokens.access_token.get_secret_value()
        self._scopes = granted_scopes
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
            if self._scopes is None:
                self._scopes = self.store.load_scopes()
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

    def _require_scope(self, scope: str, message: str) -> None:
        if self._scopes is None or scope not in self._scopes:
            raise ReconnectRequired(message)

    async def current_account_id(self) -> str:
        async with self._lock:
            token = await self._access_token()
            self._require_scope(
                PROFILE_SCOPE,
                "Reconecte o Spotify para validar a identidade da conta antes de "
                "gerenciar playlists.",
            )
            response = await self._request("GET", CURRENT_USER_URL, token=token)
            if response.status_code == 401:
                token = await self._access_token(force=True)
                response = await self._request("GET", CURRENT_USER_URL, token=token)
            if response.status_code == 401:
                self._access = None
                raise ReconnectRequired("A sessao Spotify expirou. Conecte novamente.")
            if response.status_code == 403:
                raise ReconnectRequired(
                    "Reconecte e autorize a leitura do identificador da conta Spotify."
                )
            if response.status_code != 200:
                raise AppError("Nao foi possivel validar a identidade da conta Spotify.")
            try:
                return CurrentUser.model_validate_json(response.content).account_id
            except ValidationError as exc:
                raise AppError("O Spotify retornou uma identidade de conta invalida.") from exc

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

    async def get_managed_playlist(self, playlist_id: str) -> ManagedPlaylistDetails:
        if not re.fullmatch(r"[A-Za-z0-9]{1,128}", playlist_id):
            raise AppError("O registro contem um ID de playlist invalido.")
        async with self._lock:
            token = await self._access_token()
            response = await self._request("GET", f"{PLAYLISTS_API_URL}/{playlist_id}", token=token)
            if response.status_code == 401:
                token = await self._access_token(force=True)
                response = await self._request(
                    "GET", f"{PLAYLISTS_API_URL}/{playlist_id}", token=token
                )
            if response.status_code == 401:
                self._access = None
                raise ReconnectRequired("A sessao Spotify expirou. Conecte novamente.")
            if response.status_code == 403:
                raise AppError("O Spotify negou o acesso a uma playlist registrada.")
            if response.status_code != 200:
                raise AppError("Nao foi possivel validar uma playlist registrada.")
            try:
                playlist = ManagedPlaylistDetails.model_validate_json(response.content)
            except ValidationError as exc:
                raise AppError("O Spotify retornou detalhes invalidos de playlist.") from exc
            if playlist.id != playlist_id:
                raise AppError("O Spotify retornou uma playlist diferente do registro.")
            return playlist

    async def create_public_playlist(self, name: str) -> ManagedPlaylistDetails:
        if not name.strip() or len(name) > 100:
            raise AppError("O nome da playlist nao e valido.")
        response = await self._mutate(
            "POST",
            CREATE_PLAYLIST_URL,
            PUBLIC_PLAYLIST_SCOPE,
            {"name": name, "public": True, "collaborative": False},
            expected_statuses={201},
        )
        try:
            playlist = ManagedPlaylistDetails.model_validate_json(response.content)
        except ValidationError as exc:
            raise MutationOutcomeUnknown(
                "A playlist pode ter sido criada, mas o Spotify nao confirmou seu ID."
            ) from exc
        if playlist.public is not True or playlist.collaborative or playlist.name != name:
            raise MutationOutcomeUnknown(
                "A playlist foi criada, mas o Spotify nao confirmou sua visibilidade "
                "e configuracao."
            )
        return playlist

    async def rename_public_playlist(self, playlist_id: str, name: str) -> None:
        if (
            not re.fullmatch(r"[A-Za-z0-9]{1,128}", playlist_id)
            or not name.strip()
            or len(name) > 100
        ):
            raise AppError("O ID ou nome da playlist nao e valido.")
        await self._mutate(
            "PUT",
            f"{PLAYLISTS_API_URL}/{playlist_id}",
            PUBLIC_PLAYLIST_SCOPE,
            {"name": name},
            expected_statuses={200},
        )

    async def playlist_item_ids(self, playlist_id: str) -> list[str]:
        if not re.fullmatch(r"[A-Za-z0-9]{1,128}", playlist_id):
            raise AppError("O registro contem um ID de playlist invalido.")
        path = f"/v1/playlists/{playlist_id}/items"
        url = f"https://api.spotify.com{path}?limit=50"
        items = await self._read_collection(url, path, CollectionPage[PlaylistItem])
        item_ids = [item.item_id for item in items]
        if any(item_id is None for item_id in item_ids):
            raise AppError("A playlist contem itens que nao puderam ser verificados.")
        return [item_id for item_id in item_ids if item_id is not None]

    async def replace_playlist_items(self, playlist_id: str, uris: list[str]) -> None:
        if len(uris) > 100 or not self._valid_track_uris(uris):
            raise AppError("A lista de faixas excede o limite ou contem um item invalido.")
        await self._mutate(
            "PUT",
            f"{PLAYLISTS_API_URL}/{playlist_id}/items",
            PUBLIC_PLAYLIST_SCOPE,
            {"uris": uris},
            expected_statuses={200},
        )

    async def add_playlist_items(self, playlist_id: str, uris: list[str]) -> None:
        if not uris or len(uris) > 100 or not self._valid_track_uris(uris):
            raise AppError("O lote de faixas esta invalido ou excede 100 itens.")
        await self._mutate(
            "POST",
            f"{PLAYLISTS_API_URL}/{playlist_id}/items",
            PUBLIC_PLAYLIST_SCOPE,
            {"uris": uris},
            expected_statuses={201},
        )

    async def remove_playlist_from_library(self, playlist_id: str) -> None:
        await self._change_playlist_library_state(playlist_id, "DELETE")

    async def save_playlist_to_library(self, playlist_id: str) -> None:
        await self._change_playlist_library_state(playlist_id, "PUT")

    async def _change_playlist_library_state(self, playlist_id: str, method: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9]{1,128}", playlist_id):
            raise AppError("O registro contem um ID de playlist invalido.")
        url = f"{LIBRARY_API_URL}?uris=spotify%3Aplaylist%3A{playlist_id}"
        await self._mutate(
            method,
            url,
            LIBRARY_MODIFY_SCOPE,
            None,
            expected_statuses={200, 204},
        )

    @staticmethod
    def _valid_track_uris(uris: list[str]) -> bool:
        return all(re.fullmatch(r"spotify:track:[A-Za-z0-9]{1,128}", uri) for uri in uris)

    async def _mutate(
        self,
        method: str,
        url: str,
        scope: str,
        payload: dict[str, object] | None,
        *,
        expected_statuses: set[int],
    ) -> httpx.Response:
        async with self._lock:
            token = await self._access_token()
            self._require_scope(
                scope,
                f"Reconecte o Spotify e autorize a permissao {scope} antes de continuar.",
            )
            response = await self._request(method, url, payload=payload, token=token, mutation=True)
        if response.status_code == 401:
            self._access = None
            raise ReconnectRequired("A sessao Spotify expirou. Conecte novamente.")
        if response.status_code == 403:
            raise ReconnectRequired(
                "O Spotify negou a alteracao. Verifique as permissoes e reconecte."
            )
        if response.status_code == 429:
            raise AppError("O Spotify limitou a alteracao. Nenhuma repeticao automatica foi feita.")
        if response.status_code >= 500:
            raise MutationOutcomeUnknown(
                "O Spotify nao confirmou o resultado da alteracao. "
                "Verifique a playlist antes de tentar novamente."
            )
        if response.status_code not in expected_statuses:
            raise AppError("O Spotify recusou a alteracao da playlist.")
        return response

    async def disconnect(self) -> None:
        async with self._lock:
            self._access = None
            self._expires_at = 0
            self._scopes = None
            self.store.delete()
