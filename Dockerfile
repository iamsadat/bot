# JobHunt dashboard — production container.
# Build:  docker build -t jobhunt .
# Run:    docker run -p 8000:8000 jobhunt
# Then open http://localhost:8000
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

# Install runtime deps first (better layer caching)
COPY pyproject.toml requirements.txt ./
RUN pip install --upgrade pip && \
    pip install fastapi "uvicorn[standard]" sqlalchemy alembic

# App source
COPY . .
RUN pip install -e .

EXPOSE 8000

# Bind to $PORT so this works on Render / Fly / Railway / Cloud Run unchanged.
CMD ["sh", "-c", "python -m uvicorn jobhunt.dashboard.app:app --host 0.0.0.0 --port ${PORT}"]
