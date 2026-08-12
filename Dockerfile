FROM python:3.12-slim

# ffmpeg is a system binary, not a Python package — Render's native Python
# runtime doesn't include it, so we install it at the OS level here.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
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
