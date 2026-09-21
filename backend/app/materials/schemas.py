from typing import Literal

from pydantic import BaseModel, Field

UploadType = Literal["text", "pdf", "image"]
IngestStatus = Literal[
    "pending", "extracting", "tagging", "embedding", "ready", "failed"
]


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


class UploadAccepted(BaseModel):
    """202 body: the id to poll, not a finished result."""

    source_file_id: int
    ingest_status: IngestStatus
    filename: str
    upload_type: UploadType


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
