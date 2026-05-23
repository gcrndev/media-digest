# Usage

## Capture From iPhone

1. Open YouTube or Instagram.
2. Tap Share.
3. Run the Shortcut "Salvar no Media Digest".
4. The Shortcut sends the URL to `https://media-digest.gcrndev.workers.dev/api/media/capture-token`.

## Create The iOS Shortcut

This shortcut uses Apple's Share Sheet input and the Shortcuts "Get Contents of URL" action. Apple documents Share Sheet inputs in the Shortcuts User Guide, and the POST request setup in the "Request your first API" guide.

### 1. Create shortcut

1. Open the Shortcuts app on iPhone.
2. Tap `+`.
3. Name it `Salvar no Media Digest`.
4. Tap the shortcut info/settings button.
5. Enable `Show in Share Sheet`.
6. In accepted input types, keep `URLs` and `Text`.

### 2. Add token

1. Add action: `Text`.
2. Paste the `CAPTURE_TOKEN` value from:

```text
C:\repositories\media-digest\.dev.vars
```

This token is used only by the iOS Shortcut capture endpoint.

### 3. Prepare URL input

1. Add action: `Get URLs from Input`.
2. Set input to `Shortcut Input`.
3. Add action: `Get Item from List`.
4. Set it to `First Item`.

This makes the shortcut work when the shared app sends either a URL directly or text containing a URL.

### 4. Prepare timestamp

1. Add action: `Current Date`.
2. Add action: `Format Date`.
3. Format: `ISO 8601`.

### 5. Build request body

Add action: `Dictionary`.

Fields:

```text
url = First Item
source = ios-share
captured_at = Formatted Date
```

### 6. Send to Cloudflare

Add action: `Get Contents of URL`.

URL:

```text
https://media-digest.gcrndev.workers.dev/api/media/capture-token
```

Open `Show More` and set:

```text
Method: POST
Headers:
  Authorization: Bearer <Text action token>
  Content-Type: application/json
Request Body: JSON
JSON Body: Dictionary
```

In Shortcuts, use the Text action value after `Bearer `. The final header value should look like:

```text
Bearer your-token-here
```

### 7. Show result

Add action: `Show Notification`.

Text:

```text
Media Digest: link saved
```

Optional debug action during setup:

```text
Quick Look -> Contents of URL
```

Remove or disable `Quick Look` after the first successful test.

## Test The Shortcut

### Direct test

1. Open the shortcut inside Shortcuts.
2. Tap Run.
3. If it asks for input, paste a YouTube URL.
4. Expected response:

```json
{
  "ok": true,
  "id": "...",
  "status": "captured",
  "platform": "youtube"
}
```

### Share Sheet test

1. Open YouTube.
2. Open any video.
3. Tap `Share`.
4. Choose `Salvar no Media Digest`.
5. Wait for the success notification.

Then on PC:

```powershell
cd C:\repositories\media-digest
python scripts\sync_media_captures.py --dry-run
```

Expected:

```text
Pending media captures: 1
Would sync ...
```

To process fully:

```powershell
python scripts\sync_media_captures.py --all
```

To process a one-off link already sitting in the vault or clipboard:

```powershell
python scripts\sync_media_captures.py --url "https://www.instagram.com/reel/..."
```

## Troubleshooting iOS

### Shortcut does not appear in Share Sheet

Check:

- `Show in Share Sheet` is enabled.
- Accepted input types include `URLs` and `Text`.
- Reopen YouTube/Instagram after editing the shortcut.
- Scroll to the bottom of Share Sheet and tap `Edit Actions` if needed.

### Instagram sends text instead of clean URL

Keep the `Get URLs from Input` action. If it still fails:

1. Add `Get Clipboard` as fallback.
2. Copy the Reel link first.
3. Run the shortcut manually.

### Request fails with auth error

Check:

- Header key is exactly `Authorization`.
- Header value starts with `Bearer `.
- Token matches `CAPTURE_TOKEN` from `.dev.vars`.
- No extra spaces or line breaks after the token.

### Request succeeds but local sync shows no items

Check worker URL:

```text
https://media-digest.gcrndev.workers.dev/api/media/capture-token
```

Check local config:

```text
C:\repositories\media-digest\media-sync.env
```

## Process Locally

From this repo:

```powershell
python scripts\sync_media_captures.py --all
```

The local script:

1. Fetches pending links from Cloudflare.
2. Extracts audio with `yt-dlp`.
3. Transcribes with `faster-whisper`.
4. Analyzes the transcript locally into summary, key ideas, useful details,
   critical notes, follow-up actions, and revisit questions.
5. Creates one `media-capture` note in `00-Inbox`.
6. Marks the remote item as `saved` or `failed`.

## Notes

- YouTube is the primary supported platform.
- Instagram Reels are best effort and may need cookies.
- If Instagram fails, export browser cookies and set `MEDIA_DIGEST_YTDLP_COOKIES`.
- Portuguese and English videos are supported. Keep `MEDIA_DIGEST_TRANSCRIPTION_LANGUAGE=` empty in `media-sync.env` to let Whisper auto-detect the language.
