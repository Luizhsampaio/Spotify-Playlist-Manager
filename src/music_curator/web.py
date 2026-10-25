import asyncio
import secrets
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from music_curator.config import APP_NAME, ORIGIN, Settings
from music_curator.credentials import CredentialStore, WindowsCredentialStore
from music_curator.demo import PROTOTYPE_TRACKS
from music_curator.library import SongFilters, song_collection
from music_curator.managed_registry import (
    ManagedPlaylistRecord,
    ManagedPlaylistRegistry,
    default_registry_path,
)
from music_curator.models import (
    AppError,
    ManagedPlaylistDetails,
    MutationOutcomeUnknown,
    Playlist,
    PlaylistMutationResult,
    ReconnectRequired,
    SavedTrack,
)
from music_curator.playlist_preview import ArtistPlaylist, artist_playlists
from music_curator.spotify import (
    MANAGED_PLAYLIST_DESCRIPTION,
    PREVIOUS_MANAGED_PLAYLIST_DESCRIPTION,
    SpotifyClient,
)

BASE = Path(__file__).parent
COOKIE = "curator_session"
SESSION_SECONDS = 8 * 60 * 60
PRODUCT_PAGES = {"dashboard", "songs", "managed_playlists"}


@dataclass
class PendingLogin:
    state: str
    verifier: str
    expires_at: float


@dataclass
class PlaylistValidationIssue:
    artist_key: str
    artist_name: str
    playlist_id: str | None
    message: str
    missing: bool = False


@dataclass
class PendingPlaylistSave:
    token: str
    account_id: str
    previous_selection: set[str]
    records: dict[str, ManagedPlaylistRecord]
    missing_keys: set[str]
    tracks_revision: str
    expires_at: float


@dataclass
class BrowserSession:
    csrf: str
    expires_at: float
    pending: PendingLogin | None = None
    message: str | None = None
    message_error: bool = False
    prototype_signed_in: bool = False
    tracks: list[SavedTrack] | None = None
    filters: SongFilters = field(default_factory=SongFilters)
    import_attempted: bool = False
    import_message: str | None = None
    import_status: int = 200
    import_reconnect: bool = False
    import_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    tracks_revision: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    selected_artists: set[str] = field(default_factory=set)
    playlist_results: list[PlaylistMutationResult] = field(default_factory=list)
    pending_playlist_save: PendingPlaylistSave | None = None


async def _validate_playlist_save(
    client: SpotifyClient,
    account_id: str,
    records: dict[str, ManagedPlaylistRecord],
    selected_groups: list[ArtistPlaylist],
) -> list[PlaylistValidationIssue]:
    issues: list[PlaylistValidationIssue] = []
    groups = {group.key: group for group in selected_groups}
    registered_ids = Counter(
        record.playlist_id for record in records.values() if record.state != "removed"
    )
    for group in selected_groups:
        if not group.name.strip() or len(group.name) > 100:
            issues.append(
                PlaylistValidationIssue(
                    group.key,
                    group.name,
                    None,
                    "O nome da playlist deve ter de 1 a 100 caracteres.",
                )
            )
    ids = [
        record.playlist_id
        for record in records.values()
        if record.playlist_id is not None
        and record.state in {"active", "created"}
        and record.owner_id == account_id
    ]
    presence = await client.playlists_in_library(ids)
    for key, record in records.items():
        if record.state == "removed":
            continue
        issue = PlaylistValidationIssue(key, record.artist_name, record.playlist_id, "")
        if record.state in {"pending_create", "creation_uncertain", "uncertain"}:
            issue.message = (
                "A operacao anterior tem resultado incerto. Verifique sua conta antes de "
                "resolver o registro; uma nova criacao automatica pode gerar duplicatas."
            )
        elif record.owner_id != account_id:
            issue.message = "O proprietario registrado nao corresponde a conta conectada."
        elif key in groups and record.artist_id != groups[key].artist_id:
            issue.message = "A identidade do artista nao corresponde ao registro."
        elif record.playlist_id is None:
            issue.message = "O registro nao contem um ID verificavel."
        elif registered_ids[record.playlist_id] > 1:
            issue.message = (
                "A mesma playlist esta vinculada a mais de um artista. "
                "Resolva o conflito de vinculos antes de salvar."
            )
        elif not presence[record.playlist_id]:
            issue.message = (
                "A playlist mapeada nao esta em Sua Biblioteca. "
                "Voce pode confirmar a criacao de uma nova playlist gerenciada se o artista "
                "continuar selecionado, ou voltar e desmarca-lo para encerrar o vinculo."
            )
            issue.missing = True
        else:
            try:
                details = await client.get_managed_playlist(record.playlist_id)
            except AppError as exc:
                issue.message = (
                    f"{exc} Se a playlist foi removida, sera necessario criar uma nova "
                    "playlist gerenciada. Verifique novamente para confirmar a ausencia "
                    "antes de autorizar a substituicao."
                )
            else:
                if details.owner.id != account_id:
                    issue.message = "O proprietario da playlist nao corresponde a conta conectada."
                elif details.public is not True or details.collaborative:
                    issue.message = (
                        "A playlist deixou de ser publica ou passou a ser colaborativa. "
                        "Restaure a configuracao no Spotify se quiser manter este vinculo; "
                        "nenhuma alteracao sera desfeita automaticamente."
                    )
                elif key in groups:
                    try:
                        await client.playlist_item_ids(record.playlist_id)
                    except AppError as exc:
                        issue.message = f"Nao foi possivel validar as faixas da playlist. {exc}"
        if issue.message:
            issues.append(issue)
    return issues


