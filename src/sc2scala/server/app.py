from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from sc2scala.__about__ import __version__
from sc2scala.errors import SignInError, UploadRefusedError, UploadTooLargeError
from sc2scala.server import account_routes, api_routes, bots_api, ladders_api, sign_in_routes
from sc2scala.server.database import create_database_engine, upgrade_database
from sc2scala.server.file_store import FileStore
from sc2scala.server.github_accounts import GitHubAccounts
from sc2scala.server.github_oauth_app import GitHubOAuthApp
from sc2scala.server.server_config import ServerConfig
from sc2scala.server.web import DatabaseSession, SignInRequired, render, signed_in_user

_PACKAGE_FOLDER = Path(__file__).parent
_SESSION_COOKIE = "scala_session"
_SESSION_DAYS = 30
_FILES_FOLDER_NAME = "files"


def create_app(config: ServerConfig, *, github_accounts: GitHubAccounts | None = None) -> FastAPI:
    """The server's app, its database brought up to date.

    People sign in with `github_accounts`, by default the OAuth app the config names, if it names one.
    """
    engine = create_database_engine(config.database_url)
    upgrade_database(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(title="Scala", version=__version__, lifespan=lifespan)
    app.state.config = config
    app.state.engine = engine
    app.state.templates = Jinja2Templates(directory=_PACKAGE_FOLDER / "templates")
    app.state.file_store = FileStore(config.data_folder / _FILES_FOLDER_NAME)
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
    app.mount("/static", StaticFiles(directory=_PACKAGE_FOLDER / "static"), name="static")
    app.include_router(sign_in_routes.router)
    app.include_router(account_routes.router)
    app.include_router(api_routes.router)
    app.include_router(bots_api.router)
    app.include_router(ladders_api.router)

    @app.get("/", response_class=HTMLResponse)
    def home_page(request: Request, session: DatabaseSession) -> HTMLResponse:
        return render(request, "home.html", user=signed_in_user(request, session))

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
