"""Publication identity and freshness, independent of HTTP and stored secrets."""
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import time

from app.core.service_catalog import catalog_revision
from app.core.network import bounded_setting


@lru_cache(maxsize=1)
def _compiler_revision() -> str:
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*.py')):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def publication_revision() -> str:
    # Environment values may contain credentials: expose only a digest.
    settings = '\0'.join(os.getenv(key, '') for key in (
        'SUBFLOW_PROVIDER_EGRESS', 'SUBFLOW_SUBSCRIPTION_USER_AGENT',
        'SUBFLOW_SHADOWROCKET_USER_AGENT', 'SUBFLOW_SUBCONVERTER_URL'))
    return hashlib.sha256((catalog_revision() + _compiler_revision() + settings).encode()).hexdigest()


def cache_ttl() -> float:
    return bounded_setting('SUBFLOW_CACHE_TTL', 30, 0, 300)


def is_fresh(metadata: dict, generation: int, revision: str) -> bool:
    age = time.time() - metadata.get('created_at', 0)
    return (metadata.get('generation') == generation and metadata.get('revision') == revision
            and metadata.get('last_status') != 'stale' and 0 <= age < cache_ttl())


def stamp(config: str, generation: int, revision: str, warnings: list, *, annotate: bool = True) -> tuple[str, dict]:
    now = time.time()
    generated = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec='seconds')
    metadata = {'generation': generation, 'revision': revision, 'created_at': now,
                'generated_at': generated, 'warnings': warnings, 'last_status': 'fresh',
                'content_sha256': hashlib.sha256(config.encode()).hexdigest()}
    identity = f'# Subflow generation={generation} revision={revision} generated-at={generated}\n'
    return (identity + config if annotate else config), metadata


def headers(metadata: dict, state: str) -> dict[str, str]:
    result = {'X-Subflow-Cache': state}
    if metadata:
        result.update({'X-Subflow-Generation': str(metadata['generation']),
                       'X-Subflow-Revision': metadata['revision'],
                       'X-Subflow-Generated-At': metadata['generated_at'],
                       'X-Subflow-Age': str(max(0, int(time.time() - metadata['created_at'])))})
    if state == 'stale':
        result['X-Subflow-Stale'] = 'true'
    return result
