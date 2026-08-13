FROM python:3.12-slim

# ffmpeg: audio/video merging and extraction (system binary, not a Python
# package — Render's native Python runtime doesn't include it).
# nodejs: yt-dlp has required an external JS runtime to solve YouTube's
# anti-bot "JS Challenge" system since yt-dlp 2025.11.12 — without one,
# downloads fail with errors like "Sign in to confirm you're not a bot" or
# "The page needs to be reloaded" even with valid cookies.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render sets $PORT at runtime; default to 10000 for local `docker run` testing.
ENV PORT=10000
EXPOSE 10000

# Single worker: job status is stored in memory (see app.py), so multiple
# worker processes would each have their own copy and disagree.
CMD ["sh", "-c", "gunicorn -w 1 -b 0.0.0.0:${PORT:-10000} app:app"]
