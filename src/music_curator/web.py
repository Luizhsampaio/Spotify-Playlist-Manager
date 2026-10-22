import asyncio
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from music_curator.config import ORIGIN, Settings
from music_curator.credentials import CredentialStore, WindowsCredentialStore
from music_curator.demo import PROTOTYPE_TRACKS
from music_curator.library import SongFilters, song_collection
from music_curator.models import AppError, Playlist, ReconnectRequired, SavedTrack
from music_curator.spotify import SpotifyClient

BASE = Path(__file__).parent
COOKIE = "curator_session"
SESSION_SECONDS = 8 * 60 * 60


@dataclass
class PendingLogin:
    state: str
    verifier: str
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


def create_app(
    settings: Settings,
    *,
    store: CredentialStore | None = None,
    http: httpx.AsyncClient | None = None,
    demo: bool = False,
) -> FastAPI:
    transport = http or httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False)
    client = SpotifyClient(
        settings.client_id,
        store if store is not None else WindowsCredentialStore(settings.client_id),
        transport,
    )
    sessions: dict[str, BrowserSession] = {}
    connection_version = 0
    templates = Jinja2Templates(directory=BASE / "templates")

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
                    "default-src 'none'; style-src 'self'; img-src 'self'; "
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
        if name in {"dashboard", "songs"} and signed_in and status_code == 200:
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
        if name in {"dashboard", "songs"} and status_code == 200 and session.import_message:
            message, error, status_code = session.import_message, True, session.import_status
            reconnect = session.import_reconnect
            import_failed = True
            name = "songs"
        if name in {"dashboard", "songs"} and not signed_in and status_code == 200:
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
