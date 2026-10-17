"""Content-addressed bounded JSON artifacts; DB finalization owns publication."""
import hashlib
import json
import os
from pathlib import Path
import uuid

MAX_BYTES = 64 * 1024 * 1024


def store(root, job_id, document):
    if root is None:
        raise ValueError('synthetic_artifact_root_required')
    root = Path(root).resolve(strict=True)
    job_key = str(uuid.UUID(str(job_id)))
    directory = root
    for component in ('evaluations', job_key):
        directory = directory / component
        if directory.is_symlink():
            raise ValueError('synthetic_artifact_path_invalid')
        directory.mkdir(exist_ok=True)
        if not directory.resolve(strict=True).is_relative_to(root):
            raise ValueError('synthetic_artifact_path_invalid')
    payload = json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    if len(payload) > MAX_BYTES:
        raise ValueError('synthetic_artifact_size_exceeded')
    content_hash = hashlib.sha256(payload).hexdigest()
    destination = directory / (content_hash + '.json')
    if destination.exists():
        if destination.is_symlink() or destination.stat().st_size > MAX_BYTES or hashlib.sha256(destination.read_bytes()).hexdigest() != content_hash:
            raise ValueError('synthetic_artifact_hash_invalid')
    else:
        temporary = directory / (str(uuid.uuid4()) + '.tmp')
        try:
            with temporary.open('xb') as stream:
                os.chmod(temporary, 0o600)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return {'relativePath': destination.relative_to(root).as_posix(), 'sha256': content_hash,
            'byteSize': len(payload), 'formatVersion': 'synthetic-evaluation-artifact-v1'}
