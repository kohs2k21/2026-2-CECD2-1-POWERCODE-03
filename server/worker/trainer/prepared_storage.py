"""Verified bounded prepared files; only complete content-addressed data is read."""
import hashlib
import os
from pathlib import Path
import uuid

from trainer.prepared_contract import MAX_BYTES

MAX_NORMALIZED_BYTES = 32 * 1024 * 1024


def safe_path(root, relative, *, require_file=True):
    if root is None or not isinstance(relative, str) or '\\' in relative or not relative.startswith('prepared/'):
        raise ValueError('prepared_artifact_path_invalid')
    base = Path(root).resolve(strict=True)
    parts = Path(relative).parts
    if any(item in ('', '.', '..') for item in parts):
        raise ValueError('prepared_artifact_path_invalid')
    path = base
    for part in parts:
        path = path / part
        if path.is_symlink(): raise ValueError('prepared_artifact_path_invalid')
    if not path.resolve().is_relative_to(base) or require_file and not path.is_file():
        raise ValueError('prepared_artifact_path_invalid')
    return path


def read(root, relative, expected_hash, *, maximum=MAX_BYTES):
    path = safe_path(root, relative)
    if not 0 < path.stat().st_size <= maximum:
        raise ValueError('prepared_artifact_size_invalid')
    with path.open('rb') as stream: content = stream.read(maximum + 1)
    if len(content) > maximum or hashlib.sha256(content).hexdigest() != expected_hash:
        raise ValueError('prepared_artifact_hash_mismatch')
    return content


def store(root, dataset_id, content):
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_NORMALIZED_BYTES:
        raise ValueError('prepared_normalized_size_exceeded')
    identifier = str(uuid.UUID(str(dataset_id)))
    content_hash = hashlib.sha256(content).hexdigest()
    relative = f'prepared/{identifier}/{content_hash}.jsonl'
    path = safe_path(root, relative, require_file=False)
    if not path.parent.is_dir(): raise ValueError('prepared_artifact_path_invalid')
    if path.exists():
        read(root, relative, content_hash, maximum=MAX_NORMALIZED_BYTES)
    else:
        temporary = path.parent / (str(uuid.uuid4()) + '.tmp')
        try:
            with temporary.open('xb') as output:
                os.chmod(temporary, 0o600)
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return {'relativePath': relative, 'sha256': content_hash, 'byteSize': len(content)}
