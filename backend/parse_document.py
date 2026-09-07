"""Isolated parser entry point; no models, network clients, or app construction."""
import os
from pathlib import Path
import sys


def main():
    path, kind, output, max_chars = sys.argv[1:]
    if os.name == 'posix':
        import resource
        # A malformed PDF must not consume unbounded address space.
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    from backend.rag_engine import DocumentParser
    from backend.document_limits import check_text, DocumentLimitError
    try:
        text = check_text(DocumentParser.parse(path, kind), int(max_chars))
        Path(output).write_text(text, encoding='utf-8')
    except DocumentLimitError:
        return 3
    except (Exception, MemoryError):
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
