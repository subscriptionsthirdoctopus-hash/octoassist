# OctoTime server image — mirrors the OctoAssist deployment pattern.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/octotime

COPY pyproject.toml ./
COPY app ./app

RUN pip install --upgrade pip wheel && pip install .

RUN useradd --system --no-create-home --shell /usr/sbin/nologin octotime \
    && chown -R octotime:octotime /srv/octotime
USER octotime

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
