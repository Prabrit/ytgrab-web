import os
import uuid
import threading
from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, url_for, session
import yt_dlp

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "ytgrab-secret-key-change-in-prod")

DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

JOBS = {}

def update_progress(d, job_id):
    if job_id not in JOBS:
        return
    
    if d.get('status') == 'downloading':
        total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
        downloaded = d.get('downloaded_bytes', 0)
        if total > 0:
            JOBS[job_id]['progress'] = round((downloaded / total) * 100, 1)
        else:
            p_str = d.get('_percent_str', '0%').strip().replace('%', '')
            try:
                JOBS[job_id]['progress'] = float(p_str)
            except ValueError:
                pass
    elif d.get('status') == 'finished':
        JOBS[job_id]['progress'] = 100.0
        JOBS[job_id]['status'] = 'processing'


def run_download_job(job_id, url, mode, fmt):
    JOBS[job_id]['status'] = 'downloading'
    is_audio = (mode == "music")
    cookie_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")

    ydl_opts = {
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'progress_hooks': [lambda d: update_progress(d, job_id)],
        'extractor_args': {
            'youtube': {
                'player_client': ['mweb', 'android', 'ios', 'web']
            }
        },
    }

    if os.path.exists(cookie_path):
        ydl_opts['cookiefile'] = cookie_path

    if is_audio:
        ydl_opts.update({
            'format': 'ba/b',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': fmt if fmt in ['mp3', 'm4a', 'wav'] else 'mp3',
                'preferredquality': '192',
            }],
        })
    else:
        ydl_opts.update({
            # Uses flexible matching for video+audio or pre-merged single streams
            'format': 'bv*+ba/b',
            'merge_output_format': 'mp4',
        })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)

            base, _ = os.path.splitext(filename)
            if is_audio:
                filename = f"{base}.{fmt}"
            else:
                filename = f"{base}.mp4"

            JOBS[job_id]['title'] = info.get("title", "Unknown Title")
            JOBS[job_id]['filename'] = os.path.basename(filename)
            JOBS[job_id]['status'] = 'completed'
            JOBS[job_id]['progress'] = 100.0

    except Exception as e:
        JOBS[job_id]['status'] = 'failed'
        JOBS[job_id]['error'] = str(e)


@app.route("/")
def index():
    return render_template("index.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        session["user"] = "authenticated"
        return redirect(url_for("index"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))


@app.route("/api/jobs", methods=["GET", "POST"])
def api_jobs():
    if request.method == "POST":
        data = request.get_json() or {}
        url = data.get("url")
        mode = data.get("mode", "music")
        fmt = data.get("format", "mp3")

        if not url:
            return jsonify({"error": "No URL provided"}), 400

        job_id = str(uuid.uuid4())
        JOBS[job_id] = {
            "id": job_id,
            "url": url,
            "mode": mode,
            "format": fmt,
            "status": "pending",
            "progress": 0.0,
            "title": "Fetching metadata...",
            "filename": None,
            "error": None
        }

        thread = threading.Thread(target=run_download_job, args=(job_id, url, mode, fmt))
        thread.daemon = True
        thread.start()

        return jsonify(JOBS[job_id]), 201

    return jsonify(list(JOBS.values()))

@app.route("/api/jobs/<job_id>", methods=["GET", "DELETE"])
def api_job_detail(job_id):
    if job_id not in JOBS:
        return jsonify({"error": "Job not found"}), 404

    if request.method == "DELETE":
        del JOBS[job_id]
        return jsonify({"success": True})

    return jsonify(JOBS[job_id])

@app.route("/downloads/<path:filename>")
def download_file(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)