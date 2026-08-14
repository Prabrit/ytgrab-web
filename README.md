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

You also need a **JS runtime** (Node.js or Deno) for YouTube downloads to
work reliably — see "YouTube errors" below for why. Easiest locally:
- Windows/macOS: install [Node.js](https://nodejs.org)
- Linux: `sudo apt install nodejs`

(The Docker image used for the Render deploy already includes this —
nothing extra to do there.)

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

## YouTube errors: "Sign in to confirm you're not a bot" / "The page needs to be reloaded"

Both of these come from the same underlying cause: since November 2025,
yt-dlp needs an external JavaScript runtime (Node.js or Deno) installed
alongside it to solve YouTube's anti-bot JS challenges. Without one, these
errors show up **regardless of cookies** — a JS runtime is the actual fix,
not an optional extra.

**This repo's `Dockerfile` already installs Node.js**, so a fresh deploy on
Render has this covered. If you still see these errors after redeploying:

1. Check Render's Logs tab right after a restart (look for lines starting
   `Starting gunicorn`). If you see `Warning: no JS runtime (node/deno)
   found on PATH`, the image didn't pick up the Node.js install — trigger
   **Manual Deploy → Clear build cache & deploy** on Render so it rebuilds
   from scratch instead of reusing a cached layer from before this was
   added.
2. If that warning isn't there, Node.js is working and yt-dlp is genuinely
   using it — in which case what you're hitting is YouTube occasionally
   still challenging the request. `app.py` already retries each download
   once automatically before giving up, since this class of failure is
   often intermittent.

**Cookies help too, but as a second layer, not the primary fix.** YouTube
also treats requests from shared cloud IP ranges (Render, Railway, etc.)
with more suspicion than a home IP, even with a working JS runtime. If
errors persist after confirming Node.js is active:

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
   the file lands at `/etc/secrets/cookies.txt`. `app.py` copies it into a
   writable `runtime/cookies.txt` at startup and uses that copy — Render
   mounts Secret Files **read-only**, and yt-dlp needs to write rotated
   cookies back to the file it reads from, so pointing it at the read-only
   original directly fails with `Read-only file system`.
5. Running locally instead? Set `YTGRAB_COOKIES_FILE=/path/to/cookies.txt`
   before starting the app — it'll get copied the same way.

Cookies can go stale after a few weeks — if bot-detection errors come back
later, re-export a fresh `cookies.txt` and update the Secret File. Keeping
`yt-dlp[default]` unpinned to an old version (see `requirements.txt`) also
matters — YouTube and yt-dlp are in an ongoing back-and-forth, and point
releases regularly fix breakage like this.

**Worth knowing going in:** cookies going stale isn't a one-time bug to fix
— it's an ongoing characteristic of running this on a cloud host. YouTube
invalidates session cookies faster when it sees them used repeatedly from
a datacenter IP than it would from a home connection, so expect to
re-export every so often (anywhere from weeks to, on a heavily-used
instance, days) rather than treating any single fix here as permanent.
`app.py` adds a small delay between requests to reduce how often this gets
triggered, but it doesn't eliminate the need for fresh cookies periodically.

When you re-export, two things matter for how long the cookies last:
- Export from a **private/incognito window you then just close** —
  never click "Log out" in that browser afterward, since logging out
  invalidates the session server-side and kills every cookie exported
  from it, immediately.
- If yt-dlp complains the file doesn't look like a Netscape-format cookies
  file, that's usually the export itself going wrong (e.g. JSON saved
  with a `.txt` extension) rather than anything on the app side.

## "Requested format is not available"

This one's unrelated to cookies. YouTube has been experimenting with
forcing "SABR" streaming on some player clients — when it does, and yt-dlp
doesn't have a valid Proof-of-Origin (PO) token, YouTube strips out every
real video/audio format and leaves only thumbnail images. At that point
even a fully open format selector has nothing to select, so it fails.

`app.py` already tells yt-dlp to also try the `android` player client
alongside the default `web` one, and this repo now also runs a local PO
token provider ([bgutil-ytdlp-pot-provider-rs](https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs))
so yt-dlp can generate valid tokens on demand instead of going without.
`start.sh` launches it as a background process on `127.0.0.1:4416`
alongside gunicorn — nothing extra to configure, it's part of the Docker
image now. If Render's Logs tab shows `Note: PO token provider not
reachable` right after startup, something about that process didn't come
up; a **Manual Deploy → Clear build cache & deploy** is the first thing to
try.

Which player clients are affected by SABR-forcing, and how reliable PO
token generation is, both shift over time as YouTube and the yt-dlp
community adjust — if format errors come back after all of this, it's
worth checking whether a newer version of the provider exists (bump the
download URLs in `Dockerfile` to a pinned version if `/latest` ever
regresses) rather than assuming the setup itself is wrong.

## How it works

- `POST /api/jobs` queues a download on a background thread pool (up to 3
  at once) so one person's download doesn't block another's.
- Each download is retried once automatically on failure (with a few
  seconds' pause) before being marked as errored — YouTube's anti-bot
  checks are sometimes intermittent, and a retry alone resolves some of
  them.
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
