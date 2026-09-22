"""Which Drive files Αθηνα can read, and how they become `source_files` rows.

The split with app/connections/google_drive.py is deliberate: that module knows
the Drive API, this one knows what Materials can ingest. Extraction support
(`ingest/extract.py`) is the constraint, so the mapping lives on this side of
the line where it can be checked against UPLOAD_TYPES.

A Drive row holds a pointer, never bytes (migrations/005_drive_sources.sql):
the original stays in the user's Drive, we keep the derived index, and a retry
re-fetches rather than reading disk.
"""

from typing import Any

from app.connections import google_drive

FOLDER_MIME = "application/vnd.google-apps.folder"

# Native Google formats have no bytes of their own; each maps to the export
# format that best survives becoming plain text. Sheets export as CSV because
# text/plain loses the column structure entirely.
EXPORTABLE: dict[str, tuple[str, str]] = {
    "application/vnd.google-apps.document": ("text/plain", "text"),
    "application/vnd.google-apps.presentation": ("text/plain", "text"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", "text"),
}

# Everything else is fetched as-is. Kept in step with extract.UPLOAD_TYPES:
# a mime that maps to an upload_type the extractor cannot handle would import
# cleanly and then fail during ingest, which is a worse place to find out.
DOWNLOADABLE: dict[str, str] = {
    "application/pdf": "pdf",
    "text/plain": "text",
    "text/markdown": "text",
    "text/csv": "text",
    "text/html": "text",
    "image/png": "image",
    "image/jpeg": "image",
    "image/webp": "image",
    "image/gif": "image",
    "image/bmp": "image",
    "image/tiff": "image",
}

SUPPORTED_MIMES = (*EXPORTABLE, *DOWNLOADABLE)


def plan_for(mime: str) -> tuple[str, str | None] | None:
    """`(upload_type, export_mime)` for a Drive mime type, or None if Αθηνα
    cannot read it. `export_mime` is None for files fetched as-is."""
    if mime in EXPORTABLE:
        export_mime, upload_type = EXPORTABLE[mime]
        return upload_type, export_mime
    if mime in DOWNLOADABLE:
        return DOWNLOADABLE[mime], None
    return None


def _quote(value: str) -> str:
    """Escape a user string for Drive's query language.

    Drive `q` is a string grammar with single-quoted literals, so an
    unescaped apostrophe in a search term ("Fermat's theorem") is a syntax
    error at best and a query-injection at worst. Backslash first, or it
    would escape the escapes.
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


def build_query(search: str | None = None, folder_id: str | None = None) -> str:
    """List only what can actually be ingested.

    Filtering by mime here rather than in the UI keeps the page-size honest:
    a Drive full of folders and .zip files would otherwise return pages that
    render almost empty after the client filters them.
    """
    mime_clause = " or ".join(f"mimeType = '{m}'" for m in SUPPORTED_MIMES)
    parts = [f"trashed = false and ({mime_clause})"]
    if folder_id:
        parts.append(f"'{_quote(folder_id)}' in parents")
    if search and search.strip():
        parts.append(f"name contains '{_quote(search.strip())}'")
    return " and ".join(parts)


def as_drive_file(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Drive's JSON -> the picker's row shape, or None if unsupported.

    `size` is absent for native Docs and arrives as a string for everything
    else, which json.loads will not coerce.
    """
    plan = plan_for(raw.get("mimeType", ""))
    if plan is None:
        return None
    upload_type, _ = plan

    size = raw.get("size")
    try:
        size_int = int(size) if size is not None else None
    except (TypeError, ValueError):
        size_int = None

    return {
        "drive_file_id": raw["id"],
        "name": raw.get("name") or "Untitled",
        "mime_type": raw.get("mimeType", ""),
        "upload_type": upload_type,
        "modified_at": raw.get("modifiedTime"),
        "size": size_int,
        "web_view_link": raw.get("webViewLink"),
        # Native formats are exported, so what lands in Αθηνα is a text
        # rendering rather than the document itself. The picker says so.
        "exported": raw.get("mimeType", "") in EXPORTABLE,
    }


async def fetch_bytes(drive_file_id: str, mime_type: str, limit: int) -> bytes:
    """Re-fetch a Drive file's content. Used by both import and ingest retry --
    there is no stored copy on this machine to fall back to."""
    plan = plan_for(mime_type)
    if plan is None:
        raise google_drive.DriveError(
            f"Αθηνα cannot read {mime_type!r} files from Drive"
        )
    _, export_mime = plan
    token = await google_drive.access_token()
    return await google_drive.download(
        token, drive_file_id, export_mime=export_mime, limit=limit
    )
