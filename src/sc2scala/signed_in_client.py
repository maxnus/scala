import httpx

from sc2scala.client_credentials import ClientCredentials
from sc2scala.scala_client import ScalaClient


def signed_in_client(server_url: str | None, *, http: httpx.Client | None) -> ScalaClient:
    """A client of `server_url`, by default the last server signed in to, with the token kept for it.

    Raises `NotSignedInError` if the command holds no token for it.
    """
    server_url, token = ClientCredentials.load().server_and_token(server_url)
    return ScalaClient(server_url, token, http=http)
