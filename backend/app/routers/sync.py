from fastapi import APIRouter, Depends

from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.sync import SyncSalesRequest, SyncTaskResponse

# Импорт задачи Celery — по имени, чтобы воркер видел ту же задачу
from celery_app.tasks import sync_sales

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/sales", response_model=SyncTaskResponse)
def trigger_sync_sales(
    body: SyncSalesRequest,
    current_user: User = Depends(get_current_user),
):
    """Поставить в очередь задачу синхронизации продаж с WB за период."""
    result = sync_sales.delay(
        str(current_user.id),
        body.date_from,
        body.date_to,
    )
    return SyncTaskResponse(
        task_id=result.id,
        message="Задача синхронизации продаж поставлена в очередь.",
    )
