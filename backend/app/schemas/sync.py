from pydantic import BaseModel


class SyncSalesRequest(BaseModel):
    date_from: str  # YYYY-MM-DD
    date_to: str    # YYYY-MM-DD


class SyncTaskResponse(BaseModel):
    task_id: str
    message: str
