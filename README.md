# Sonara

Desktop app (Windows) that renders a still-image video from an audio file + cover
image, then uploads it straight to YouTube — title, description, tags, privacy,
and optional scheduled publish time. Built with Python + PySide6, ffmpeg, and the
YouTube Data API v3.

## 1. One-time Google Cloud setup (required before signing in)

The app needs its own OAuth client so it can ask your Google account for
permission to upload to your channel. Google requires this to be created by
you, in your own Google Cloud project — there's no way around that step.

1. Go to https://console.cloud.google.com/ and create a new project (or pick
   an existing one).
2. **APIs & Services → Library** → search "YouTube Data API v3" → **Enable**.
   Also search "YouTube Analytics API" → **Enable** — only needed for the
   Analytics tab's "Discovered via" column (how viewers found each video);
   everything else works without it.
3. **APIs & Services → OAuth consent screen**:
   - User type: **External** (unless you have a Workspace org).
   - Fill in app name ("Sonara"), your email as support/developer contact.
   - Scopes: you can skip adding scopes here, the app requests them directly.
   - Under **Test users**, add the Google account(s) you'll sign in with —
     while the app is in "Testing" publish status, only test users can log in.
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**:
   - Application type: **Desktop app**.
   - Name it anything, click **Create**, then **Download JSON**.
5. In Sonara: **Settings → Google API Credentials… → Import client_secret.json…**
   and select the file you just downloaded.

Click **Connect Account** in the app — a browser window opens for you to sign
in and grant access, then returns control to the app.

> Note: the "Testing" publish status caps you at 100 test users and Google may
> require re-consent every 7 days for unverified apps. That's fine for
> personal/small-scale use. If you want it usable long-term by other people,
> you'd need to submit the OAuth consent screen for verification.

## 2. Running from source

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python src\main.py
```

Requires `ffmpeg`/`ffprobe` on your `PATH` (already detected on this machine
at `C:\ffmpeg\ffmpeg-master-latest-win64-gpl\bin`).

## 3. Building the .exe

```
.venv\Scripts\pyinstaller build.spec
```

Output lands in `dist\Sonara\Sonara.exe` (or `dist\Sonara.exe` depending on
PyInstaller mode — `build.spec` is currently a single-folder build).

To make the .exe fully self-contained (no separate ffmpeg install required on
the machine you hand it to), copy `ffmpeg.exe` and `ffprobe.exe` into the same
folder as `Sonara.exe` after building — the app checks next to itself first.

## Notes / limitations

- **Custom thumbnails** (`videos.thumbnails.set`) require the uploading
  channel to be phone-verified with Google. If it's not, the app silently
  skips setting the thumbnail — the upload itself still succeeds.
- **Scheduling**: YouTube only supports scheduled publish by uploading the
  video as `private` with a `publishAt` timestamp; YouTube flips it to public
  automatically at that time. The app handles this for you when you check
  "Schedule for later".
- Credentials are cached at `%APPDATA%\Sonara\token.json` so you don't have to
  sign in every run; **Sign Out** deletes that file.
- **Analytics tab**: lists your channel's uploads with thumbnails, view/like/
  comment counts, and engagement rate — click any column header to re-sort
  (e.g. by Views or Engagement to see which titles did best). Private videos
  are hidden by default; check "Show private videos" to reveal them.
  - The **"Discovered via"** column (top traffic source, and top search term
    for videos mainly found via YouTube Search) needs the YouTube Analytics
    API enabled (step 2 above) *and* a fresh sign-in, since it was added
    after the initial OAuth scopes were set. If you set this up before that
    API was enabled, or the column just shows "—": enable the API, then
    **Sign Out** and **Connect Account** again in Sonara to re-grant the new
    permission — Google won't add it to an existing session automatically.
