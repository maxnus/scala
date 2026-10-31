import random
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from sc2scala.__about__ import __version__
from sc2scala.errors import SignInError, UploadRefusedError, UploadTooLargeError
from sc2scala.server import (
    account_routes,
    api_routes,
    bot_forms,
    bots_api,
    ladder_forms,
    ladder_runners_api,
    ladders_api,
    login_requests,
    machine_forms,
    matches_api,
    pages,
    runner_api,
    runner_setup,
    runners_api,
    sign_in_routes,
)
from sc2scala.server.database import create_database_engine, upgrade_database
from sc2scala.server.file_store import FileStore
from sc2scala.server.github_accounts import GitHubAccounts
from sc2scala.server.github_oauth_app import GitHubOAuthApp
from sc2scala.server.server_config import ServerConfig
from sc2scala.server.storage_tidying import StorageTidier
from sc2scala.server.web import SignInRequired, render, signed_in_user

_PACKAGE_FOLDER = Path(__file__).parent
_SESSION_COOKIE = "scala_session"
_SESSION_DAYS = 30
_FILES_FOLDER_NAME = "files"
_REFUSAL_TITLES = {403: "Not for you to see", 404: "Not found"}
_STEPS_PER_GAME_SECOND = 22.4


def create_app(
    config: ServerConfig, *, github_accounts: GitHubAccounts | None = None, tidies_storage: bool = True
) -> FastAPI:
    """The server's app, its database brought up to date.

    People sign in with `github_accounts`, by default the OAuth app the config names, if it names one. While it runs,
    it tidies its storage every hour if `tidies_storage`, as `StorageTidier` says.
    """
    engine = create_database_engine(config.database_url)
    upgrade_database(engine)
    file_store = FileStore(config.data_folder / _FILES_FOLDER_NAME)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        tidier = StorageTidier(engine, file_store, keep_logs_days=config.keep_logs_days) if tidies_storage else None
        if tidier is not None:
            tidier.start()
        try:
            yield
        finally:
            if tidier is not None:
                tidier.stop()
            engine.dispose()

    app = FastAPI(title="Scala", version=__version__, lifespan=lifespan)
    app.state.config = config
    app.state.engine = engine
    app.state.templates = Jinja2Templates(directory=_PACKAGE_FOLDER / "templates")
    app.state.templates.env.filters["when"] = _when
    app.state.templates.env.filters["game_time"] = _game_time
    app.state.file_store = file_store
    app.state.match_random = random.SystemRandom()
    app.state.refit_lock = threading.Lock()
    if github_accounts is None and config.github_sign_in:
        github_accounts = GitHubOAuthApp(client_id=config.github_client_id, client_secret=config.github_client_secret)
    app.state.github_accounts = github_accounts
    app.add_middleware(
        SessionMiddleware,
        secret_key=config.secret_key,
        session_cookie=_SESSION_COOKIE,
        max_age=_SESSION_DAYS * 24 * 3600,
        same_site="lax",
        https_only=config.public_url.startswith("https://"),
    )

    @app.middleware("http")
    async def forbid_framing(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        """Keeps every page out of other sites' frames, where a hidden page could take a signed-in user's click."""
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        return response

    app.mount("/static", StaticFiles(directory=_PACKAGE_FOLDER / "static"), name="static")
    app.include_router(sign_in_routes.router)
    app.include_router(account_routes.router)
    app.include_router(api_routes.router)
    app.include_router(bots_api.router)
    app.include_router(ladders_api.router)
    app.include_router(ladder_runners_api.router)
    app.include_router(runners_api.router)
    app.include_router(runner_setup.router)
    app.include_router(runner_api.router)
    app.include_router(matches_api.router)
    app.include_router(bot_forms.router)
    app.include_router(ladder_forms.router)
    app.include_router(machine_forms.router)
    app.include_router(login_requests.router)
    app.include_router(pages.router)

    @app.exception_handler(StarletteHTTPException)
    async def explain_refusal(request: Request, error: StarletteHTTPException) -> Response:
        """A page's refusal as a page; the API's as FastAPI answers it."""
        if request.url.path.startswith(("/api/", "/static/")):
            return await http_exception_handler(request, error)
        title = _REFUSAL_TITLES.get(error.status_code, "Something went wrong")
        with Session(engine) as session:
            user = signed_in_user(request, session)
            return render(
                request, "message.html", user=user, title=title, message=error.detail, status_code=error.status_code
            )

    @app.exception_handler(SignInRequired)
    def send_to_sign_in(request: Request, error: SignInRequired) -> RedirectResponse:
        return RedirectResponse(f"/sign-in?{urlencode({'next': error.path})}", status_code=303)

    @app.exception_handler(SignInError)
    def explain_failed_sign_in(request: Request, error: SignInError) -> HTMLResponse:
        return render(
            request, "message.html", user=None, title="Signing in failed", message=str(error), status_code=400
        )

    @app.exception_handler(UploadRefusedError)
    def refuse_upload(request: Request, error: UploadRefusedError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=413 if isinstance(error, UploadTooLargeError) else 422)

    return app


def _when(moment: datetime | None) -> str:
    """A moment to the minute, in UTC."""
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC") if moment else ""


def _game_time(steps: int | None) -> str:
    """Game steps as minutes and seconds of game time, at the game's faster speed."""
    if steps is None:
        return ""
    minutes, seconds = divmod(round(steps / _STEPS_PER_GAME_SECOND), 60)
    return f"{minutes}:{seconds:02d}"
