"""Схемы ответов API для дашборда: P&L по дням, артикулы, воронка, time-series по SKU."""
from pydantic import BaseModel


def _num(v) -> float | None:
    if v is None:
        return None
    return float(v)


class PnlDayResponse(BaseModel):
    date: str
    revenue: float | None
    commission: float | None
    logistics: float | None
    penalties: float | None
    storage: float | None
    ads_spend: float | None
    cogs: float | None
    tax: float | None
    operation_expenses: float | None
    margin: float | None


class OperationalExpenseResponse(BaseModel):
    id: str
    date: str
    amount: float
    comment: str | None


class OperationalExpenseCreate(BaseModel):
    date: str
    amount: float
    comment: str | None = None


class OperationalExpenseUpdate(BaseModel):
    date: str
    amount: float
    comment: str | None = None


class ArticleResponse(BaseModel):
    nm_id: int
    vendor_code: str | None
    name: str | None
    subject_name: str | None
    cost_price: float | None


class ArticleCostUpdate(BaseModel):
    nm_id: int
    cost_price: float


class FunnelDayResponse(BaseModel):
    date: str
    nm_id: int
    vendor_code: str | None
    open_count: int | None
    cart_count: int | None
    order_count: int | None
    order_sum: float | None
    buyout_percent: float | None
    cr_to_cart: float | None
    cr_to_order: float | None


class SkuDayResponse(BaseModel):
    date: str
    nm_id: int
    revenue: float | None
    commission: float | None
    logistics: float | None
    penalties: float | None
    storage: float | None
    ads_spend: float | None
    cogs: float | None
    tax: float | None
    margin: float | None
    open_count: int | None
    cart_count: int | None
    order_count: int | None
    order_sum: float | None
