"""List what's sitting in the drop folder.

Read-only on purpose: this node never moves a file. Identity/dedup across runs
is physical rather than tracked in the DB -- a file only shows up here because
`apply_decisions` moved every previously-considered file out, either into
managed storage (imported) or `.skipped/` (rejected). A rename between runs is
therefore indistinguishable from a new file; accepted as a limitation rather
than solved with a content hash.
"""

from app.config import get_settings
from app.materials.gather.state import GatherState, LocalCandidate
from app.materials.ingest.extract import infer_upload_type

SKIPPED_DIRNAME = ".skipped"


def scan_inbox(state: GatherState) -> dict:
    inbox = get_settings().materials_inbox_path
    inbox.mkdir(parents=True, exist_ok=True)
    skipped_dir = inbox / SKIPPED_DIRNAME

    candidates: list[LocalCandidate] = []
    for path in sorted(inbox.iterdir()):
        if path == skipped_dir or not path.is_file():
            continue
        candidates.append(
            {
                "path": str(path),
                "name": path.name,
                "upload_type": infer_upload_type(path.name),
                "size": path.stat().st_size,
            }
        )

    return {"local_candidates": candidates}
