# ---- builder: alpine with uv to resolve & install deps from pyproject ----
FROM python:alpine AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System deps for compiling wheels commonly needed by kubernetes deps
RUN apk update && apk add --no-cache --no-interactive --purge \
    curl \
    ca-certificates \
 && update-ca-certificates

# Install uv (standalone installer)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:/root/.cargo/bin:${PATH}"

WORKDIR /app

# Copy project manifest and source code for dependency resolution and package installation
COPY . /app

# Create venv and install all deps from pyproject with uv sync
RUN uv venv /opt/venv --clear \
 && . /opt/venv/bin/activate \
 && uv sync --frozen --active \
 && uv pip install -e .

# ---- final runtime image (alpine) ----
FROM python:alpine AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Ensure system trust store exists for HTTPS
RUN apk update && apk add --no-cache --no-interactive --purge \
    ca-certificates \
 && update-ca-certificates

# Copy venv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
# Copy the entire application since package is already installed in builder stage
COPY --from=builder /app ./

EXPOSE 8000

# Run via our CLI entrypoint (installed in venv)
CMD ["carta-sidecar", "--host", "0.0.0.0", "--port", "8000"]
