from pydantic import BaseModel


class GatherRunResult(BaseModel):
    run_id: int
    candidates_seen: int
    imported_file_ids: list[int] = []
    refreshed_file_ids: list[int] = []
    skipped_local: list[str] = []
