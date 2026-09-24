from pydantic import BaseModel, Field

from app.materials.schemas import IngestStatus, SourceOrigin, UploadType


class GatherRunResult(BaseModel):
    run_id: int
    # The Knowledge-Sync row this run wrote -- lets the caller link straight
    # to it rather than re-deriving which session was just created.
    session_id: int
    candidates_seen: int
    imported_file_ids: list[int] = []
    refreshed_file_ids: list[int] = []
    skipped_local: list[str] = []


class GatherFolderConfig(BaseModel):
    """Both null means gather scans all of Drive -- the default."""

    folder_id: str | None = None
    folder_name: str | None = None


class GatherFolderUpdate(BaseModel):
    # A pasted Drive folder link or bare id -- see drive.extract_folder_id.
    folder: str = Field(min_length=1, max_length=2048)


class GatherTopicFile(BaseModel):
    """One file's contribution to one topic -- a file can appear under
    several topics, since tagging is per-chunk, not per-file."""

    source_file_id: int
    filename: str
    upload_type: UploadType
    origin: SourceOrigin
    drive_url: str | None = None
    chunk_count: int


class GatherTopic(BaseModel):
    topic_id: int
    topic_name: str
    files: list[GatherTopicFile]


class GatherPendingFile(BaseModel):
    """A file this run touched that has no chunks tagged to any topic yet --
    still mid-ingest, or tagging degraded/failed. `ingest_status` is what
    distinguishes "check back later" from "something went wrong"."""

    source_file_id: int
    filename: str
    upload_type: UploadType
    origin: SourceOrigin
    drive_url: str | None = None
    ingest_status: IngestStatus


class GatherRunMaterials(BaseModel):
    """What a gather run actually added, organised the way a student thinks
    about it -- by topic, not by file, since one lecture PDF routinely spans
    several. Computed fresh from current tagging state rather than frozen at
    run time, so it stays correct as ingestion finishes or topics get
    merged/renamed later -- and so a file deleted from Materials afterward
    (an ordinary thing to do) shows up under `removed` by the name it had
    when this run touched it, rather than silently disappearing from the
    run's history as if it never happened."""

    session_id: int
    topics: list[GatherTopic]
    pending: list[GatherPendingFile]
    removed: list[str]
    skipped: list[str]


class TestModeStatus(BaseModel):
    enabled: bool
