#!/usr/bin/env python3
"""Sync and locally process Media Digest captures."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import pathlib
import re
import subprocess
import sys
import uuid
import urllib.error
import urllib.request


ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "media-sync.env"
MANIFEST_PATH = ROOT / "sync-manifest.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync pending media links into the local vault.")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="Path to env-style config file.")
    parser.add_argument("--dry-run", action="store_true", help="List pending captures without processing.")
    parser.add_argument("--process", action="store_true", help="Extract, transcribe, and create notes.")
    parser.add_argument("--all", action="store_true", help="Sync pending captures, then process synced captures.")
    args = parser.parse_args()

    config = load_config(pathlib.Path(args.config))
    base_url = require(config, "MEDIA_DIGEST_BASE_URL").rstrip("/")
    device_id = require(config, "MEDIA_DIGEST_DEVICE_ID")
    device_secret = require(config, "MEDIA_DIGEST_DEVICE_SECRET")
    vault_root = pathlib.Path(require(config, "MEDIA_DIGEST_VAULT_ROOT"))

    manifest = load_manifest()
    client = SignedClient(base_url, device_id, device_secret)

    if args.all:
        sync_result = sync_pending_media(manifest, client, args.dry_run)
        if sync_result != 0:
            return sync_result
        return process_synced_media(config, vault_root, manifest, client, args.dry_run)

    if args.process:
        return process_synced_media(config, vault_root, manifest, client, args.dry_run)

    return sync_pending_media(manifest, client, args.dry_run)


def sync_pending_media(manifest: dict, client: "SignedClient", dry_run: bool) -> int:
    pending = client.json_request("GET", "/api/media/pending").get("items", [])
    if not pending:
        print("No pending media captures.")
        return 0

    print(f"Pending media captures: {len(pending)}")
    for item in pending:
        capture_id = item["id"]
        if capture_id in manifest.get("synced_ids", []):
            print(f"Skip already synced locally: {capture_id}")
            continue

        if dry_run:
            print(f"Would sync {capture_id}: {item['url']}")
            continue

        manifest.setdefault("synced_ids", []).append(capture_id)
        manifest.setdefault("captures", {})[capture_id] = {
            "id": capture_id,
            "url": item["url"],
            "platform": item.get("platform", "unknown"),
            "source": item.get("source", "ios-share"),
            "created_at": item.get("captured_at") or item.get("created_at"),
            "remote_status": "synced",
            "synced_at": utc_now(),
        }
        save_manifest(manifest)
        client.json_request("POST", f"/api/media/{capture_id}/status", {"status": "synced"})
        print(f"Synced {capture_id}")

    return 0


def process_synced_media(
    config: dict[str, str],
    vault_root: pathlib.Path,
    manifest: dict,
    client: "SignedClient",
    dry_run: bool,
) -> int:
    captures = manifest.get("captures", {})
    pending = [
        (capture_id, capture)
        for capture_id, capture in captures.items()
        if capture.get("remote_status") == "synced" and not capture.get("note_file")
    ]

    if not pending:
        print("No synced media pending processing.")
        return 0

    log(f"Synced media pending processing: {len(pending)}")
    transcriber = None if dry_run else FasterWhisperTranscriber(config)

    for capture_id, capture in pending:
        created_at = parse_iso(capture["created_at"])
        artifacts_dir = vault_root / artifacts_relative_dir(created_at, capture_id)
        log(f"Start {capture_id}: {capture['url']}")

        if dry_run:
            print(f"Would process {capture_id}: {capture['url']}")
            print(f"Would write artifacts under {artifacts_dir}")
            continue

        try:
            log("Status -> extracting")
            client.json_request("POST", f"/api/media/{capture_id}/status", {"status": "extracting"})
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            extracted = extract_media(config, capture["url"], artifacts_dir, capture_id)
            duration = format_duration(extracted.duration_seconds)
            log(f"Extracted audio: {extracted.audio_path.name} | title={extracted.title!r} | duration={duration}")

            log("Status -> transcribing")
            client.json_request("POST", f"/api/media/{capture_id}/status", {"status": "transcribing"})
            transcript = transcriber.transcribe(extracted.audio_path, duration_seconds=extracted.duration_seconds)
            transcript_path = artifacts_dir / f"{capture_id}.transcript.json"
            transcript_path.write_text(json.dumps(transcript.to_dict(), indent=2) + "\n", encoding="utf-8")
            log(f"Transcript saved: {transcript_path}")

            log("Status -> summarizing")
            client.json_request("POST", f"/api/media/{capture_id}/status", {"status": "summarizing"})
            note_path = vault_root / note_relative_path(created_at, extracted.title, capture_id)
            write_media_note(
                note_path=note_path,
                created_at=created_at,
                capture_id=capture_id,
                capture=capture,
                extracted=extracted,
                transcript=transcript,
                artifacts_dir=artifacts_dir.relative_to(vault_root),
            )
            log(f"Note written: {note_path}")

            capture["note_file"] = str(note_path.relative_to(vault_root)).replace("\\", "/")
            capture["artifacts_dir"] = str(artifacts_dir.relative_to(vault_root)).replace("\\", "/")
            capture["remote_status"] = "saved"
            capture["processed_at"] = utc_now()
            save_manifest(manifest)
            client.json_request(
                "POST",
                f"/api/media/{capture_id}/status",
                {"status": "saved", "message": f"Created {capture['note_file']}"},
            )
            log(f"Done {capture_id} -> {note_path}")
        except Exception as exc:
            capture["remote_status"] = "failed"
            capture["error"] = str(exc)
            save_manifest(manifest)
            client.json_request(
                "POST",
                f"/api/media/{capture_id}/status",
                {"status": "failed", "message": str(exc)},
            )
            log(f"Failed {capture_id}: {exc}", stream=sys.stderr)

    return 0


class SignedClient:
    def __init__(self, base_url: str, device_id: str, device_secret: str) -> None:
        self.base_url = base_url
        self.device_id = device_id
        self.device_secret = device_secret.encode("utf-8")

    def json_request(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"content-type": "application/json"} if payload is not None else {}
        data = self.request(method, path, body, headers)
        return json.loads(data.decode("utf-8"))

    def request(self, method: str, path: str, body: bytes, headers: dict[str, str]) -> bytes:
        body_hash = hashlib.sha256(body).hexdigest()
        timestamp = utc_now()
        nonce = str(uuid.uuid4())
        canonical = "\n".join([method.upper(), path, timestamp, nonce, body_hash])
        signature = hmac.new(self.device_secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()

        signed_headers = {
            **headers,
            "user-agent": "MediaDigestSync/0.1",
            "accept": "application/json, */*",
            "x-device-id": self.device_id,
            "x-timestamp": timestamp,
            "x-nonce": nonce,
            "x-body-sha256": body_hash,
            "x-signature": signature,
        }
        request = urllib.request.Request(
            self.base_url + path,
            data=body if method.upper() not in {"GET", "HEAD"} else None,
            headers=signed_headers,
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {error.code} {path}: {detail}") from error


class ExtractedMedia:
    def __init__(
        self,
        title: str,
        creator: str,
        duration_seconds: int | None,
        audio_path: pathlib.Path,
        info_path: pathlib.Path,
    ) -> None:
        self.title = title
        self.creator = creator
        self.duration_seconds = duration_seconds
        self.audio_path = audio_path
        self.info_path = info_path


class TranscriptionResult:
    def __init__(self, text: str, segments: list[dict], engine: str, confidence: str) -> None:
        self.text = text
        self.segments = segments
        self.engine = engine
        self.confidence = confidence

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "segments": self.segments,
            "engine": self.engine,
            "confidence": self.confidence,
        }


class FasterWhisperTranscriber:
    def __init__(self, config: dict[str, str]) -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("Missing faster-whisper. Install it with: pip install -r requirements-transcribe.txt") from exc

        self.model_name = config.get("MEDIA_DIGEST_WHISPER_MODEL", "small")
        self.language = empty_to_none(config.get("MEDIA_DIGEST_TRANSCRIPTION_LANGUAGE"))
        device = config.get("MEDIA_DIGEST_WHISPER_DEVICE", "cpu")
        compute_type = config.get("MEDIA_DIGEST_WHISPER_COMPUTE_TYPE", "int8")
        log(f"Loading faster-whisper model={self.model_name} device={device} compute_type={compute_type}")
        self.model = WhisperModel(self.model_name, device=device, compute_type=compute_type)
        log("Whisper model ready")

    def transcribe(self, audio_path: pathlib.Path, duration_seconds: int | None = None) -> TranscriptionResult:
        log(f"Transcription started: {audio_path}")
        segments_iter, _info = self.model.transcribe(str(audio_path), language=self.language)
        segments = []
        last_progress = -1
        for segment in segments_iter:
            text = segment.text.strip()
            if not text:
                continue
            segments.append({"start": segment.start, "end": segment.end, "text": text})
            if duration_seconds:
                progress = min(100, int((segment.end / duration_seconds) * 100))
                if progress >= last_progress + 5:
                    log(f"Transcribing... {progress}% ({format_timestamp(segment.end)} / {format_duration(duration_seconds)})")
                    last_progress = progress
            elif len(segments) % 25 == 0:
                log(f"Transcribing... {len(segments)} segments ({format_timestamp(segment.end)})")
        text = "\n".join(segment["text"] for segment in segments)
        confidence = "medium" if text else "low"
        log(f"Transcription finished: {len(segments)} segments")
        return TranscriptionResult(
            text=text or "_No speech detected._",
            segments=segments,
            engine=f"faster-whisper:{self.model_name}",
            confidence=confidence,
        )


def extract_media(config: dict[str, str], url: str, output_dir: pathlib.Path, capture_id: str) -> ExtractedMedia:
    ytdlp = config.get("MEDIA_DIGEST_YTDLP", "yt-dlp")
    ffmpeg = resolve_ffmpeg(config)
    output_template = str(output_dir / f"{capture_id}.%(ext)s")
    command = [
        ytdlp,
        "--no-playlist",
        "--write-info-json",
        "-x",
        "--audio-format",
        "m4a",
        "-o",
        output_template,
        url,
    ]
    if ffmpeg:
        command[1:1] = ["--ffmpeg-location", ffmpeg]
    cookies = empty_to_none(config.get("MEDIA_DIGEST_YTDLP_COOKIES"))
    if cookies:
        command[1:1] = ["--cookies", cookies]

    log("yt-dlp started")
    subprocess.run(command, check=True)
    log("yt-dlp finished")

    info_path = output_dir / f"{capture_id}.info.json"
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
    audio_candidates = list(output_dir.glob(f"{capture_id}.*"))
    audio_candidates = [path for path in audio_candidates if path.suffix.lower() in {".m4a", ".mp3", ".opus", ".webm", ".wav"}]
    if not audio_candidates:
        raise RuntimeError("yt-dlp did not produce an audio file")

    audio_path = sorted(audio_candidates, key=lambda path: path.stat().st_mtime, reverse=True)[0]
    return ExtractedMedia(
        title=str(info.get("title") or capture_id),
        creator=str(info.get("uploader") or info.get("channel") or ""),
        duration_seconds=int(info["duration"]) if info.get("duration") is not None else None,
        audio_path=audio_path,
        info_path=info_path,
    )


def resolve_ffmpeg(config: dict[str, str]) -> str | None:
    configured = empty_to_none(config.get("MEDIA_DIGEST_FFMPEG"))
    if configured and configured != "ffmpeg":
        return configured
    try:
        import imageio_ffmpeg
    except ImportError:
        return configured
    return imageio_ffmpeg.get_ffmpeg_exe()


def write_media_note(
    note_path: pathlib.Path,
    created_at: dt.datetime,
    capture_id: str,
    capture: dict,
    extracted: ExtractedMedia,
    transcript: TranscriptionResult,
    artifacts_dir: pathlib.Path,
) -> None:
    if note_path.exists():
        return

    note_path.parent.mkdir(parents=True, exist_ok=True)
    created = created_at.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    transcript_text = format_segments(transcript)
    summary = rough_summary(transcript.text)
    artifact_path = str(artifacts_dir).replace("\\", "/")
    safe_title = yaml_escape(extracted.title)
    safe_creator = yaml_escape(extracted.creator)
    content = f"""---
