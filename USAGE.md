# Usage

## Capture From iPhone

1. Open YouTube or Instagram.
2. Tap Share.
3. Run the Shortcut "Salvar no Media Digest".
4. The Shortcut sends the URL to `/api/media/capture-token`.

## Process Locally

From this repo:

```powershell
python scripts\sync_media_captures.py --all
```

The local script:

1. Fetches pending links from Cloudflare.
2. Extracts audio with `yt-dlp`.
3. Transcribes with `faster-whisper`.
4. Creates one `media-capture` note in `00-Inbox`.
5. Marks the remote item as `saved` or `failed`.

## Notes

- YouTube is the primary supported platform.
- Instagram Reels are best effort and may need cookies.
- If Instagram fails, export browser cookies and set `MEDIA_DIGEST_YTDLP_COOKIES`.
