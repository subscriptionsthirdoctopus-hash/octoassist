# OctoAssist server image
# Python 3.12-slim. Used by the docker-compose stack at deploy/docker/.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/octoassist

# psycopg[binary] bundles libpq, so no apt deps are needed.
COPY pyproject.toml ./
COPY app ./app

RUN pip install --upgrade pip wheel && pip install .

# Drop privileges
RUN useradd --system --no-create-home --shell /usr/sbin/nologin octoassist \
    && chown -R octoassist:octoassist /srv/octoassist
USER octoassist

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
