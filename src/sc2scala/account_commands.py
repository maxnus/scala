import argparse
import getpass
from collections.abc import Callable

import httpx

from sc2scala.client_credentials import ClientCredentials, normalized_server_url
from sc2scala.scala_client import ScalaClient


def add_account_commands(add_parser: Callable[..., argparse.ArgumentParser]) -> None:
    """Adds `login`, `whoami` and `logout` to the commands whose `add_parser` this is."""
    login = add_parser(
        "login",
        help="sign the command in to a server",
        description="Signs this command in to a server with a token made on the server's account page.",
    )
    login.add_argument("server", help="the server's address, such as https://scala.example.org")
    login.add_argument("--token", help="the token; asked for if not given, which keeps it out of the shell's history")
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
    token = args.token or getpass.getpass(f"Token, from {server_url}/account: ")
    login = ScalaClient(server_url, token.strip(), http=http).me()
    credentials = ClientCredentials.load()
    credentials.add(server_url, token.strip())
    credentials.save()
    print(f"Signed in to {server_url} as {login}")
    return 0


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
