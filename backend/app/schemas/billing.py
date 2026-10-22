from pydantic import BaseModel, Field


class BillingStatusResponse(BaseModel):
    subscription_status: str
    trial_ends_at: str | None
    current_period_ends_at: str | None
    auto_renew: bool
    is_access_blocked: bool
    days_left: int
    # own — подписка самого пользователя; granted — доступ по подписке владельца магазина
    access_source: str = "own"
    store_owner_email: str | None = None
    # До какого срока действует доступ (для granted — срок подписки владельца), None если неизвестно/бессрочно
    access_until: str | None = None


class CheckoutRequest(BaseModel):
    amount: float = Field(default=1990, ge=1)
    return_url: str | None = None


class CheckoutResponse(BaseModel):
    payment_id: str
    confirmation_url: str


class WebhookResponse(BaseModel):
    ok: bool = True


class YookassaSyncReturnResponse(BaseModel):
    """Результат опроса ЮKassa после возврата пользователя на return_url."""

    activated: bool
    payment_status: str | None = None
    detail: str | None = None
