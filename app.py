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
        # The 'tv' client is highly resistant to bot-blocking and format hiding
        'extractor_args': {
            'youtube': {
                'player_client': ['tv', 'web']
            }
        },
    }

    if os.path.exists(cookie_path):
        ydl_opts['cookiefile'] = cookie_path
    else:
        print("WARNING: cookies.txt not found. YouTube may block datacenter IPs.")

    if is_audio:
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': fmt if fmt in ['mp3', 'm4a', 'wav'] else 'mp3',
                'preferredquality': '192',
            }],
        })
    else:
        ydl_opts.update({
            # Bulletproof fallback for video formats
            'format': 'bestvideo+bestaudio/best',
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