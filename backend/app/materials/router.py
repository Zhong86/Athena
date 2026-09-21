"""HTTP surface for Materials.

Uploads return 202, not 201: tagging a 600-chunk PDF is minutes of Hermes
calls, so the request stores the bytes, starts a background run, and hands back
an id to poll.
"""

from pathlib import Path

import anyio
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Query, UploadFile

from app.config import get_settings
from app.db import connection
from app.materials import repository as repo
from app.materials import search as search_module
from app.materials import vectors as vector_store
from app.materials.ingest.extract import UPLOAD_TYPES, infer_upload_type
from app.materials.ingest.pipeline import ingest
from app.materials.schemas import (
    ChunkOut,
    ChunkPage,
    SearchRequest,
    SearchResponse,
    SourceFile,
    TextUpload,
    Topic,
    TopicDetail,
    UploadAccepted,
)

router = APIRouter(prefix="/materials", tags=["materials"])


def _store_bytes(file_id: int, filename: str, data: bytes) -> str:
    """Keep the original so a failed extract can be retried without a
    re-upload. The id-scoped directory means two files of the same name cannot
    overwrite each other."""
    settings = get_settings()
    directory = settings.uploads_path / str(file_id)
    directory.mkdir(parents=True, exist_ok=True)
    # Only the basename: a filename from a browser is untrusted input, and
    # "../../etc/passwd" must not escape the uploads directory.
    path = directory / Path(filename).name
    path.write_bytes(data)
    return str(path)


def _accept(filename: str, upload_type: str, data: bytes, tasks: BackgroundTasks):
    with connection() as conn:
        source_file = repo.create_source_file(
            conn, filename=filename, upload_type=upload_type, byte_size=len(data)
        )
    file_id = source_file["id"]

    stored_path = _store_bytes(file_id, filename, data)
    with connection() as conn:
        conn.execute(
            "UPDATE source_files SET stored_path = ? WHERE id = ?",
            (stored_path, file_id),
        )

    tasks.add_task(ingest, file_id)
    return UploadAccepted(
        source_file_id=file_id,
        ingest_status="pending",
        filename=filename,
        upload_type=upload_type,
    )


@router.post("/uploads", response_model=UploadAccepted, status_code=202)
async def upload_file(
    tasks: BackgroundTasks, file: UploadFile = File(...)
) -> UploadAccepted:
    upload_type = infer_upload_type(file.filename or "", file.content_type)
    if upload_type not in UPLOAD_TYPES:
        raise HTTPException(
            415,
            f"unsupported file type: {file.filename!r} "
            f"({file.content_type}). Supported: text, PDF, images.",
        )

    data = await file.read()
    if not data:
        raise HTTPException(400, "the uploaded file is empty")

    max_bytes = get_settings().max_upload_bytes
    if len(data) > max_bytes:
        raise HTTPException(
            413, f"file is larger than the {max_bytes // (1024 * 1024)}MB limit"
        )

    return _accept(file.filename or "upload", upload_type, data, tasks)


@router.post("/uploads/text", response_model=UploadAccepted, status_code=202)
async def upload_text(body: TextUpload, tasks: BackgroundTasks) -> UploadAccepted:
    return _accept(body.filename, "text", body.text.encode("utf-8"), tasks)


@router.get("/uploads", response_model=list[SourceFile])
def list_uploads() -> list[SourceFile]:
    with connection() as conn:
        return [SourceFile(**f) for f in repo.list_source_files(conn)]


@router.get("/uploads/{file_id}", response_model=SourceFile)
def get_upload(file_id: int) -> SourceFile:
    """Polled by the UI while an ingest runs."""
    with connection() as conn:
        source_file = repo.get_source_file(conn, file_id)
    if source_file is None:
        raise HTTPException(404, f"source file {file_id} not found")
    return SourceFile(**source_file)


@router.post("/uploads/{file_id}/retry", response_model=SourceFile, status_code=202)
def retry_upload(file_id: int, tasks: BackgroundTasks) -> SourceFile:
    with connection() as conn:
        source_file = repo.get_source_file(conn, file_id)
        if source_file is None:
            raise HTTPException(404, f"source file {file_id} not found")
        if source_file["ingest_status"] not in repo.RESTARTABLE:
            raise HTTPException(
                409,
                f"source file {file_id} is {source_file['ingest_status']}, "
                "not failed -- nothing to retry",
            )
        repo.set_status(conn, file_id, "pending")
        source_file = repo.get_source_file(conn, file_id)

    tasks.add_task(ingest, file_id)
    return SourceFile(**source_file)


@router.delete("/uploads/{file_id}", status_code=204)
async def delete_upload(file_id: int) -> None:
    with connection() as conn:
        source_file = repo.get_source_file(conn, file_id)
        if source_file is None:
            raise HTTPException(404, f"source file {file_id} not found")
        # Chunks cascade in SQL; the vectors and the stored bytes do not.
        repo.delete_source_file(conn, file_id)

    await anyio.to_thread.run_sync(vector_store.delete_by_source_file, file_id)

    stored_path = source_file.get("stored_path")
    if stored_path:
        directory = Path(stored_path).parent
        Path(stored_path).unlink(missing_ok=True)
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()


@router.get("/topics", response_model=list[Topic])
def list_topics() -> list[Topic]:
    with connection() as conn:
        return [Topic(**t) for t in repo.list_topics(conn)]


@router.get("/topics/{topic_id}", response_model=TopicDetail)
def get_topic(topic_id: int) -> TopicDetail:
    with connection() as conn:
        topic = repo.get_topic(conn, topic_id)
        if topic is None:
            raise HTTPException(404, f"topic {topic_id} not found")
        sources = repo.sources_for_topic(conn, topic_id)
        chunk_count = repo.count_chunks_for_topic(conn, topic_id)

    return TopicDetail(**topic, sources=sources, chunk_count=chunk_count)


@router.get("/topics/{topic_id}/chunks", response_model=ChunkPage)
def get_topic_chunks(
    topic_id: int,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> ChunkPage:
    with connection() as conn:
        if repo.get_topic(conn, topic_id) is None:
            raise HTTPException(404, f"topic {topic_id} not found")
        rows = repo.chunks_for_topic(conn, topic_id, limit=limit, offset=offset)
        total = repo.count_chunks_for_topic(conn, topic_id)

    return ChunkPage(
        items=[ChunkOut(**r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.post("/search", response_model=SearchResponse)
async def search(body: SearchRequest) -> SearchResponse:
    """HTTP face of `search_materials`. Embedding is CPU-bound, so it runs off
    the event loop."""
    result = await anyio.to_thread.run_sync(
        lambda: search_module.search_materials(body.topic, body.query, limit=body.limit)
    )
    return SearchResponse(**result)
