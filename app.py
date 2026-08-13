#!/usr/bin/env python3
"""
ytgrab web - a small self-hosted web front end for downloading YouTube
audio/video, built on yt-dlp. Meant to be shared with people you know
(family, friends), not opened up to the public internet.

Run:
    python app.py

Set an access password before sharing beyond your own machine:
    export YTGRAB_PASSWORD="something-only-you-share"   (Windows: set YTGRAB_PASSWORD=...)
    python app.py
"""

import os
import time
import uuid
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import (
    Flask, render_template, request, jsonify, session,
    redirect, url_for, send_file, abort,
)

import yt_dlp

BASE_DIR = Path(__file__).parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)

ACCESS_PASSWORD = os.environ.get("YTGRAB_PASSWORD")           # None = no login required
JOB_MAX_AGE_SECONDS = 2 * 60 * 60                              # clean up after 2 hours
MAX_CONCURRENT_DOWNLOADS = 3

# Optional: path to a Netscape-format cookies file, used to authenticate
# yt-dlp's requests as a real browser session. Needed on most cloud hosts,
# since YouTube blocks datacenter IPs with a "Sign in to confirm you're not
# a bot" error otherwise. On Render this is a Secret File, which lands at
# /etc/secrets/<filename> at runtime — see README.md for setup.
#
# yt-dlp writes rotated cookies *back* to this file after each use, but
# Render's Secret Files are mounted read-only — so we copy the source into
# a writable runtime path once at startup and point yt-dlp at the copy
# instead of the read-only original.
_COOKIES_SOURCE = os.environ.get("YTGRAB_COOKIES_FILE", "/etc/secrets/cookies.txt")
RUNTIME_DIR = BASE_DIR / "runtime"
RUNTIME_DIR.mkdir(exist_ok=True)
COOKIES_FILE = None
if os.path.exists(_COOKIES_SOURCE):
    COOKIES_FILE = str(RUNTIME_DIR / "cookies.txt")
    shutil.copyfile(_COOKIES_SOURCE, COOKIES_FILE)

app = Flask(__name__)
app.secret_key = os.environ.get("YTGRAB_SECRET_KEY", uuid.uuid4().hex)

executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_DOWNLOADS)
JOBS = {}
JOBS_LOCK = threading.Lock()

JS_RUNTIME = "node" if shutil.which("node") else ("deno" if shutil.which("deno") else None)

# These run on import, so they show up in logs whether the app is started
# with `python app.py` (dev) or `gunicorn ... app:app` (production/Render) —
# a check tucked inside `if __name__ == "__main__"` only fires for the
# former and silently never runs under gunicorn.
if not shutil.which("ffmpeg"):
    print("Warning: ffmpeg not found on PATH — audio extraction and merging will fail.")
if not ACCESS_PASSWORD:
    print("Warning: YTGRAB_PASSWORD is not set — the site has no login. "
          "Set it before exposing this beyond localhost.")
if not COOKIES_FILE:
    print(f"Note: no cookies file found at {_COOKIES_SOURCE} — downloads may fail with "
          f"YouTube's bot-detection error on cloud hosts. See README.md.")
if not JS_RUNTIME:
    print("Warning: no JS runtime (node/deno) found on PATH — YouTube downloads will "
          "likely fail with 'Sign in to confirm you're not a bot' or "
          "'The page needs to be reloaded', regardless of cookies. See README.md.")


# --------------------------------------------------------------------------
# Auth (simple shared-password gate; skipped entirely if no password is set)
# --------------------------------------------------------------------------

def login_required(view):
    def wrapped(*args, **kwargs):
        if ACCESS_PASSWORD and not session.get("authed"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    wrapped.__name__ = view.__name__
    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    if not ACCESS_PASSWORD:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        if request.form.get("password") == ACCESS_PASSWORD:
            session["authed"] = True
            return redirect(request.args.get("next") or url_for("index"))
        error = "Wrong password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.pop("authed", None)
    return redirect(url_for("login"))


# --------------------------------------------------------------------------
# Job helpers
# --------------------------------------------------------------------------

def new_job(url, audio_only, audio_format, quality):
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "url": url,
            "status": "queued",
            "percent": 0.0,
            "speed": "",
            "eta": "",
            "title": "",
            "filename": None,
            "error": None,
            "created": time.time(),
        }
    executor.submit(run_job, job_id, url, audio_only, audio_format, quality)
    return job_id


def update_job(job_id, **fields):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(fields)


