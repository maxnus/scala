import re

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from sc2scala.api_tokens import hash_of_token
from sc2scala.server.models import ApiToken
from tests.conftest import csrf_of, sign_in_dev

_TOKEN_IN_PAGE = re.compile(r"<pre>(scala_[A-Za-z0-9_-]+)</pre>")


def make_token(server: TestClient, name: str = "laptop") -> str:
    page = server.post("/account/tokens", data={"name": name, "csrf_token": csrf_of(server, "/account")}).text
    found = _TOKEN_IN_PAGE.search(page)
    assert found is not None, page
    return found.group(1)


def stored_tokens(server: TestClient) -> list[ApiToken]:
    with Session(server.app.state.engine) as session:  # type: ignore[attr-defined]
        return list(session.scalars(select(ApiToken)))


def test_a_token_is_shown_once_and_kept_as_its_hash(server: TestClient) -> None:
    sign_in_dev(server, "alice")
    token = make_token(server)
    assert token not in server.get("/account").text
    [stored] = stored_tokens(server)
    assert stored.token_hash == hash_of_token(token)
    assert stored.name == "laptop"


def test_a_token_says_whose_it_is(server: TestClient) -> None:
    sign_in_dev(server, "alice")
    token = make_token(server)
    server.cookies.clear()
    response = server.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert response.json() == {"login": "alice"}
    assert stored_tokens(server)[0].last_used_at is not None


def test_a_request_without_a_valid_token_is_refused(server: TestClient) -> None:
    assert server.get("/api/me").status_code == 401
    assert server.get("/api/me", headers={"Authorization": "Bearer scala_unknown"}).status_code == 401
    assert server.get("/api/me", headers={"Authorization": "Basic YWxpY2U6"}).status_code == 401


def test_a_deleted_token_is_refused(server: TestClient) -> None:
    sign_in_dev(server, "alice")
    token = make_token(server)
    [stored] = stored_tokens(server)
    server.post(f"/account/tokens/{stored.id}/delete", data={"csrf_token": csrf_of(server, "/account")})
    assert server.get("/api/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_nobody_deletes_another_users_token(server: TestClient) -> None:
    sign_in_dev(server, "alice")
    token = make_token(server)
    [stored] = stored_tokens(server)
    server.cookies.clear()
    sign_in_dev(server, "mallory")
    server.post(f"/account/tokens/{stored.id}/delete", data={"csrf_token": csrf_of(server, "/account")})
    assert server.get("/api/me", headers={"Authorization": f"Bearer {token}"}).json() == {"login": "alice"}


def test_a_tokens_last_use_is_written_once_a_minute_at_most(server: TestClient) -> None:
    sign_in_dev(server, "alice")
    token = make_token(server)
    server.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    first_use = stored_tokens(server)[0].last_used_at
    server.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert stored_tokens(server)[0].last_used_at == first_use
