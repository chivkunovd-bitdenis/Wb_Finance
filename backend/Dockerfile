FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright browsers + system deps (needed for ai_competitor_report_fetch_playwright on server).
# This keeps deployments deterministic: no runtime downloads, no missing shared libs.
RUN python -m playwright install-deps chromium && python -m playwright install chromium

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