type: media-capture
status: pending-triage
created: {created}
source: {capture.get("source", "ios-share")}
platform: {capture.get("platform", "unknown")}
source_url: "{capture["url"]}"
media_title: "{safe_title}"
creator: "{safe_creator}"
duration_seconds: {extracted.duration_seconds if extracted.duration_seconds is not None else ""}
transcription_engine: {transcript.engine}
extraction_engine: yt-dlp
confidence: {transcript.confidence}
remote_id: {capture_id}
artifacts_dir: {artifact_path}
---

# {extracted.title}

## Source

- URL: {capture["url"]}
- Platform: {capture.get("platform", "unknown")}
- Creator: {extracted.creator or "Unknown"}
- Duration: {format_duration(extracted.duration_seconds)}

## Executive Summary

{summary}

## Key Ideas

_Pending review._

## Useful Details

_Pending review._

## Action Items / Follow-ups

_Pending review._

## Transcript

{transcript_text}
"""
    note_path.write_text(content, encoding="utf-8")


def format_segments(transcript: TranscriptionResult) -> str:
    if not transcript.segments:
        return transcript.text.strip()
    lines = []
    for segment in transcript.segments:
        lines.append(f"- [{format_timestamp(segment['start'])}] {segment['text']}")
    return "\n".join(lines)


def rough_summary(text: str) -> str:
    cleaned = " ".join(text.split())
    if not cleaned or cleaned == "_No speech detected._":
        return "_No speech detected._"
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    return " ".join(sentences[:4]).strip()


def artifacts_relative_dir(created_at: dt.datetime, capture_id: str) -> pathlib.Path:
    return pathlib.Path(
        "Meta",
        "media-captures",
        f"{created_at.year:04d}",
        f"{created_at.month:02d}",
        capture_id,
    )


def note_relative_path(created_at: dt.datetime, title: str, capture_id: str) -> pathlib.Path:
    timestamp = created_at.astimezone(dt.timezone.utc).strftime("%Y-%m-%d-%H%M")
    slug = slugify(title) or capture_id
    return pathlib.Path("00-Inbox", f"{timestamp}-media-capture-{slug}.md")


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return value[:70].strip("-")


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "Unknown"
    minutes, second = divmod(seconds, 60)
    hour, minute = divmod(minutes, 60)
    if hour:
        return f"{hour}:{minute:02d}:{second:02d}"
    return f"{minute}:{second:02d}"


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    minute, second = divmod(total, 60)
    hour, minute = divmod(minute, 60)
    if hour:
        return f"{hour:02d}:{minute:02d}:{second:02d}"
    return f"{minute:02d}:{second:02d}"


def yaml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def load_config(path: pathlib.Path) -> dict[str, str]:
    config: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            config[key.strip()] = value.strip().strip('"')
    return {**os.environ, **config}


def require(config: dict[str, str], key: str) -> str:
    value = config.get(key)
    if not value:
        raise RuntimeError(f"Missing config: {key}")
    return value


def empty_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        return {"synced_ids": [], "captures": {}}
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_iso(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def log(message: str, stream = sys.stdout) -> None:
    timestamp = dt.datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", file=stream, flush=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
