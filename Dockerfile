FROM python:3.14.4-slim-bookworm@sha256:fc74d22ffd0d5ac395a4b7bdda75a4539758862c49ebf3005647084631e63789

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-por \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir uv==0.12.22
COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY src ./src
COPY scripts ./scripts
COPY demo ./demo
COPY evals ./evals
RUN uv sync --locked --no-dev
ENV PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data/private \
    && chown -R appuser:appuser /app
USER 10001:10001

EXPOSE 8000
CMD ["uvicorn", "knowledgedesk.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
