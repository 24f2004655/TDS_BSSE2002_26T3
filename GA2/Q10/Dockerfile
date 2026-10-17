FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the whole context rather than naming each config layer: COPY of a path
# that does not exist fails the build, and .env is an optional layer (main.py
# carries an embedded fallback). A named COPY .env . broke a deploy once because
# the file had not been committed.
COPY . .

# Render injects PORT for docker services; fall back to 8000 locally.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