def _record_with_state(
    record: ManagedPlaylistRecord,
    state: str,
    *,
    playlist_id: str | None = None,
    owner_id: str | None = None,
) -> ManagedPlaylistRecord:
    values = record.model_dump()
    values["state"] = state
    if playlist_id is not None:
        values["playlist_id"] = playlist_id
    if owner_id is not None:
        values["owner_id"] = owner_id
    return ManagedPlaylistRecord.model_validate(values)


def _playlist_result(
    group_key: str,
    name: str,
    status: Literal["completed", "partial", "failed"],
    message: str,
    playlist_id: str | None = None,
) -> PlaylistMutationResult:
    return PlaylistMutationResult(
        artist_key=group_key,
        artist_name=name,
        status=status,
        message=message,
        playlist_id=playlist_id,
    )


def _managed_destination_matches(
    details: ManagedPlaylistDetails, account_id: str, record: ManagedPlaylistRecord
) -> bool:
    return (
        details.owner.id == account_id
        and details.owner.id == record.owner_id
        and details.public is True
        and not details.collaborative
    )


async def _manage_selected_artist(
    client: SpotifyClient,
    registry: ManagedPlaylistRegistry,
    account_id: str,
    artist_key: str,
    group: ArtistPlaylist,
    record: ManagedPlaylistRecord | None,
) -> PlaylistMutationResult:
    if record is None:
        record = ManagedPlaylistRecord(
            artist_key=group.key,
            artist_id=group.artist_id,
            artist_name=group.name,
            state="pending_create",
        )
        registry.save_record(account_id, record)
        try:
            created = await client.create_public_playlist(group.name)
        except MutationOutcomeUnknown:
            uncertain = _record_with_state(record, "creation_uncertain")
            registry.save_record(account_id, uncertain)
            return _playlist_result(
                artist_key,
                group.name,
                "partial",
                "A criacao pode ter ocorrido, mas o Spotify nao confirmou o ID. "
                "Confira sua conta antes de tentar novamente.",
            )
        except AppError as exc:
            registry.delete_record(account_id, group.key)
            return _playlist_result(artist_key, group.name, "failed", str(exc))
        record = _record_with_state(
            record,
            "created",
            playlist_id=created.id,
            owner_id=created.owner.id,
        )
        try:
            registry.save_record(account_id, record)
        except AppError as exc:
            return _playlist_result(
                artist_key,
                group.name,
                "partial",
                f"A playlist foi criada, mas o ID nao pode ser confirmado no registro local. {exc}",
                created.id,
            )
    elif record.state in {"pending_create", "creation_uncertain"}:
        return _playlist_result(
            artist_key,
            group.name,
            "partial",
            "A criacao anterior tem resultado incerto. Verifique o Spotify antes de "
            "resolver o registro local.",
            record.playlist_id,
        )
    elif record.state == "uncertain":
        return _playlist_result(
            artist_key,
            group.name,
            "partial",
            "A ultima alteracao da biblioteca tem resultado incerto. "
            "Verifique a conta Spotify antes de continuar.",
            record.playlist_id,
        )
    elif record.artist_id != group.artist_id:
        return _playlist_result(
            artist_key,
            group.name,
            "failed",
            "A identidade do artista nao corresponde ao registro. Nenhuma alteracao foi feita.",
            record.playlist_id,
        )

    playlist_id = record.playlist_id
    if playlist_id is None or record.owner_id is None:
        return _playlist_result(
            artist_key, group.name, "partial", "O registro nao contem um ID verificavel."
        )
    try:
        details = await client.get_managed_playlist(playlist_id)
    except AppError as exc:
        return _playlist_result(
            artist_key,
            group.name,
            "partial" if record.state == "created" else "failed",
            str(exc),
            playlist_id,
        )
    if not _managed_destination_matches(details, account_id, record):
        return _playlist_result(
            artist_key,
            group.name,
            "failed",
            "A playlist nao corresponde ao destino publico registrado. "
            "Nenhuma alteracao foi feita.",
            playlist_id,
        )
    needs_standard_description = (
        details.description is None
        or details.description.strip().casefold() in {"", "null"}
        or details.description == PREVIOUS_MANAGED_PLAYLIST_DESCRIPTION
    )
    expected_description = (
        MANAGED_PLAYLIST_DESCRIPTION if needs_standard_description else details.description
    )
    if details.name != group.name or needs_standard_description:
        metadata_error: AppError | None = None
        try:
            await client.update_public_playlist_details(
                playlist_id,
                group.name,
                description=MANAGED_PLAYLIST_DESCRIPTION if needs_standard_description else None,
            )
        except AppError as exc:
            metadata_error = exc
        try:
            details = await client.get_managed_playlist(playlist_id)
        except AppError as exc:
            message = f"Nao foi possivel confirmar o nome e a descricao da playlist. {exc}"
            if metadata_error is not None:
                message += f" {metadata_error}"
            return _playlist_result(artist_key, group.name, "partial", message, playlist_id)
        if not _managed_destination_matches(details, account_id, record):
            return _playlist_result(
                artist_key,
                group.name,
                "partial",
                "A propriedade ou visibilidade mudou durante a verificacao. "
                "Nenhuma faixa foi alterada.",
                playlist_id,
            )
        if details.name != group.name or details.description != expected_description:
            message = "O Spotify nao confirmou o nome e a descricao esperados."
            if metadata_error is not None:
                message += f" {metadata_error}"
            return _playlist_result(artist_key, group.name, "partial", message, playlist_id)
    expected_ids = [track.id for track in group.tracks]
    if any(track_id is None for track_id in expected_ids):
        raise AppError("Uma faixa selecionada nao possui ID Spotify valido.")
    expected_uris = [f"spotify:track:{track_id}" for track_id in expected_ids]
    try:
        current_ids = await client.playlist_item_ids(playlist_id)
    except AppError as exc:
        return _playlist_result(
            artist_key,
            group.name,
            "partial" if record.state == "created" else "failed",
            str(exc),
            playlist_id,
        )
    write_error: AppError | None = None
    if current_ids != [track_id for track_id in expected_ids if track_id is not None]:
        try:
            await client.replace_playlist_items(playlist_id, expected_uris[:100])
            for start in range(100, len(expected_uris), 100):
                await client.add_playlist_items(playlist_id, expected_uris[start : start + 100])
        except AppError as exc:
            write_error = exc

    try:
        confirmed_ids = await client.playlist_item_ids(playlist_id)
    except AppError:
        message = (
            "Nao foi possivel verificar o conteudo final da playlist. Nenhum sucesso foi anunciado."
        )
        if write_error is not None:
            message = f"{message} {write_error}"
        return _playlist_result(artist_key, group.name, "partial", message, playlist_id)
    if confirmed_ids != [track_id for track_id in expected_ids if track_id is not None]:
        message = "A leitura do Spotify nao confirmou todas as faixas esperadas."
        if write_error is not None:
            message = f"{message} {write_error}"
        try:
            registry.save_record(account_id, _record_with_state(record, "active"))
        except AppError as exc:
            return _playlist_result(
                artist_key,
                group.name,
                "partial",
                f"As faixas foram confirmadas, mas o estado local nao pode ser atualizado. {exc}",
                playlist_id,
            )
        return _playlist_result(artist_key, group.name, "partial", message, playlist_id)
    registry.save_record(account_id, _record_with_state(record, "active"))
    return _playlist_result(
        artist_key,
        group.name,
        "completed",
        "Todas as faixas foram confirmadas no Spotify.",
        playlist_id,
    )


