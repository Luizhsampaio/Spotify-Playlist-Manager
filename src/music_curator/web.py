import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from music_curator.config import ORIGIN, Settings
from music_curator.credentials import CredentialStore, WindowsCredentialStore
from music_curator.models import AppError, Playlist
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
            and request.url.path == "/"
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

    async def verified_session(request: Request) -> BrowserSession | None:
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
            if len(body) > 4096:
                return None
        try:
            fields = parse_qs(body.decode("ascii"), max_num_fields=2)
        except (UnicodeDecodeError, ValueError):
            return None
        csrf = fields.get("csrf", [])
        if len(csrf) != 1 or not secrets.compare_digest(csrf[0], session.csrf):
            return None
        return session

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> Response:
        sid, session, created = session_for(request)
        message, session.message = session.message, None
        response = render(request, session, message=message)
        if created:
            response.set_cookie(COOKIE, sid, httponly=True, samesite="lax", max_age=SESSION_SECONDS)
        return response

    @app.post("/connect")
    async def connect(request: Request) -> Response:
        session = await verified_session(request)
        if session is None:
            return PlainTextResponse("Sessao invalida. Reabra a pagina.", status_code=403)
        try:
            url, state, verifier = client.authorization()
        except AppError as exc:
            return render(request, session, message=str(exc), error=True, status_code=400)
        session.pending = PendingLogin(state, verifier, time.monotonic() + 600)
        if demo:
            url = "/callback?" + urlencode({"state": state, "code": "synthetic-code"})
        return RedirectResponse(url, status_code=303)

    @app.get("/callback")
    async def callback(request: Request) -> Response:
        session = sessions.get(request.cookies.get(COOKIE, ""))
        if session is None or session.expires_at <= time.monotonic():
            return PlainTextResponse(
                "Sessao expirada. Volte a pagina inicial e conecte novamente.", status_code=400
            )
        pending, session.pending = session.pending, None
        states = request.query_params.getlist("state")
        if (
            pending is None
            or pending.expires_at <= time.monotonic()
            or len(states) != 1
            or not secrets.compare_digest(states[0], pending.state)
        ):
            session.message = "Autorizacao invalida ou expirada. Conecte novamente."
            return RedirectResponse("/", status_code=303)
        if "error" in request.query_params:
            session.message = (
                "Autorizacao cancelada ou negada no Spotify. Nenhuma mudanca foi feita."
            )
            return RedirectResponse("/", status_code=303)
        codes = request.query_params.getlist("code")
        if len(codes) != 1 or not codes[0] or len(codes[0]) > 4096:
            session.message = "O Spotify nao enviou uma autorizacao valida. Conecte novamente."
            return RedirectResponse("/", status_code=303)
        try:
            await client.complete_login(codes[0], pending.verifier)
            session.message = (
                "Conexao salva com seguranca. Agora voce pode carregar suas playlists."
            )
        except AppError as exc:
            session.message = str(exc)
        return RedirectResponse("/", status_code=303)

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
        session = await verified_session(request)
        if session is None:
            return PlainTextResponse("Sessao invalida. Reabra a pagina.", status_code=403)
        # Invalidate all pending authorizations, including other tabs, before deleting credentials.
        for item in sessions.values():
            item.pending = None
        try:
            await client.disconnect()
        except AppError as exc:
            return render(request, session, message=str(exc), error=True, status_code=503)
        sessions.clear()
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(COOKIE)
        return response

    return app
