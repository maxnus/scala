"""Ladder passwords, kept as their scrypt hashes, with Python's own scrypt."""

import base64
import hashlib
import secrets

_SCRYPT_COST = 2**14
_SCRYPT_BLOCK_SIZE = 8
_SCRYPT_PARALLELISM = 1
_SALT_BYTES = 16
_HASH_BYTES = 32


def hash_of_password(password: str) -> str:
    """`password`'s scrypt hash, with a salt of its own, as `scrypt$<n>$<r>$<p>$<salt>$<hash>`."""
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = _scrypt(password, salt, _SCRYPT_COST, _SCRYPT_BLOCK_SIZE, _SCRYPT_PARALLELISM)
    return "$".join(
        [
            "scrypt",
            str(_SCRYPT_COST),
            str(_SCRYPT_BLOCK_SIZE),
            str(_SCRYPT_PARALLELISM),
            base64.b64encode(salt).decode(),
            base64.b64encode(derived).decode(),
        ]
    )


def password_matches(password: str, password_hash: str) -> bool:
    """Whether `password` is the one `password_hash` was made from."""
    scheme, cost, block_size, parallelism, salt, expected = password_hash.split("$")
    if scheme != "scrypt":
        raise ValueError(f"Not a password hash this server makes: {scheme}")
    derived = _scrypt(password, base64.b64decode(salt), int(cost), int(block_size), int(parallelism))
    return secrets.compare_digest(derived, base64.b64decode(expected))


def _scrypt(password: str, salt: bytes, cost: int, block_size: int, parallelism: int) -> bytes:
    return hashlib.scrypt(
        password.encode(), salt=salt, n=cost, r=block_size, p=parallelism, dklen=_HASH_BYTES, maxmem=64 * 1024 * 1024
    )
