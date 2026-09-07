"""List unreferenced immutable source versions; use --apply with writers stopped.

This never removes currently indexed sources. Run after failed writes/replacements
or deletion, including when applying a document-retention policy. It intentionally
leaves queued upload staging alone because those bytes can still be needed by jobs.
"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def collect(store, apply=False):
    from backend.source_storage import source_store
    from backend.blob_store import S3DocumentBlobStore
    blobs = source_store(store)
    conn = store._connect()
    try:
        if hasattr(store, 'tenant_id'):
            rows = conn.execute('SELECT storage_key FROM documents WHERE tenant_id = %s', (store.tenant_id,)).fetchall()
        else:
            rows = conn.execute('SELECT storage_key FROM documents').fetchall()
        referenced = {r[0] for r in rows if r[0]}
    finally:
        conn.close()
    if isinstance(blobs, S3DocumentBlobStore):
        prefix = blobs.tenant_id + '/'
        pages = blobs._client.get_paginator('list_objects_v2').paginate(Bucket=blobs.bucket, Prefix=prefix + 'versions/')
        candidates = [entry['Key'][len(prefix):] for page in pages for entry in page.get('Contents', [])]
    else:
        candidates = [path.relative_to(blobs.root).as_posix() for path in (blobs.root / 'versions').rglob('*') if path.is_file()]
    garbage = sorted(set(candidates) - referenced)
    if apply:
        for key in garbage:
            blobs.delete(key)
    return garbage


def main():
    from config import get_settings
    from app_factory import build_dependencies
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Delete listed orphans; stop API and workers first.')
    args = parser.parse_args()
    deps = build_dependencies(get_settings())
    try:
        garbage = collect(deps.vector_store, apply=args.apply)
        print(f'{"Deleted" if args.apply else "Found"} {len(garbage)} unreferenced source version(s).')
    finally:
        deps.queue.close()


if __name__ == '__main__':
    main()
