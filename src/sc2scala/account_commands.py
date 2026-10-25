import argparse
import socket
import time
import webbrowser
from collections.abc import Callable

import httpx

from sc2scala.client_credentials import ClientCredentials, normalized_server_url
from sc2scala.errors import ScalaError, ServerError
from sc2scala.scala_client import ScalaClient
from sc2scala.server.api_tokens import hash_of_token, new_token

_SECONDS_BETWEEN_ASKS = 2.0


def add_account_commands(add_parser: Callable[..., argparse.ArgumentParser]) -> None:
    """Adds `login`, `whoami` and `logout` to the commands whose `add_parser` this is."""
    login = add_parser(
        "login",
        help="sign the command in to a server",
        description="Signs this command in to a server: it shows a code and opens the server's page in the browser, "
        "where you sign in and confirm the code. With --token, signs in with a token made on your page instead.",
    )
    login.add_argument("server", help="the server's address, such as https://scala.example.org")
    login.add_argument("--token", help="a token made on the server's page, to sign in without the browser")
    login.add_argument(
        "--no-browser", action="store_true", help="show the page's address rather than open it, as over SSH"
    )
    whoami = add_parser("whoami", help="say who the command is signed in as")
    whoami.add_argument("--server", help="the server to ask (default: the last one signed in to)")
    logout = add_parser(
        "logout",
        help="forget a server's token",
        description="Forgets a server's token here; delete it on the server's account page to revoke it.",
    )
    logout.add_argument("--server", help="the server to forget (default: the last one signed in to)")


def run_login(args: argparse.Namespace, *, http: httpx.Client | None) -> int:
    server_url = normalized_server_url(args.server)
    if args.token is None:
        token = _token_confirmed_in_browser(server_url, http=http, open_browser=not args.no_browser)
    else:
        token = args.token.strip()
        if not token:
            raise ScalaError(f"The token is empty: make one on {server_url}/account, or leave out --token")
    login = ScalaClient(server_url, token, http=http).me()
    credentials = ClientCredentials.load()
    credentials.add(server_url, token)
    credentials.save()
    print(f"Signed in to {server_url} as {login}")
    return 0


def _token_confirmed_in_browser(server_url: str, *, http: httpx.Client | None, open_browser: bool) -> str:
    """A token this command makes, once someone signed in on the server confirms the code it shows.

    Raises `ScalaError` if the request is refused, or nobody answers it before it expires.
    """
    token = new_token()
    asked = ScalaClient(server_url, "", http=http).request_login(
        token_hash=hash_of_token(token), name=f"sc2scala on {socket.gethostname()}"
    )
    print(f"Confirm the code {asked['code']} on {asked['confirm_url']}")
    if open_browser and not webbrowser.open(asked["confirm_url"]):
        print("Open that page in a browser: none could be opened here")
    asking = ScalaClient(server_url, "", http=http)
    signed_in = ScalaClient(server_url, token, http=http)
    while True:
        if _is_signed_in(signed_in):
            return token
        if not asking.login_request_waits(asked["code"]):
            if _is_signed_in(signed_in):
                return token
            raise ScalaError(
                f"The sign-in with the code {asked['code']} was refused, or expired: "
                f"run `sc2scala login {server_url}` again"
            )
        time.sleep(_SECONDS_BETWEEN_ASKS)


def _is_signed_in(client: ScalaClient) -> bool:
    """Whether the server takes the client's token; the request confirmed is then answered."""
    try:
        client.me()
    except ServerError as error:
        if error.status_code != 401:
            raise
        return False
    return True


def run_whoami(args: argparse.Namespace, *, http: httpx.Client | None) -> int:
    server_url, token = ClientCredentials.load().server_and_token(args.server)
    print(f"{ScalaClient(server_url, token, http=http).me()} on {server_url}")
    return 0


def run_logout(args: argparse.Namespace) -> int:
    credentials = ClientCredentials.load()
    server_url, _ = credentials.server_and_token(args.server)
    credentials.remove(server_url)
    credentials.save()
    print(f"Forgot the token for {server_url}; delete it on {server_url}/account to revoke it")
    return 0
