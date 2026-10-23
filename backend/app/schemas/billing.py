import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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


MarketplaceCode = Literal["wb", "ozon"]

_INN_PATTERN = re.compile(r"[0-9]{10}(?:[0-9]{2})?")
_KPP_PATTERN = re.compile(r"[0-9]{9}")


class BillingRequisitesIn(BaseModel):
    """Реквизиты покупателя, на которого выставляется счёт."""

    name: str = Field(min_length=1, max_length=255)
    inn: str
    kpp: str | None = None
    address: str = Field(min_length=1, max_length=500)

    @field_validator("name", "address", "inn", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("kpp", mode="before")
    @classmethod
    def _normalize_kpp(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def _check_identifiers(self) -> "BillingRequisitesIn":
        if not _INN_PATTERN.fullmatch(self.inn):
            raise ValueError("ИНН должен состоять из 10 или 12 цифр")
        if self.kpp is not None and not _KPP_PATTERN.fullmatch(self.kpp):
            raise ValueError("КПП должен состоять из 9 цифр")
        return self


class BillingRequisitesResponse(BaseModel):
    name: str
    inn: str
    kpp: str | None
    address: str


class CreateInvoiceRequest(BaseModel):
    marketplaces: list[MarketplaceCode] = Field(min_length=1, max_length=2)


class BillingInvoiceResponse(BaseModel):
    id: str
    number: int
    number_display: str
    issued_at: str
    period_start: date
    period_end: date
    marketplaces: list[str]
    amount: float
    status: str
    paid_at: str | None
    buyer: BillingRequisitesResponse


class AdminBillingInvoiceResponse(BillingInvoiceResponse):
    user_email: str | None


class InvoicePreviewResponse(BaseModel):
    period_start: date
    period_end: date
    marketplaces: list[str]
    amount: float
