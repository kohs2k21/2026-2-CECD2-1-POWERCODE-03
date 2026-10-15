"""운영 산출물의 허용 경로와 내용 해시를 검사한다."""

from __future__ import annotations

import hashlib
from pathlib import Path


def verified_artifact_path(root: Path | None, relative_path: str, expected_hash: str) -> str | None:
    if root is None or not isinstance(relative_path, str):
        return None
    try:
        base = root.resolve(strict=True)
        path = (base / relative_path).resolve(strict=True)
        if not path.is_relative_to(base) or not path.is_file() or path.suffix != ".joblib":
            return None
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return str(path) if digest.hexdigest() == expected_hash else None
    except (OSError, ValueError):
        return None
