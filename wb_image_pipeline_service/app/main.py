import logging

from fastapi import FastAPI

from app.config import settings

logger = logging.getLogger(__name__)

app = FastAPI(title="WB Image Pipeline Service", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "wb_image_pipeline_service"}


@app.on_event("startup")
def _log_config() -> None:
    logger.info(
        "wb_image_pipeline_service starting env=%s models structure=%s prompt_pack=%s image=%s",
        settings.env,
        settings.openai_model_structure,
        settings.openai_model_prompt_pack,
        settings.openai_image_model,
    )
