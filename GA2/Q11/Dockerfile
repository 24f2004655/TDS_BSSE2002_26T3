FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Whole context rather than one COPY per file: a named COPY of a file that is not
# committed fails the build (this broke a Q5 deploy).
COPY . .

# Render injects PORT for docker services; fall back to 8000 locally.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
