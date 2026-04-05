from pydantic import BaseModel, Field


class BillingStatusResponse(BaseModel):
    subscription_status: str
    trial_ends_at: str | None
    current_period_ends_at: str | None
    auto_renew: bool
    is_access_blocked: bool
    days_left: int


class CheckoutRequest(BaseModel):
    amount: float = Field(default=1990, ge=1)
    return_url: str | None = None


class CheckoutResponse(BaseModel):
    payment_id: str
    confirmation_url: str


class WebhookResponse(BaseModel):
    ok: bool = True
