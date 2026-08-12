import os
import sys
import logging
from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, url_for, session
import yt_dlp

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "ytgrab-secret-key-change-in-prod")

DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Helper function to get ytdl options
def get_ydl_opts(download_format="mp3", is_audio_only=True):
    # Path to cookies.txt if uploaded/present
    cookie_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
    
    opts = {
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        # Bypasses YouTube's "The page needs to be reloaded" bot error:
        'extractor_args': {
            'youtube': {
                'player_client': ['mweb', 'ios', 'android', 'web_safari']
            }
        },
    }

    if os.path.exists(cookie_path):
        opts['cookiefile'] = cookie_path

    if is_audio_only:
        opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': download_format if download_format in ['mp3', 'm4a', 'wav'] else 'mp3',
                'preferredquality': '192',
            }],
        })
    else:
        opts.update({
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        })

    return opts

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

@app.route("/download", methods=["POST"])
def download():
    data = request.get_json() or {}
    url = data.get("url")
    mode = data.get("mode", "music")  # 'music' or 'video'
    fmt = data.get("format", "mp3")

    if not url:
        return jsonify({"status": "error", "message": "No URL provided"}), 400

    is_audio = (mode == "music")
    opts = get_ydl_opts(download_format=fmt, is_audio_only=is_audio)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            # Adjust extension for audio post-processing
            if is_audio:
                base, _ = os.path.splitext(filename)
                filename = f"{base}.{fmt}"

            download_filename = os.path.basename(filename)

        return jsonify({
            "status": "success",
            "title": info.get("title", "Unknown Title"),
            "file": download_filename,
            "id": info.get("id")
        })

    except Exception as e:
        error_msg = str(e)
        return jsonify({
            "status": "error",
            "message": error_msg
        }), 500

@app.route("/files/<path:filename>")
def get_file(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)