"""Generate and index the synthetic demo in the configured local data directory.

Run scripts/precache_models.py once first (Docker already does that at build time).
"""
import os
from pathlib import Path
import runpy
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from config import get_settings
    settings = get_settings()
    if settings.app_env not in {'development', 'test'} or settings.vector_store != 'sqlite':
        raise SystemExit('Demo seeding requires the development/test SQLite profile.')
    # Source generators use relative paths. Keep generated inputs out of the checkout.
    for key, value in {'RAG_DB_PATH': settings.rag_db_path.resolve(),
                       'STORED_DOCUMENTS_DIR': settings.stored_documents_dir.resolve()}.items():
        os.environ[key] = str(value)
    settings.rag_db_path.parent.mkdir(parents=True, exist_ok=True)
    original = Path.cwd()
    with tempfile.TemporaryDirectory(prefix='claims-demo-') as folder:
        try:
            os.chdir(folder)
            for script in ('generate_auto_pdfs.py', 'create_sample_files.py', 'ingest_all.py'):
                runpy.run_path(str(ROOT / script), run_name='__main__')
        finally:
            os.chdir(original)


if __name__ == '__main__':
    main()
