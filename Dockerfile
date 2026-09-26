FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN pip install --no-cache-dir poetry==2.2.1
COPY pyproject.toml poetry.lock* README.md ./
RUN poetry install --only main --no-root --no-interaction --no-ansi

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

RUN groupadd --system bot && useradd --system --gid bot --home-dir /app bot
USER bot

CMD ["python", "-m", "app.bot.main"]
