FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# We use the shell form (sh -c) so the container can evaluate Render's dynamic $PORT variable.
# If $PORT is missing (like on your local machine), it defaults to 5000.
CMD sh -c "gunicorn --bind 0.0.0.0:${PORT:-5000} app:app"