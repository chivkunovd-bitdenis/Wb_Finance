from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.models.base import Base, uuid_gen


class ProductGenerationJob(Base):
    """
    Черновик/задача полной ИИ-генерации товара (монолит).
    Цена хранится в копейках (целое), согласовано с контрактом PG-1.2.
    """

    __tablename__ = "product_generation_jobs"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    status = Column(String(32), nullable=False, default="draft", index=True)

    pipeline_run_id = Column(String(64), nullable=True)

    vendor_code = Column(String(255), nullable=True)
    title = Column(String(1000), nullable=True)
    brand = Column(String(500), nullable=True)
    # WB «предмет» (категория карточки); публикация в PG-5 может потребовать заполнения позже.
    wb_subject_id = Column(Integer, nullable=True)
    description_user = Column(Text, nullable=True)
    seo_description = Column(Text, nullable=True)

    price_kopeks = Column(Integer, nullable=True)

    dimensions_length = Column(Numeric(12, 4), nullable=True)
    dimensions_width = Column(Numeric(12, 4), nullable=True)
    dimensions_height = Column(Numeric(12, 4), nullable=True)
    weight_brutto = Column(Numeric(12, 4), nullable=True)

    sizes_json = Column(JSONB, nullable=True)
    reference_paths_json = Column(JSONB, nullable=True)

    selected_main_asset_id = Column(String(64), nullable=True)
    selected_series_asset_ids = Column(JSONB, nullable=True)

    wb_publish_error = Column(Text, nullable=True)
    wb_response_json = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status in ('draft', 'in_progress', 'error', 'ready_to_publish', 'published')",
            name="ck_product_generation_jobs_status",
        ),
    )
