# Media Digest

Capture YouTube and Instagram Reels links from iOS, store them in Cloudflare, then process them locally into Obsidian notes.

## Flow

```text
iOS Share Sheet
  -> Apple Shortcut
  -> Cloudflare Worker
  -> local PC sync
  -> yt-dlp + ffmpeg
  -> faster-whisper
  -> 00-Inbox media-capture note
```

## Setup

Install dependencies:

```powershell
npm install
python -m pip install -r requirements-transcribe.txt
```

Create a Cloudflare KV namespace:

```powershell
npx wrangler kv namespace create MEDIA_DIGEST_KV
```

Put the returned namespace id in `wrangler.toml`.

Create `.dev.vars` for local dev:

```text
PAIRING_TOKEN=<long-random-token>
CAPTURE_TOKEN=<long-random-token-for-ios-shortcut>
```

Deploy:

```powershell
npm run deploy
```

Set production secrets:

```powershell
npx wrangler secret put PAIRING_TOKEN
npx wrangler secret put CAPTURE_TOKEN
```

## iOS Shortcut MVP

Use the simpler capture-token endpoint first:

```http
POST https://<worker-url>/api/media/capture-token
Authorization: Bearer <CAPTURE_TOKEN>
Content-Type: application/json
```

Body:

```json
{
  "url": "Shortcut Input",
  "source": "ios-share",
  "captured_at": "Current Date"
}
```

This is easier than HMAC signing inside Shortcuts. The Worker still supports device pairing and signed requests for the local PC sync.

## Pair Local PC

Pair once:

```powershell
$body = @{ name = "PC local" } | ConvertTo-Json
Invoke-RestMethod -Method Post `
  -Uri "https://<worker-url>/api/pair" `
  -Headers @{ Authorization = "Bearer <PAIRING_TOKEN>" } `
  -ContentType "application/json" `
  -Body $body
```

Copy `media-sync.env.example` to `media-sync.env` and fill:

```text
MEDIA_DIGEST_BASE_URL=https://<worker-url>
MEDIA_DIGEST_DEVICE_ID=<device_id>
MEDIA_DIGEST_DEVICE_SECRET=<device_secret>
MEDIA_DIGEST_VAULT_ROOT=C:\repositories\secondbrain
```

## Sync

List/sync pending links:

```powershell
python scripts\sync_media_captures.py
```

Sync and process:

```powershell
python scripts\sync_media_captures.py --all
```

Generated notes land in:

```text
C:\repositories\secondbrain\00-Inbox
```

Artifacts land in:

```text
C:\repositories\secondbrain\Meta\media-captures
```
