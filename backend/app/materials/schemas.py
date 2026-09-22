from typing import Literal

from pydantic import BaseModel, Field

UploadType = Literal["text", "pdf", "image"]
IngestStatus = Literal[
    "pending", "extracting", "tagging", "embedding", "ready", "failed"
]
# Where the original lives. 'local' bytes are on our disk; a 'drive' original
# stays in the user's Drive and we hold a pointer plus the derived index.
SourceOrigin = Literal["local", "drive"]


class TextUpload(BaseModel):
    """The paste box on the Materials page."""

    filename: str = Field(min_length=1, max_length=255, default="Pasted text")
    text: str = Field(min_length=1)


class SourceFile(BaseModel):
    id: int
    filename: str
    upload_type: UploadType
    uploaded_at: str
    ingest_status: IngestStatus
    ingest_error: str | None = None
    byte_size: int | None = None
    chunk_count: int = 0
    origin: SourceOrigin = "local"
    # Only set for origin='drive'. The link the Sources list opens -- the file
    # in Drive, not a copy of it here.
    drive_url: str | None = None
    drive_modified_at: str | None = None


class UploadAccepted(BaseModel):
    """202 body: the id to poll, not a finished result."""

    source_file_id: int
    ingest_status: IngestStatus
    filename: str
    upload_type: UploadType


class DriveFile(BaseModel):
    """One row of the Drive picker. Not a `source_files` row -- nothing has
    been imported yet at this point."""

    drive_file_id: str
    name: str
    mime_type: str
    # How Αθηνα would ingest it, so the picker can group and explain.
    upload_type: UploadType
    modified_at: str | None = None
    # Absent for native Google formats, which have no bytes of their own.
    size: int | None = None
    web_view_link: str | None = None
    # True for Docs/Sheets/Slides: what Αθηνα reads is an exported text
    # rendering, not the document itself, and the picker says so.
    exported: bool = False
    # Set when this file is already in Materials, so the picker can offer
    # "re-import" instead of silently making a duplicate.
    source_file_id: int | None = None


class DriveFilePage(BaseModel):
    items: list[DriveFile] = []
    # Drive's opaque cursor; None on the last page.
    next_page_token: str | None = None


class DriveImportRequest(BaseModel):
    # Ids only. Filename and mime type are re-read from Drive server-side --
    # they decide how bytes are extracted, so they are not the browser's to
    # assert.
    file_ids: list[str] = Field(min_length=1, max_length=25)


class DriveImportResult(BaseModel):
    """202 body. Partial success is normal: one unreadable file should not
    cost the user the other nine."""

    accepted: list[UploadAccepted] = []
    # `{filename or id: reason}` for files that could not be started.
    rejected: dict[str, str] = {}


class Topic(BaseModel):
    id: int
    name: str
    description: str | None = None
    # -1 means no signal yet, per the spec's default.
    user_understanding: int = -1
    auto_created: bool = True
    chunk_count: int = 0
    source_count: int = 0


class TopicSource(BaseModel):
    """One row of the topic page's material list: "9 of 34 chunks tagged to
    Entropy · rest tagged to Heat transfer, Second law"."""

    source_file_id: int
    filename: str
    upload_type: UploadType
    uploaded_at: str
    ingest_status: IngestStatus
    chunks_in_topic: int
    chunks_total: int
    other_topics: list[str] = []
    # Carried here too so a Drive file opens from the topic page the same way
    # it does from the Sources list -- one row, one behaviour.
    origin: SourceOrigin = "local"
    drive_url: str | None = None


class TopicDetail(BaseModel):
    id: int
    name: str
    description: str | None = None
    user_understanding: int = -1
    auto_created: bool = True
    chunk_count: int = 0
    sources: list[TopicSource] = []


class ChunkOut(BaseModel):
    id: int
    text: str
    topic_id: int | None = None
    source_file_id: int
    filename: str
    order_index: int


class ChunkPage(BaseModel):
    items: list[ChunkOut]
    total: int
    limit: int
    offset: int


class SearchRequest(BaseModel):
    topic: str | None = None
    query: str = Field(min_length=1)
    limit: int = Field(5, ge=1, le=50)


class SearchHit(BaseModel):
    chunk_id: int
    text: str
    topic_id: int | None = None
    topic_name: str | None = None
    source_file_id: int
    source_filename: str
    distance: float | None = None


class SearchResponse(BaseModel):
    # Echoes how `topic` was interpreted so a caller can tell a real filter
    # from a fallback to searching everything.
    topic_resolved: str | None = None
    topic_matched: bool = False
    results: list[SearchHit] = []
