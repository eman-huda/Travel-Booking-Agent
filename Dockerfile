# AI Travel Agent Testbed: one image, used by both the API and the UI services.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app ./app
COPY data ./data
COPY frontend ./frontend
COPY scripts ./scripts
COPY tests ./tests
COPY .streamlit ./.streamlit
COPY pytest.ini README.md ./

# var/ holds the SQLite database and logs (mounted as a volume in compose so runs survive restarts).
RUN mkdir -p /app/var/logs

EXPOSE 8000 8501

# Secrets are never baked in. Provide them at runtime through .env / environment variables.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
