# OctoAssist server image
# Python 3.12-slim. Used by the docker-compose stack at deploy/docker/.
# Build context is the repo root (so we can also bundle agent-py/).

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/octoassist

# psycopg[binary] bundles libpq, so no apt deps are needed.
# Dependencies come from the hash-pinned lock (server/requirements.lock), so two
# builds a month apart install byte-identical wheels. Regenerate with
# `pip-compile --generate-hashes --strip-extras -o requirements.lock pyproject.toml`
# (see deploy/droplet/README.md). The app itself is installed without deps.
COPY server/pyproject.toml server/requirements.lock ./
RUN pip install --upgrade pip wheel && pip install --require-hashes -r requirements.lock
COPY server/app ./app
RUN pip install --no-deps .

# Cross-platform Python agent — served at /agent/files/* by views_agent_download.
COPY agent-py /srv/agent_py

# Drop privileges
RUN useradd --system --no-create-home --shell /usr/sbin/nologin octoassist \
    && chown -R octoassist:octoassist /srv/octoassist /srv/agent_py
USER octoassist

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
