FROM python:3.12-slim

# ffmpeg: audio/video merging and extraction (system binary, not a Python
# package — Render's native Python runtime doesn't include it).
# nodejs: yt-dlp has required an external JS runtime to solve YouTube's
# anti-bot "JS Challenge" system since yt-dlp 2025.11.12 — without one,
# downloads fail with errors like "Sign in to confirm you're not a bot" or
# "The page needs to be reloaded" even with valid cookies.
# wget: used below to fetch the PO token provider (not present in the slim
# base image).
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg nodejs wget && \
    rm -rf /var/lib/apt/lists/*

# PO token provider: YouTube is increasingly requiring a Proof-of-Origin
# token before it'll return real (non-placeholder) video/audio formats —
# without one, downloads fail with "Requested format is not available"
# even though extraction otherwise succeeds. This runs as a small local
# HTTP server (127.0.0.1:4416) that yt-dlp queries for tokens; see
# start.sh for how it's launched alongside gunicorn, and README.md for
# background. Binary + matching yt-dlp plugin come from the same release
# so their versions always line up.
RUN wget -q https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs/releases/latest/download/bgutil-pot-linux-x86_64 \
        -O /usr/local/bin/bgutil-pot && \
    chmod +x /usr/local/bin/bgutil-pot && \
    wget -q https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs/releases/latest/download/bgutil-ytdlp-pot-provider-rs.zip \
        -O /tmp/pot-plugin.zip && \
    python3 -c "import zipfile, site; zipfile.ZipFile('/tmp/pot-plugin.zip').extractall(site.getsitepackages()[0])" && \
    rm /tmp/pot-plugin.zip

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Build-time smoke test: fail loudly here if the PO token plugin extracted
# above isn't actually where yt-dlp will look for it, rather than silently
# shipping an image where every YouTube download fails with "Requested
# format is not available" and no clue why. This mirrors the check the
# plugin's own README recommends running with `yt-dlp -v`, just done once
# at build time instead of on every request.
RUN python3 -c "import yt_dlp_plugins.extractor.getpot_bgutil_http" && \
    echo "PO token plugin import: OK"

COPY . .
RUN chmod +x start.sh

# Render sets $PORT at runtime; default to 10000 for local `docker run` testing.
ENV PORT=10000
EXPOSE 10000

# start.sh launches the PO token provider in the background, then execs
# gunicorn in the foreground. Single gunicorn worker: job status is stored
# in memory (see app.py), so multiple worker processes would each have
# their own copy and disagree.
CMD ["./start.sh"]