def make_progress_hook(job_id):
    def hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            percent = (downloaded / total * 100) if total else 0.0
            update_job(
                job_id,
                status="downloading",
                percent=round(percent, 1),
                speed=(d.get("_speed_str") or "").strip(),
                eta=(d.get("_eta_str") or "").strip(),
            )
        elif d["status"] == "finished":
            update_job(job_id, status="processing", percent=100.0)
    return hook


def run_job(job_id, url, audio_only, audio_format, quality):
    outtmpl = str(DOWNLOAD_DIR / f"{job_id}.%(ext)s")
    opts = {
        "outtmpl": outtmpl,
        "progress_hooks": [make_progress_hook(job_id)],
        "noplaylist": True,
        "ignoreerrors": False,
        "quiet": True,
        "no_warnings": True,
        "sleep_interval_requests": 1,  # small pause between internal requests -
                                        # rapid-fire requests are one of the
                                        # things that gets an IP flagged faster
        # YouTube is currently experimenting with forcing "SABR" streaming on
        # some player clients (web_safari in particular), which requires a
        # Proof-of-Origin token yt-dlp can't generate on its own — without
        # one, YouTube strips out every real video/audio format and only
        # thumbnail images are left, causing "Requested format is not
        # available" even with a fully open selector. Explicitly trying
        # android alongside the default web client currently sidesteps this
        # for most videos. Which clients are affected shifts over time, so
        # this may need revisiting later.
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }

    if audio_only:
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": audio_format,
            "preferredquality": "192",
        }]
    else:
        if quality == "best":
            opts["format"] = "bestvideo+bestaudio/best"
        else:
            height = quality.rstrip("pP")
            # Trailing /best (no height cap) means: if this video simply
            # doesn't have anything at or under the requested resolution,
            # fall back to whatever's available instead of failing outright.
            opts["format"] = (
                f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"
            )
        opts["merge_output_format"] = "mp4"

    if COOKIES_FILE:
        opts["cookiefile"] = COOKIES_FILE

    if JS_RUNTIME:
        opts["js_runtimes"] = {JS_RUNTIME: {}}

    last_exc = None
    for attempt in range(2):
        try:
            update_job(job_id, status="downloading" if attempt == 0 else "retrying")
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                ext = audio_format if audio_only else "mp4"
                filename = f"{job_id}.{ext}"
                if not (DOWNLOAD_DIR / filename).exists():
                    # Fall back to whatever yt-dlp actually produced
                    matches = list(DOWNLOAD_DIR.glob(f"{job_id}.*"))
                    if matches:
                        filename = matches[0].name
                update_job(
                    job_id,
                    status="done",
                    title=info.get("title", "download"),
                    filename=filename,
                )
                return
        except Exception as exc:
            last_exc = exc
            if attempt == 0:
                time.sleep(3)  # short pause before the one retry

    update_job(job_id, status="error", error=str(last_exc))


def cleanup_loop():
    while True:
        cutoff = time.time() - JOB_MAX_AGE_SECONDS
        with JOBS_LOCK:
            stale = [jid for jid, j in JOBS.items() if j["created"] < cutoff]
            for jid in stale:
                job = JOBS.pop(jid)
                if job.get("filename"):
                    path = DOWNLOAD_DIR / job["filename"]
                    if path.exists():
                        path.unlink(missing_ok=True)
        time.sleep(600)


threading.Thread(target=cleanup_loop, daemon=True).start()


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@app.route("/")
@login_required
def index():
    return render_template("index.html", has_password=bool(ACCESS_PASSWORD))


@app.route("/api/jobs", methods=["POST"])
@login_required
def create_job():
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "A URL is required."}), 400

    audio_only = bool(data.get("audio_only", True))
    audio_format = data.get("audio_format", "mp3")
    quality = data.get("quality", "best")

    job_id = new_job(url, audio_only, audio_format, quality)
    return jsonify({"job_id": job_id})


@app.route("/api/jobs/<job_id>")
@login_required
def job_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        abort(404)
    return jsonify(job)


@app.route("/api/jobs/<job_id>/file")
@login_required
def job_file(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or job["status"] != "done" or not job["filename"]:
        abort(404)
    path = DOWNLOAD_DIR / job["filename"]
    if not path.exists():
        abort(404)
    download_name = f"{job['title'] or 'download'}{path.suffix}"
    return send_file(path, as_attachment=True, download_name=download_name)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), threaded=True)
