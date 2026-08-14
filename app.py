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

# PO token provider (see start.sh / Dockerfile) — a local HTTP server that
# gives yt-dlp Proof-of-Origin tokens, which YouTube increasingly requires
# before returning real (non-placeholder) formats. A few retries here
# because start.sh launches it just before this module gets imported, so
# it may need a moment to come up.
POT_PROVIDER_URL = "http://127.0.0.1:4416"


def _pot_provider_reachable():
    import urllib.request
    for _ in range(5):
        try:
            urllib.request.urlopen(f"{POT_PROVIDER_URL}/ping", timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False


POT_PROVIDER_UP = _pot_provider_reachable()

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
if not POT_PROVIDER_UP:
    print(f"Note: PO token provider not reachable at {POT_PROVIDER_URL} — downloads "
          f"may fail with 'Requested format is not available' on some videos. "
          f"See README.md.")


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


class _CaptureLogger:
    """Swallows yt-dlp's verbose/debug output instead of printing it, so we
    can surface the useful parts — in particular the
    '[debug] [youtube] [pot] PO Token Providers: ...' line — only when a
    job actually fails. That one line is the difference between "the PO
    token plugin never loaded" and "it loaded fine, something else is
    wrong", which otherwise you'd only find by re-running yt-dlp by hand
    with -v. See https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs#verification
    """
    def __init__(self):
        self.lines = []

    def debug(self, msg):
        self.lines.append(msg)

    def info(self, msg):
        self.lines.append(msg)

    def warning(self, msg):
        self.lines.append(f"WARNING: {msg}")

    def error(self, msg):
        self.lines.append(f"ERROR: {msg}")


def build_ydl_opts(job_id, audio_only, audio_format, quality, legacy_pot=False):
    outtmpl = str(DOWNLOAD_DIR / f"{job_id}.%(ext)s")
    logger = _CaptureLogger()

    # YouTube is currently forcing "SABR" streaming on some player clients
    # (web in particular), which requires a Proof-of-Origin token yt-dlp
    # can't generate on its own — without one, YouTube strips out every
    # real video/audio format and only thumbnail images are left, causing
    # "Requested format is not available" even with a fully open selector.
    #
    # Rather than pinning a hand-picked client list (which breaks whenever
    # YouTube changes which clients are SABR-gated — as happened here:
    # "android" + "web" stopped being enough), we use yt-dlp's own
    # actively-maintained "default" client set and add "mweb" alongside
    # it, since that's the client yt-dlp's own PO Token Guide recommends
    # pairing with a PO token provider for GVS requests:
    # https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide
    bgutil_args = {"base_url": [POT_PROVIDER_URL]}
    if legacy_pot:
        # Documented fallback when tokens from the provider stop being
        # accepted: https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs
        bgutil_args["disable_innertube"] = ["1"]

    opts = {
        "outtmpl": outtmpl,
        "progress_hooks": [make_progress_hook(job_id)],
        "noplaylist": True,
        "ignoreerrors": False,
        "quiet": True,
        "no_warnings": True,
        "verbose": True,       # captured by `logger` below, not printed —
        "logger": logger,      # this is what lets us see the PO Token
                                # Providers line when something goes wrong.
        "sleep_interval_requests": 1,  # small pause between internal requests -
                                        # rapid-fire requests are one of the
                                        # things that gets an IP flagged faster
        "extractor_args": {
            "youtube": {"player_client": ["default", "mweb"]},
            "youtubepot-bgutilhttp": bgutil_args,
        },
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

    return opts, logger


def run_job(job_id, url, audio_only, audio_format, quality):
    last_exc = None
    last_logger = None
    for attempt in range(2):
        # If the first attempt failed specifically with "format not
        # available", the PO token from the provider is most likely being
        # rejected — retry once in the provider's documented legacy mode
        # instead of just repeating the exact same request.
        legacy_pot = attempt == 1 and last_exc is not None and (
            "Requested format is not available" in str(last_exc)
        )
        opts, logger = build_ydl_opts(job_id, audio_only, audio_format, quality, legacy_pot)
        last_logger = logger
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

    # Pull out the handful of lines from the verbose log that actually
    # explain a format failure — everything else in there is noise for
    # this purpose. Two independent systems can each cause "Requested
    # format is not available" on their own: the PO token provider (its
    # status alone doesn't prove tokens are being *accepted*) and the
    # separate signature/n-challenge JS solver — a working PO token
    # provider says nothing about whether that solver is also working.
    # Printed to stdout (so it lands in Render's Logs tab) only on
    # failure — successful jobs never dump this.
    KEY_MARKERS = (
        "PO Token Providers",
        "SABR",
        "challenge solving failed",
        "Signature solving failed",
        "nsig extraction failed",
        "No supported JavaScript runtime",
        "skipped as they are missing a url",
        "skipped as they are missing a URL",
    )
    key_lines = []
    for line in (last_logger.lines if last_logger else []):
        if any(marker in line for marker in KEY_MARKERS) and line.strip() not in key_lines:
            key_lines.append(line.strip())

    print(f"[job {job_id}] failed: {last_exc}")
    if last_logger and last_logger.lines:
        print(f"[job {job_id}] yt-dlp -v output follows:")
        print("\n".join(last_logger.lines))

    pot_line = next((l for l in key_lines if "PO Token Providers" in l), None)
    js_challenge_failed = any(
        ("challenge solving failed" in l) or ("nsig extraction failed" in l)
        for l in key_lines
    )
    no_js_runtime = any("No supported JavaScript runtime" in l for l in key_lines)

    error_msg = str(last_exc)
    if "Requested format is not available" in error_msg:
        if no_js_runtime:
            error_msg += (
                " — yt-dlp reports no usable JavaScript runtime at request "
                "time, even though this should be configured. That points "
                "at the js_runtimes setting not taking effect, or "
                f"node not actually being on PATH inside the container "
                "(JS_RUNTIME was "
                f"{'detected as ' + repr(JS_RUNTIME) if JS_RUNTIME else 'NOT detected'} "
                "at startup)."
            )
        elif js_challenge_failed:
            error_msg += (
                " — the signature/n-challenge JS solver failed "
                "independently of the PO token provider (both can break "
                "formats on their own). This is usually YouTube having "
                "changed the player JS faster than the installed "
                "yt-dlp/yt-dlp-ejs version accounts for — try redeploying "
                "with 'Clear build cache & deploy' to pick up the latest "
                "yt-dlp[default], which bundles current EJS scripts."
            )
        elif pot_line is None:
            error_msg += (
                " — couldn't confirm the PO token plugin loaded at all "
                "(no 'PO Token Providers' line in yt-dlp's own debug "
                "output). If it's missing entirely, the plugin most "
                "likely isn't being found on PYTHONPATH. See README.md."
            )
        elif "bgutil" not in pot_line.lower() or pot_line.rstrip().endswith(": none"):
            error_msg += (
                f" — yt-dlp reports no working PO token provider ({pot_line}). "
                "The plugin didn't load or the local bgutil-pot server isn't "
                "reachable. See README.md."
            )
        else:
            error_msg += (
                f" — the PO token plugin IS loaded and reporting in "
                f"({pot_line}), and no separate JS-challenge failure showed "
                "up either, so tokens are being generated but YouTube is "
                "still rejecting this request — usually a flagged server "
                "IP rather than a config problem at this point. "
                "See README.md."
            )

    if key_lines:
        error_msg += "\n\nRelevant yt-dlp debug lines:\n" + "\n".join(f"  {l}" for l in key_lines)

    update_job(job_id, status="error", error=error_msg)


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