async def _remove_deselected_artist(
    client: SpotifyClient,
    registry: ManagedPlaylistRegistry,
    account_id: str,
    record: ManagedPlaylistRecord,
) -> PlaylistMutationResult | None:
    if record.state == "removed":
        registry.delete_record(account_id, record.artist_key)
        return None
    if record.state in {"pending_create", "creation_uncertain", "uncertain"}:
        return _playlist_result(
            record.artist_key,
            record.artist_name,
            "partial",
            "A playlist tem uma operacao pendente ou incerta. "
            "Verifique o Spotify antes de tentar novamente.",
            record.playlist_id,
        )
    playlist_id = record.playlist_id
    if playlist_id is None or record.owner_id is None:
        return _playlist_result(
            record.artist_key,
            record.artist_name,
            "partial",
            "O registro nao contem um ID verificavel.",
        )
    details = await client.get_managed_playlist(playlist_id)
    if not _managed_destination_matches(details, account_id, record):
        return _playlist_result(
            record.artist_key,
            record.artist_name,
            "failed",
            "A playlist nao corresponde ao destino publico registrado. "
            "Nenhuma alteracao foi feita.",
            playlist_id,
        )
    pending = _record_with_state(record, "uncertain")
    registry.save_record(account_id, pending)
    try:
        await client.remove_playlist_from_library(playlist_id)
    except MutationOutcomeUnknown:
        return _playlist_result(
            record.artist_key,
            record.artist_name,
            "partial",
            "O Spotify nao confirmou a remocao da biblioteca. "
            "Verifique sua conta antes de tentar novamente.",
            playlist_id,
        )
    except AppError as exc:
        registry.save_record(account_id, record)
        return _playlist_result(
            record.artist_key, record.artist_name, "failed", str(exc), playlist_id
        )
    try:
        registry.delete_record(account_id, record.artist_key)
    except AppError as exc:
        return _playlist_result(
            record.artist_key,
            record.artist_name,
            "partial",
            f"A playlist saiu da biblioteca, mas o ID local nao pode ser apagado. {exc}",
            playlist_id,
        )
    return _playlist_result(
        record.artist_key,
        record.artist_name,
        "completed",
        "Playlist removida de Sua Biblioteca. Ela pode continuar publica e acessivel por link.",
        playlist_id,
    )


