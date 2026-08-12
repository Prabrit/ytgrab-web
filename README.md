# ytgrab web

A small self-hosted website for pulling audio/video from YouTube, built on
[`yt-dlp`](https://github.com/yt-dlp/yt-dlp) with a Flask backend. Designed
to be shared with people you know via a password, not opened up as a public
service.

## Setup

```bash
pip install -r requirements.txt
```

You need **ffmpeg** on the machine running the server:
- Windows: `winget install ffmpeg`
- macOS: `brew install ffmpeg`
- Linux: `sudo apt install ffmpeg`

## Run it

```bash
export YTGRAB_PASSWORD="pick-something-only-you-share"   # Windows: set YTGRAB_PASSWORD=...
python app.py
```

Visit `http://localhost:5000`. If `YTGRAB_PASSWORD` isn't set, the site has
no login at all — fine for testing on your own machine, but set it before
anyone else can reach the server.

## Letting people on a different network use it

Three common options, roughly easiest-to-set-up first:

**1. A tunnel (no router configuration needed)**
Tools like [ngrok](https://ngrok.com), Cloudflare Tunnel, or Tailscale Funnel
expose your local server through a public URL without touching your router.
Good for casual/occasional sharing.
```bash
ngrok http 5000
```
Share the `https://…ngrok...` URL it gives you, plus your password.

**2. Port forwarding on your router**
Forward an external port to port 5000 on the machine running the app, then
share your public IP (or a dynamic DNS hostname if your ISP doesn't give you
a static IP). More permanent, but exposes your home network directly — make
sure the password is set and keep the machine's OS/software updated.

**3. Deploy to Render (free, always-on within the limits below)**

This repo already includes what you need: a `Dockerfile` (installs ffmpeg,
which Render's native Python runtime doesn't have) and a `render.yaml`
Blueprint.

1. **Push this folder to a GitHub repo.**
   ```bash
   cd ytgrab-web
   git init
   git add .
   git commit -m "ytgrab web"
   ```
   Create an empty repo on GitHub (github.com → New repository), then:
   ```bash
   git remote add origin https://github.com/<your-username>/<repo-name>.git
   git branch -M main
   git push -u origin main
   ```

2. **Sign up at [render.com](https://render.com)** — GitHub login is
   fastest, no credit card needed for the free tier.

3. **New → Blueprint**, connect your GitHub account, and select the repo.
   Render reads `render.yaml` and shows you the service it's about to
   create. You'll be prompted to enter `YTGRAB_PASSWORD` right there (it
   generates `YTGRAB_SECRET_KEY` for you automatically). Click **Deploy
   Blueprint**.

   *(No `render.yaml`, or prefer clicking through manually? New → Web
   Service → connect the repo → Render auto-detects the Dockerfile and
   sets Environment to Docker → pick the Free plan → add `YTGRAB_PASSWORD`
   and `YTGRAB_SECRET_KEY` under the Environment tab yourself.)*

4. Wait for the build (a few minutes the first time) — you'll get a URL
   like `https://ytgrab-web.onrender.com`. That's what you share.

**Free tier limits worth knowing:** 750 instance-hours/month (enough for
one service running continuously), and the service **spins down after 15
minutes with no traffic**, taking ~30–60 seconds to wake back up on the
next request. That's fine for casual/family use; if that cold start is
annoying, Render's paid Starter tier keeps it always warm.

**Railway instead?** Same idea, but Railway's free usage is a small
monthly credit rather than an indefinite free tier — check current pricing
before relying on it for always-on hosting. If you go that route, add a
`nixpacks.toml` with:
```toml
[phases.setup]
nixPkgs = ['ffmpeg']
```
so Railway's builder installs ffmpeg (or reuse the same `Dockerfile`,
which Railway also supports).

## "Sign in to confirm you're not a bot"

If downloads fail with this error, it's YouTube blocking requests from
your host's server IP, not a bug — very common on Render, Railway, and
similar platforms, since they use shared IP ranges YouTube treats as
automated traffic. The fix is to give yt-dlp cookies from a real logged-in
session so its requests look like a browser instead:

1. **Use a secondary Google account for this**, not your main one — yt-dlp's
   own docs note that automated use like this risks the account getting
   flagged or temporarily locked.
2. Log into YouTube with that account in a **private/incognito window**
   (regular tabs rotate cookies frequently, which breaks this).
3. Install the **"Get cookies.txt LOCALLY"** browser extension (check
   you've got that exact one — an older extension with a similar name was
   pulled from the Chrome Web Store over data-exfiltration concerns), and
   export a `cookies.txt` file from youtube.com.
4. **Don't commit that file to git.** On Render: go to your service →
   Environment → Secret Files → Add Secret File → name it `cookies.txt` →
   paste in the file's contents → Save. Render redeploys automatically and
   the file lands at `/etc/secrets/cookies.txt`, which `app.py` already
   checks for and uses automatically if present.
5. Running locally instead? Set `YTGRAB_COOKIES_FILE=/path/to/cookies.txt`
   before starting the app.

Cookies can go stale after a few weeks — if the bot-detection error comes
back later, re-export a fresh `cookies.txt` and update the Secret File.

## How it works

- `POST /api/jobs` queues a download on a background thread pool (up to 3
  at once) so one person's download doesn't block another's.
- The browser polls `GET /api/jobs/<id>` once a second for progress and
  swaps in a download link when the job finishes.
- Finished files and their job records are deleted automatically after 2
  hours (`JOB_MAX_AGE_SECONDS` in `app.py`) so disk usage doesn't grow
  unbounded.
- Job state lives in memory — restarting the server clears in-progress
  jobs. That's a deliberate simplicity trade-off for a small personal tool;
  swap in Redis or a database if you need it to survive restarts.

## Before you share this with others

Downloading music you don't have the rights to, at scale, for other people,
is the kind of thing that's gotten similar sites (youtube-mp3.org, FLVTO,
2Conv) sued by record labels and shut down. Keeping this behind a password
and sharing it only with people you know — rather than posting the link
publicly — is both the safer and the more common way this kind of
self-hosted tool is actually used.
