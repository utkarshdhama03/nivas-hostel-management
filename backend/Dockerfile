# Backend image: used by the api, worker and tools services.
# The virtual environment lives at /opt/venv (outside /app) so that bind-mounting
# the source code over /app during development doesn't hide the installed packages.
FROM python:3.13.18-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/opt/venv/bin:$PATH"

# WeasyPrint (PDF receipts, Q75) needs Pango for text layout and a font. Debian packages,
# used unmodified (LGPL/open fonts, 07 §4.1).
RUN apt-get update  && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core  && rm -rf /var/lib/apt/lists/*

# uv from PyPI (not from GitHub's container registry, per Q6/Q73)
RUN pip install --no-cache-dir uv==0.13.7

WORKDIR /app

# Dependencies first (cached layer), then the code.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --all-groups

COPY . .