def create_app(
    settings: Settings,
    *,
    store: CredentialStore | None = None,
    http: httpx.AsyncClient | None = None,
    demo: bool = False,
    registry_path: Path | None = None,
) -> FastAPI:
    transport = http or httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False)
    client = SpotifyClient(
        settings.client_id,
        store if store is not None else WindowsCredentialStore(settings.client_id),
        transport,
    )
    registry = ManagedPlaylistRegistry(
        registry_path if registry_path is not None else default_registry_path()
    )
    sessions: dict[str, BrowserSession] = {}
    connection_version = 0
    playlist_operation_lock = asyncio.Lock()
    templates = Jinja2Templates(directory=BASE / "templates")
    templates.env.globals["app_name"] = APP_NAME

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await transport.aclose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

    @app.middleware("http")
    async def local_only(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.headers.get("host") != "127.0.0.1:8765":
            return PlainTextResponse("Endereco local invalido.", status_code=400)
        if request.method == "POST" and request.headers.get("origin") != ORIGIN:
            return PlainTextResponse("Origem da requisicao invalida.", status_code=403)
        returning_navigation = (
            request.method == "GET"
            and request.url.path in {"/", "/entrar", "/app"}
            and request.headers.get("sec-fetch-mode") == "navigate"
            and request.headers.get("sec-fetch-dest") == "document"
        )
        if (
            request.headers.get("sec-fetch-site") == "cross-site"
            and request.url.path != "/callback"
            and not returning_navigation
        ):
            return PlainTextResponse("Acesso externo bloqueado.", status_code=403)
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                # no-referrer on form pages makes browsers send Origin: null on POST.
                "Referrer-Policy": (
                    "no-referrer" if request.url.path == "/callback" else "same-origin"
                ),
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": (
                    "default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; "
                    f"img-src 'self'{' https://i.scdn.co/image/' if not demo else ''}; "
                    "form-action 'self' https://accounts.spotify.com; "
                    "frame-ancestors 'none'; base-uri 'none'"
                ),
            }
        )
        return response

    def session_for(request: Request) -> tuple[str, BrowserSession, bool]:
        now = time.monotonic()
        for key in list(sessions):
            if sessions[key].expires_at <= now:
                del sessions[key]
        sid = request.cookies.get(COOKIE, "")
        if sid in sessions:
            return sid, sessions[sid], False
        if len(sessions) >= 32:
            del sessions[next(iter(sessions))]
        sid = secrets.token_urlsafe(32)
        session = BrowserSession(secrets.token_urlsafe(32), now + SESSION_SECONDS)
        sessions[sid] = session
        return sid, session, True

    def render(
        request: Request,
        session: BrowserSession,
        *,
        playlists: list[Playlist] | None = None,
        message: str | None = None,
        error: bool = False,
        status_code: int = 200,
    ) -> HTMLResponse:
        connected = False
        try:
            connected = client.saved_connection()
        except AppError as exc:
            message = str(exc)
            error = True
            status_code = 503
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "configured": bool(settings.client_id),
                "connected": connected,
                "csrf": session.csrf,
                "playlists": playlists,
                "message": message,
                "error": error,
                "demo": demo,
            },
            status_code=status_code,
        )

    async def verified_form(
        request: Request, *, max_fields: int = 2, max_bytes: int = 4096
    ) -> tuple[BrowserSession, dict[str, list[str]]] | None:
        sid = request.cookies.get(COOKIE, "")
        session = sessions.get(sid)
        if session is None or session.expires_at <= time.monotonic():
            return None
        if request.headers.get("content-type", "").split(";")[0] != (
            "application/x-www-form-urlencoded"
        ):
            return None
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > max_bytes:
                return None
        try:
            fields = parse_qs(
                body.decode("ascii"), max_num_fields=max_fields, keep_blank_values=True
            )
        except (UnicodeDecodeError, ValueError):
            return None
        csrf = fields.get("csrf", [])
        if len(csrf) != 1 or not secrets.compare_digest(csrf[0], session.csrf):
            return None
        return session, fields

    async def verified_session(request: Request) -> BrowserSession | None:
        verified = await verified_form(request)
        return verified[0] if verified is not None else None

    async def refresh_tracks(sid: str, session: BrowserSession) -> bool:
        version = connection_version
        items: list[SavedTrack] | None = None
        message: str | None = None
        status, reconnect = 200, False
        try:
            items = list(PROTOTYPE_TRACKS) if demo else await client.list_saved_tracks()
        except ReconnectRequired as exc:
            message, status, reconnect = str(exc), 403, True
        except AppError as exc:
            message, status = str(exc), 502
        if (
            sessions.get(sid) is not session
            or session.expires_at <= time.monotonic()
            or version != connection_version
        ):
            return False
        session.import_attempted = True
        session.import_message = message
        session.import_status = status
        session.import_reconnect = reconnect
        if status == 200:
            session.tracks = items
            session.tracks_revision = secrets.token_urlsafe(24)
        elif reconnect:
            session.tracks = None
            session.filters = SongFilters()
        return True

    async def product_page(
        request: Request,
        name: str,
        *,
        message: str | None = None,
        error: bool = False,
        status_code: int = 200,
        reconnect: bool = False,
        import_failed: bool = False,
        open_artist_dialog: bool = False,
        validation_issues: list[PlaylistValidationIssue] | None = None,
        attempted_selection: set[str] | None = None,
        playlist_records: dict[str, ManagedPlaylistRecord] | None = None,
    ) -> Response:
        sid, session, created = session_for(request)
        if message is None:
            message, session.message = session.message, None
            error, session.message_error = session.message_error, False
        signed_in = session.prototype_signed_in if demo else False
        if not demo:
            try:
                signed_in = client.saved_connection()
            except AppError as exc:
                message, error, status_code = str(exc), True, 503
        if name in PRODUCT_PAGES and signed_in and status_code == 200:
            async with session.import_lock:
                if not session.import_attempted and not await refresh_tracks(sid, session):
                    return PlainTextResponse(
                        "Sessao ou conexao alterada. Reabra a pagina.", status_code=409
                    )
            if not demo:
                try:
                    signed_in = client.saved_connection()
                except AppError as exc:
                    message, error, status_code = str(exc), True, 503
        if name in PRODUCT_PAGES and status_code == 200 and session.import_message:
            message, error, status_code = session.import_message, True, session.import_status
            reconnect = session.import_reconnect
            import_failed = True
            name = "songs"
        managed_playlist_ids: dict[str, str] = {}
        if (
            name == "managed_playlists"
            and signed_in
            and status_code == 200
            and not demo
            and playlist_records is None
        ):
            try:
                account_id = await client.current_account_id()
                session.selected_artists = registry.selection_for(account_id)
                playlist_records = registry.records_for(account_id)
            except AppError as exc:
                message, error, status_code = str(exc), True, 503
        if signed_in and playlist_records is not None:
            managed_playlist_ids = {
                artist_key: record.playlist_id
                for artist_key, record in playlist_records.items()
                if record.playlist_id is not None and record.state != "removed"
            }
        if name in PRODUCT_PAGES and not signed_in and status_code == 200:
            response: Response = RedirectResponse("/entrar", status_code=303)
        elif name == "login" and signed_in and not message:
            response = RedirectResponse("/app", status_code=303)
        else:
            response = templates.TemplateResponse(
                request=request,
                name=f"prototype_{name}.html",
                context={
                    "csrf": session.csrf,
                    "signed_in": signed_in,
                    "demo": demo,
                    "configured": bool(settings.client_id),
                    "message": message,
                    "error": error,
                    "active_page": name,
                    "tracks": session.tracks if signed_in else None,
                    "reconnect": reconnect,
                    "import_failed": import_failed,
                    "filters": session.filters,
                    "collection": song_collection(session.tracks, session.filters)
                    if name == "songs" and signed_in and session.tracks is not None
                    else None,
                    "artist_collection": artist_playlists(session.tracks)
                    if name == "managed_playlists" and signed_in and session.tracks is not None
                    else None,
                    "selected_artists": (
                        attempted_selection
                        if attempted_selection is not None
                        else session.selected_artists
                    )
                    if signed_in
                    else set(),
                    "managed_playlist_ids": managed_playlist_ids,
                    "playlist_bindings_available": playlist_records is not None,
                    "tracks_revision": session.tracks_revision,
                    "open_artist_dialog": open_artist_dialog or validation_issues is not None,
                    "playlist_results": session.playlist_results,
                    "validation_issues": validation_issues,
                    "conflicted_artist_keys": {
                        issue.artist_key for issue in validation_issues or []
                    },
                    "missing_artist_keys": {
                        issue.artist_key for issue in validation_issues or [] if issue.missing
                    },
                    "pending_playlist_save": session.pending_playlist_save,
                    "artist_draft": attempted_selection is not None,
                },
                status_code=status_code,
            )
        if created:
            response.set_cookie(COOKIE, sid, httponly=True, samesite="lax", max_age=SESSION_SECONDS)
        return response

    @app.get("/")
    async def home(request: Request) -> Response:
        return await product_page(request, "home")

    @app.get("/entrar")
    async def login_page(request: Request) -> Response:
        return await product_page(request, "login")

    @app.get("/app")
    async def dashboard(request: Request) -> Response:
        return await product_page(request, "dashboard")

    @app.get("/app/musicas")
    async def songs_page(request: Request) -> Response:
        return await product_page(request, "songs")

    @app.get("/app/playlists")
    async def managed_playlists_page(request: Request) -> Response:
        session = sessions.get(request.cookies.get(COOKIE, ""))
        if session is not None:
            session.pending_playlist_save = None
        return await product_page(request, "managed_playlists")

    @app.post("/app/playlists/por-artista/atualizar")
    async def refresh_artist_playlist_settings(request: Request) -> Response:
        verified = await verified_form(request, max_fields=10004, max_bytes=1024 * 1024)
        if verified is None:
            return PlainTextResponse(
                "Sessao ou formulario invalido. Reabra a pagina.", status_code=403
            )
        session, fields = verified
        selected = fields.get("artists", [])
        message: str | None = None
        status = 400
        records: dict[str, ManagedPlaylistRecord] | None = None
        async with session.import_lock:
            available = (
                {group.key for group in artist_playlists(session.tracks).playlists}
                if session.tracks is not None
                else set()
            )
            if set(fields) - {"csrf", "revision", "artists"}:
                message = "Formulario invalido. Use somente as opcoes apresentadas."
            elif (
                len(fields.get("revision", [])) != 1
                or not fields["revision"][0].isascii()
                or not secrets.compare_digest(fields["revision"][0], session.tracks_revision)
            ):
                message = "A colecao foi atualizada. Reabra a configuracao e tente novamente."
                status = 409
            elif len(selected) != len(set(selected)) or not set(selected).issubset(available):
                message = "Selecao de artistas invalida. Reabra a configuracao."
            else:
                try:
                    connected = session.prototype_signed_in if demo else client.saved_connection()
                    if not connected:
                        return RedirectResponse("/entrar", status_code=303)
                    account_id = None if demo else await client.current_account_id()
                    if account_id is not None:
                        records = registry.records_for(account_id)
                    session.pending_playlist_save = None
                    if not await refresh_tracks(request.cookies.get(COOKIE, ""), session):
                        return PlainTextResponse(
                            "Sessao ou conexao alterada. Reabra a pagina.", status_code=409
                        )
                    if session.import_message:
                        message, status = session.import_message, session.import_status
                    else:
                        current_keys = {
                            group.key for group in artist_playlists(session.tracks or []).playlists
                        }
                        removed_count = len(set(selected) - current_keys)
                        selected = [key for key in selected if key in current_keys]
                        if account_id is not None:
                            records = registry.records_for(account_id)
                        message = (
                            "Musicas e artistas atualizados. As marcacoes ainda nao foram salvas."
                        )
                        if removed_count:
                            message += (
                                f" {removed_count} artista(s) marcado(s) nao estao mais "
                                "na colecao. Revise a selecao antes de configurar; "
                                "nenhuma playlist foi alterada."
                            )
                        status = 200
                except AppError as exc:
                    message, status = str(exc), 503
        response = await product_page(
            request,
            "managed_playlists",
            message=message,
            error=status != 200,
            status_code=status,
            open_artist_dialog=True,
            attempted_selection=set(selected) & available if status != 200 else set(selected),
            playlist_records=records,
        )
        if "application/json" in request.headers.get("accept", ""):
            if not isinstance(response, HTMLResponse):
                return JSONResponse(
                    {"success": False, "message": "Conexao alterada. Reabra a configuracao."},
                    status_code=409,
                )
            return JSONResponse(
                {
                    "success": status == 200 and response.status_code == 200,
                    "message": message
                    if response.status_code == status
                    else "Nao foi possivel concluir a atualizacao. Reabra a configuracao.",
                    "revision": session.tracks_revision,
                    "selected_artists": sorted(session.selected_artists),
                    "html": bytes(response.body).decode("utf-8"),
                },
                status_code=response.status_code,
            )
        return response

    @app.post("/app/playlists/por-artista/salvar")
    async def save_artist_playlist_settings(request: Request) -> Response:
        verified = await verified_form(request, max_fields=10004, max_bytes=1024 * 1024)
        if verified is None:
            return PlainTextResponse(
                "Sessao ou formulario invalido. Reabra a pagina.", status_code=403
            )
        session, fields = verified
        try:
            connected = session.prototype_signed_in if demo else client.saved_connection()
        except AppError as exc:
            return await product_page(
                request, "managed_playlists", message=str(exc), error=True, status_code=503
            )
        if not connected:
            return RedirectResponse("/entrar", status_code=303)

        async with session.import_lock:
            pending = session.pending_playlist_save
            session.pending_playlist_save = None
            confirmation = fields.get("confirmation", [])
            confirming = bool(confirmation)
            confirmation_valid = (
                confirming
                and len(confirmation) == 1
                and confirmation[0].isascii()
                and pending is not None
                and pending.expires_at > time.monotonic()
                and pending.tracks_revision == session.tracks_revision
                and secrets.compare_digest(confirmation[0], pending.token)
                and not demo
            )
            message = None
            status = 400
            if session.tracks is None:
                message = "Carregue suas musicas antes de salvar a configuracao."
            elif (
                len(fields.get("revision", [])) != 1
                or not fields["revision"][0].isascii()
                or not secrets.compare_digest(fields["revision"][0], session.tracks_revision)
            ):
                message = "A colecao foi atualizada. Reabra a configuracao e selecione os artistas."
                status = 409
            elif set(fields) - (
                {"csrf", "revision", "artists", "confirmation"}
                if confirming
                else {"csrf", "revision", "artists"}
            ):
                message = "Formulario invalido. Use somente as opcoes apresentadas."
            elif confirming and not confirmation_valid:
                message = "A confirmacao expirou ou ja foi utilizada. Verifique novamente."
                status = 409
            else:
                selected = fields.get("artists", [])
                available = {group.key for group in artist_playlists(session.tracks).playlists}
                if len(selected) != len(set(selected)) or not set(selected).issubset(available):
                    message = "Selecao de artistas invalida. Reabra a configuracao."
                else:
                    groups = artist_playlists(session.tracks).playlists
                    selected_keys = set(selected)
                    if demo:
                        session.selected_artists = selected_keys
                        session.playlist_results = []
                    else:
                        records: dict[str, ManagedPlaylistRecord] | None = None
                        try:
                            async with playlist_operation_lock:
                                version = connection_version
                                account_id = await client.current_account_id()
                                records = registry.records_for(account_id)
                                previous_selection = registry.selection_for(account_id)
                                if (
                                    confirming
                                    and pending is not None
                                    and (
                                        pending.account_id != account_id
                                        or pending.records != records
                                        or pending.previous_selection != previous_selection
                                    )
                                ):
                                    raise AppError(
                                        "A conta ou os vinculos mudaram desde a confirmacao. "
                                        "Volte a configuracao e verifique novamente."
                                    )
                                selected_groups = [
                                    group for group in groups if group.key in selected_keys
                                ]
                                await client.validate_management_permissions()
                                issues = await _validate_playlist_save(
                                    client, account_id, records, selected_groups
                                )
                                missing_keys = {
                                    issue.artist_key for issue in issues if issue.missing
                                }
                                blocking_issues = [
                                    issue
                                    for issue in issues
                                    if not issue.missing or issue.artist_key in selected_keys
                                ]
                                confirmed = (
                                    confirming
                                    and pending is not None
                                    and missing_keys == pending.missing_keys
                                    and len(missing_keys) == len(issues)
                                )
                                if blocking_issues and not confirmed:
                                    if len(missing_keys) == len(issues):
                                        session.pending_playlist_save = PendingPlaylistSave(
                                            token=secrets.token_urlsafe(32),
                                            account_id=account_id,
                                            previous_selection=previous_selection,
                                            records=records,
                                            missing_keys=missing_keys,
                                            tracks_revision=session.tracks_revision,
                                            expires_at=time.monotonic() + 600,
                                        )
                                    return await product_page(
                                        request,
                                        "managed_playlists",
                                        message=(
                                            "O salvamento foi interrompido antes das alteracoes. "
                                            "Escolha como deseja continuar."
                                        ),
                                        error=True,
                                        status_code=409,
                                        validation_issues=issues,
                                        attempted_selection=selected_keys,
                                        playlist_records=records,
                                    )
                                if confirming and not confirmed:
                                    raise AppError(
                                        "A situacao das playlists mudou. Volte a configuracao "
                                        "e salve novamente; nenhuma substituicao foi feita."
                                    )
                                if (
                                    version != connection_version
                                    or sessions.get(request.cookies.get(COOKIE, "")) is not session
                                    or session.expires_at <= time.monotonic()
                                ):
                                    raise AppError(
                                        "A sessao ou conexao mudou durante a verificacao. "
                                        "Reabra a configuracao."
                                    )
                                registry.save_selection(account_id, selected_keys)
                                for key in missing_keys:
                                    registry.delete_record(account_id, key)
                                    del records[key]
                                results: list[PlaylistMutationResult] = []
                                for group in selected_groups:
                                    record = records.get(group.key)
                                    if record is not None and record.state == "removed":
                                        try:
                                            registry.delete_record(account_id, group.key)
                                        except AppError as exc:
                                            results.append(
                                                _playlist_result(
                                                    group.key,
                                                    group.name,
                                                    "failed",
                                                    str(exc),
                                                    record.playlist_id,
                                                )
                                            )
                                            continue
                                        record = None
                                    try:
                                        results.append(
                                            await _manage_selected_artist(
                                                client,
                                                registry,
                                                account_id,
                                                group.key,
                                                group,
                                                record,
                                            )
                                        )
                                    except AppError as exc:
                                        results.append(
                                            _playlist_result(
                                                group.key,
                                                group.name,
                                                "partial"
                                                if isinstance(exc, MutationOutcomeUnknown)
                                                else "failed",
                                                str(exc),
                                                record.playlist_id if record else None,
                                            )
                                        )
                                for artist_key, record in records.items():
                                    if artist_key in selected_keys:
                                        continue
                                    try:
                                        result = await _remove_deselected_artist(
                                            client, registry, account_id, record
                                        )
                                    except AppError as exc:
                                        result = _playlist_result(
                                            artist_key,
                                            record.artist_name,
                                            "partial"
                                            if isinstance(exc, MutationOutcomeUnknown)
                                            else "failed",
                                            str(exc),
                                            record.playlist_id,
                                        )
                                    if result is not None:
                                        results.append(result)
                                session.selected_artists = selected_keys
                                session.playlist_results = results
                                if not results:
                                    session.message = (
                                        "Nao havia playlists gerenciadas para alterar."
                                    )
                                    session.message_error = False
                        except AppError as exc:
                            return await product_page(
                                request,
                                "managed_playlists",
                                message=str(exc),
                                error=True,
                                status_code=502,
                                validation_issues=[],
                                attempted_selection=selected_keys,
                                playlist_records=records,
                            )
        if message:
            return await product_page(
                request,
                "managed_playlists",
                message=message,
                error=True,
                status_code=status,
                open_artist_dialog=True,
            )
        return RedirectResponse("/app/playlists", status_code=303)

    @app.post("/app/musicas/filtrar")
    async def filter_songs(request: Request) -> Response:
        verified = await verified_form(request, max_fields=5, max_bytes=16384)
        if verified is None:
            return PlainTextResponse(
                "Sessao ou formulario invalido. Reabra a pagina.", status_code=403
            )
        session, fields = verified
        try:
            connected = session.prototype_signed_in if demo else client.saved_connection()
        except AppError as exc:
            return await product_page(
                request, "songs", message=str(exc), error=True, status_code=503
            )
        if not connected:
            return RedirectResponse("/entrar", status_code=303)
        if session.tracks is None:
            return await product_page(
                request,
                "songs",
                message="Importe suas musicas antes de pesquisar ou filtrar.",
                error=True,
                status_code=400,
            )
        if any(len(values) != 1 for values in fields.values()):
            return await product_page(
                request,
                "songs",
                message="Envie um unico valor por filtro.",
                error=True,
                status_code=400,
            )
        try:
            filters = SongFilters.model_validate(
                {key: values[0] for key, values in fields.items() if key != "csrf"}
            )
        except ValidationError:
            return await product_page(
                request,
                "songs",
                message="Filtros invalidos. Use busca de ate 200 caracteres e ano com 4 digitos.",
                error=True,
                status_code=400,
            )
        session.filters = filters
        return RedirectResponse("/app/musicas", status_code=303)

    @app.post("/app/musicas/importar")
    async def import_songs(request: Request) -> Response:
        session = await verified_session(request)
        if session is None:
            return PlainTextResponse("Sessao invalida. Reabra a pagina.", status_code=403)
        try:
            connected = session.prototype_signed_in if demo else client.saved_connection()
        except AppError as exc:
            return await product_page(
                request, "songs", message=str(exc), error=True, status_code=502, import_failed=True
            )
        if not connected:
            return RedirectResponse("/entrar", status_code=303)
        async with session.import_lock:
            if not await refresh_tracks(request.cookies.get(COOKIE, ""), session):
                return PlainTextResponse(
                    "Sessao ou conexao alterada. Reabra a pagina.", status_code=409
                )
        if session.import_message:
            return await product_page(
                request,
                "songs",
                message=session.import_message,
                error=True,
                status_code=session.import_status,
                reconnect=session.import_reconnect,
                import_failed=True,
            )
        return RedirectResponse("/app/musicas", status_code=303)

    if demo:

        @app.post("/demo/entrar")
        async def prototype_enter(request: Request) -> Response:
            session = await verified_session(request)
            if session is None:
                return PlainTextResponse("Sessao invalida. Reabra a pagina.", status_code=403)
            session.prototype_signed_in = True
            session.csrf = secrets.token_urlsafe(32)
            session.tracks = None
            session.selected_artists.clear()
            session.filters = SongFilters()
            session.import_attempted = False
            session.import_message = None
            session.import_status = 200
            session.import_reconnect = False
            return RedirectResponse("/app", status_code=303)

        @app.post("/demo/sair")
        async def prototype_leave(request: Request) -> Response:
            session = await verified_session(request)
            if session is None:
                return PlainTextResponse("Sessao invalida. Reabra a pagina.", status_code=403)
            sessions.pop(request.cookies.get(COOKIE, ""), None)
            response = RedirectResponse("/", status_code=303)
            response.delete_cookie(COOKIE)
            return response

        return app

    @app.post("/connect")
    async def connect(request: Request) -> Response:
        session = await verified_session(request)
        if session is None:
            return PlainTextResponse("Sessao invalida. Reabra a pagina.", status_code=403)
        try:
            url, state, verifier = client.authorization()
        except AppError as exc:
            return await product_page(
                request, "login", message=str(exc), error=True, status_code=400
            )
        session.pending = PendingLogin(state, verifier, time.monotonic() + 600)
        return RedirectResponse(url, status_code=303)

    @app.get("/callback")
    async def callback(request: Request) -> Response:
        nonlocal connection_version
        session = sessions.get(request.cookies.get(COOKIE, ""))
        if session is None or session.expires_at <= time.monotonic():
            return await product_page(
                request,
                "login",
                message="Sessao expirada. Conecte novamente.",
                error=True,
                status_code=400,
            )
        pending, session.pending = session.pending, None
        session.message_error = True
        states = request.query_params.getlist("state")
        if (
            pending is None
            or pending.expires_at <= time.monotonic()
            or len(states) != 1
            or not secrets.compare_digest(states[0], pending.state)
        ):
            session.message = "Autorizacao invalida ou expirada. Conecte novamente."
            return RedirectResponse("/entrar", status_code=303)
        if "error" in request.query_params:
            session.message = (
                "Autorizacao cancelada ou negada no Spotify. Nenhuma mudanca foi feita."
            )
            return RedirectResponse("/entrar", status_code=303)
        codes = request.query_params.getlist("code")
        if len(codes) != 1 or not codes[0] or len(codes[0]) > 4096:
            session.message = "O Spotify nao enviou uma autorizacao valida. Conecte novamente."
            return RedirectResponse("/entrar", status_code=303)
        try:
            await client.complete_login(codes[0], pending.verifier)
            connection_version += 1
            for item in sessions.values():
                item.tracks = None
                item.selected_artists.clear()
                item.pending_playlist_save = None
                item.filters = SongFilters()
                item.pending = None
                item.import_attempted = False
                item.import_message = None
                item.import_status = 200
                item.import_reconnect = False
            session.csrf = secrets.token_urlsafe(32)
            session.message = "Conexao salva com seguranca."
            session.message_error = False
        except AppError as exc:
            session.message = str(exc)
            return RedirectResponse("/entrar", status_code=303)
        return RedirectResponse("/app", status_code=303)

    @app.post("/playlists", response_class=HTMLResponse)
    async def playlists(request: Request) -> Response:
        session = await verified_session(request)
        if session is None:
            return PlainTextResponse("Sessao invalida. Reabra a pagina.", status_code=403)
        try:
            items = await client.list_playlists()
        except AppError as exc:
            return render(request, session, message=str(exc), error=True, status_code=502)
        return render(request, session, playlists=items)

    @app.post("/disconnect")
    async def disconnect(request: Request) -> Response:
        nonlocal connection_version
        session = await verified_session(request)
        if session is None:
            return PlainTextResponse("Sessao invalida. Reabra a pagina.", status_code=403)
        # Invalidate all pending authorizations, including other tabs, before deleting credentials.
        for item in sessions.values():
            item.pending = None
        try:
            await client.disconnect()
        except AppError as exc:
            return await product_page(
                request, "dashboard", message=str(exc), error=True, status_code=503
            )
        connection_version += 1
        sessions.clear()
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(COOKIE)
        return response

    return app
