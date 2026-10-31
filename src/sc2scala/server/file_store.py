import hashlib
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, TypeVar

from sc2scala.errors import UploadTooLargeError

_CHUNK_BYTES = 1024 * 1024
PARTIAL_UPLOAD_PREFIX = ".upload-"
"""How the name of an upload being received begins, before it is checked and kept."""
_CheckResult = TypeVar("_CheckResult")


@dataclass(frozen=True)
class StoredFile:
    sha256: str
    size_bytes: int
    path: Path


class FileStore:
    """The server's files, each kept under the folder of its kind as `<sha256><suffix>`, so that a file uploaded twice
    is kept once."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path_of(self, kind: str, sha256: str, suffix: str) -> Path:
        return self.root / kind / f"{sha256}{suffix}"

    def store(
        self,
        upload: BinaryIO,
        *,
        kind: str,
        suffix: str,
        max_bytes: int,
        check: Callable[[Path], _CheckResult],
    ) -> tuple[StoredFile, _CheckResult]:
        """Keeps `upload` once `check` accepts it, and returns it with what `check` returned.

        Raises `UploadTooLargeError` if it holds more than `max_bytes`, and lets `check`'s exception through; either
        way nothing is kept.
        """
        folder = self.root / kind
        folder.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size_bytes = 0
        descriptor, temporary_name = tempfile.mkstemp(dir=folder, prefix=PARTIAL_UPLOAD_PREFIX)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as received:
                while chunk := upload.read(_CHUNK_BYTES):
                    size_bytes += len(chunk)
                    if size_bytes > max_bytes:
                        raise UploadTooLargeError(f"The upload holds more than {max_bytes // _CHUNK_BYTES} MiB")
                    digest.update(chunk)
                    received.write(chunk)
            check_result = check(temporary)
            final = self.path_of(kind, digest.hexdigest(), suffix)
            os.replace(temporary, final)
        finally:
            temporary.unlink(missing_ok=True)
        return StoredFile(sha256=digest.hexdigest(), size_bytes=size_bytes, path=final), check_result
