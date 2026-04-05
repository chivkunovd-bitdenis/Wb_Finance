from pydantic import BaseModel
from pydantic import Field


class SyncSalesRequest(BaseModel):
    date_from: str  # YYYY-MM-DD
    date_to: str    # YYYY-MM-DD


class SyncFunnelRequest(BaseModel):
    """Опционально: если не передать — в задаче используется окно «последние 7 дней»."""
    date_from: str | None = None
    date_to: str | None = None


class SyncTaskResponse(BaseModel):
    task_id: str
    message: str


class SyncBatchResponse(BaseModel):
    """Ответ для эндпоинтов, которые ставят в очередь несколько задач."""
    task_ids: list[str]
    message: str


class FolderMigrationRequest(BaseModel):
    folder_path: str = Field(..., description="Absolute path to folder with CSV exports")
    filename_regex: str = Field(
        ...,
        description="Regex with named group user_email; dataset is optional for CSV",
    )
    file_glob: str = Field(default="*.csv", description="Glob pattern for files in folder")
    delimiter: str = Field(default=",", min_length=1, max_length=1)
    encoding: str = Field(default="utf-8")
    dry_run: bool = Field(default=True)
    include_all_users: bool = Field(
        default=False,
        description="If false, only files for current user email are processed",
    )
    auto_create_users: bool = Field(
        default=False,
        description="If true, create missing users by email during migration",
    )
    auto_create_users_password: str | None = Field(
        default=None,
        description="Optional password for auto-created users; random is generated if omitted",
    )
    auto_create_users_is_active: bool = Field(
        default=False,
        description="Activation flag for auto-created users",
    )


class FolderFileReport(BaseModel):
    file_name: str
    user_email: str | None = None
    dataset: str | None = None
    source_rows: int = 0
    inserted_rows: int = 0
    status: str
    error: str | None = None


class FolderMigrationResponse(BaseModel):
    dry_run: bool
    total_files: int
    matched_files: int
    processed_files: int
    source_rows: int
    inserted_rows: int
    rejected_rows: int
    created_users: int
    files: list[FolderFileReport]
