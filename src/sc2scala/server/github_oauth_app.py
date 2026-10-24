from urllib.parse import urlencode

import httpx

from sc2scala.errors import SignInError
from sc2scala.server.github_accounts import GitHubUser

_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
_USER_URL = "https://api.github.com/user"
_TIMEOUT_SECONDS = 15


class GitHubOAuthApp:
    """GitHub's sign-in through an OAuth app of the server's own, which asks for no access beyond the account's name.

    A `GitHubAccounts`. GitHub's access token serves only to read the account, and is not kept.
    """

    def __init__(self, *, client_id: str, client_secret: str, http: httpx.Client | None = None) -> None:
        self.client_id = client_id
        self._client_secret = client_secret
        self._http = http or httpx.Client(timeout=_TIMEOUT_SECONDS)

    def authorize_url(self, *, state: str, redirect_uri: str) -> str:
        query = urlencode({"client_id": self.client_id, "redirect_uri": redirect_uri, "state": state, "scope": ""})
        return f"{_AUTHORIZE_URL}?{query}"

    def user_for_code(self, code: str, *, redirect_uri: str) -> GitHubUser:
        try:
            token_response = self._http.post(
                _ACCESS_TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self._client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                headers={"Accept": "application/json"},
            )
            access_token = token_response.json().get("access_token")
            if not access_token:
                raise SignInError(f"GitHub refused the sign-in: {token_response.json().get('error', 'no token')}")
            user_response = self._http.get(
                _USER_URL,
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"},
            )
            user_response.raise_for_status()
            account = user_response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise SignInError(f"GitHub could not be asked who signed in: {error}") from error
        return GitHubUser(id=int(account["id"]), login=str(account["login"]))
