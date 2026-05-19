import { appHtml, faviconSvg } from "./app";

interface Env {
  MEDIA_DIGEST_KV: KVNamespace;
  PAIRING_TOKEN: string;
  CAPTURE_TOKEN?: string;
  MAX_URL_LENGTH?: string;
}

type MediaStatus =
  | "captured"
  | "synced"
  | "extracting"
  | "transcribing"
  | "summarizing"
  | "saved"
  | "failed";

interface RegisteredDevice {
  id: string;
  name: string;
  secret: string;
  created_at: string;
  last_seen_at?: string;
  revoked?: boolean;
}

interface MediaCapture {
  id: string;
  url: string;
  platform: "youtube" | "instagram" | "unknown";
  source: string;
  status: MediaStatus;
  captured_at: string;
  created_at: string;
  updated_at?: string;
  device_id?: string;
  message?: string;
}

const statuses: MediaStatus[] = [
  "captured",
  "synced",
  "extracting",
  "transcribing",
  "summarizing",
  "saved",
  "failed"
];
const signatureWindowMs = 5 * 60 * 1000;

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/") {
      return html(appHtml);
    }

    if (url.pathname === "/favicon.svg" || url.pathname === "/favicon.ico") {
      return new Response(faviconSvg, {
        headers: { "content-type": "image/svg+xml; charset=utf-8" }
      });
    }

    if (!url.pathname.startsWith("/api/")) {
      return json({ error: "not_found" }, 404);
    }

    if (url.pathname === "/api/pair" && request.method === "POST") {
      return pairDevice(request, env);
    }

    if (url.pathname === "/api/media/capture-token" && request.method === "POST") {
      return captureWithToken(request, env);
    }

    const auth = await verifyDeviceSignature(request, env);
    if (!auth.ok) {
      return json({ error: auth.error }, 401);
    }

    if (url.pathname === "/api/media/capture" && request.method === "POST") {
      return captureMedia(request, env, auth.device.id);
    }

    if (url.pathname === "/api/media/pending" && request.method === "GET") {
      return listMedia(request, env, ["captured", "failed"]);
    }

    if (url.pathname === "/api/media" && request.method === "GET") {
      return listMedia(request, env);
    }

    const statusMatch = url.pathname.match(/^\/api\/media\/([^/]+)\/status$/);
    if (statusMatch && request.method === "POST") {
      return updateMediaStatus(statusMatch[1], request, env);
    }

    return json({ error: "not_found" }, 404);
  }
};

async function pairDevice(request: Request, env: Env): Promise<Response> {
  const header = request.headers.get("authorization") || "";
  if (header !== `Bearer ${env.PAIRING_TOKEN}`) {
    return json({ error: "invalid_pairing_token" }, 401);
  }

  const payload = await request.json().catch(() => ({})) as { name?: string };
  const now = new Date().toISOString();
  const id = crypto.randomUUID();
  const secret = randomSecret();
  const device: RegisteredDevice = {
    id,
    secret,
    name: cleanDeviceName(payload.name),
    created_at: now
  };

  await env.MEDIA_DIGEST_KV.put(deviceKey(id), JSON.stringify(device));

  return json({
    ok: true,
    device_id: id,
    device_secret: secret,
    created_at: now
  });
}

async function captureWithToken(request: Request, env: Env): Promise<Response> {
  if (!env.CAPTURE_TOKEN) {
    return json({ error: "capture_token_not_configured" }, 500);
  }

  const header = request.headers.get("authorization") || "";
  if (header !== `Bearer ${env.CAPTURE_TOKEN}`) {
    return json({ error: "invalid_capture_token" }, 401);
  }

  return captureMedia(request, env, "ios-shortcut");
}

async function captureMedia(request: Request, env: Env, deviceId: string): Promise<Response> {
  const payload = await request.json().catch(() => ({})) as {
    url?: string;
    source?: string;
    captured_at?: string;
  };
  const cleanUrl = normalizeMediaUrl(payload.url || "", env);
  if (!cleanUrl.ok) {
    return json({ error: cleanUrl.error }, 400);
  }

  const now = new Date();
  const id = `${compactIso(now)}-${crypto.randomUUID().slice(0, 8)}`;
  const capture: MediaCapture = {
    id,
    url: cleanUrl.url,
    platform: detectPlatform(cleanUrl.url),
    source: cleanSource(payload.source),
    status: "captured",
    captured_at: validIsoOrNow(payload.captured_at, now),
    created_at: now.toISOString(),
    device_id: deviceId
  };

  await env.MEDIA_DIGEST_KV.put(mediaKey(id), JSON.stringify(capture));

  return json({ ok: true, id, status: capture.status, platform: capture.platform });
}

async function listMedia(
  request: Request,
  env: Env,
  allowedStatuses?: MediaStatus[]
): Promise<Response> {
  const url = new URL(request.url);
  const limit = Math.min(Number(url.searchParams.get("limit") || "50"), 100);
  const listed = await env.MEDIA_DIGEST_KV.list({ prefix: "media:", limit: 1000 });
  const items: MediaCapture[] = [];

  for (const key of listed.keys) {
    const value = await env.MEDIA_DIGEST_KV.get(key.name);
    if (!value) continue;
    const item = JSON.parse(value) as MediaCapture;
    if (allowedStatuses && !allowedStatuses.includes(item.status)) continue;
    items.push(item);
  }

  items.sort((left, right) => left.created_at.localeCompare(right.created_at));
  return json({ items: items.slice(0, limit) });
}

async function updateMediaStatus(id: string, request: Request, env: Env): Promise<Response> {
  const payload = await request.json().catch(() => ({})) as {
    status?: MediaStatus;
    message?: string;
  };
  if (!payload.status || !statuses.includes(payload.status)) {
    return json({ error: "invalid_status" }, 400);
  }

  const value = await env.MEDIA_DIGEST_KV.get(mediaKey(id));
  if (!value) {
    return json({ error: "not_found" }, 404);
  }

  const item = JSON.parse(value) as MediaCapture;
  item.status = payload.status;
  item.updated_at = new Date().toISOString();
  item.message = cleanMessage(payload.message);
  await env.MEDIA_DIGEST_KV.put(mediaKey(id), JSON.stringify(item));

  return json({ ok: true, id, status: item.status });
}

async function verifyDeviceSignature(
  request: Request,
  env: Env
): Promise<{ ok: true; device: RegisteredDevice } | { ok: false; error: string }> {
  const deviceId = request.headers.get("x-device-id") || "";
  const timestamp = request.headers.get("x-timestamp") || "";
  const nonce = request.headers.get("x-nonce") || "";
  const bodyHash = request.headers.get("x-body-sha256") || "";
  const signature = request.headers.get("x-signature") || "";

  if (!deviceId || !timestamp || !nonce || !bodyHash || !signature) {
    return { ok: false, error: "missing_device_signature" };
  }

  const timestampMs = Date.parse(timestamp);
  if (!Number.isFinite(timestampMs) || Math.abs(Date.now() - timestampMs) > signatureWindowMs) {
    return { ok: false, error: "stale_device_signature" };
  }

  const device = await getDevice(deviceId, env);
  if (!device || device.revoked) {
    return { ok: false, error: "unknown_device" };
  }

  const computedBodyHash = await sha256Hex(await request.clone().arrayBuffer());
  if (!constantTimeEqual(bodyHash, computedBodyHash)) {
    return { ok: false, error: "body_hash_mismatch" };
  }

  const path = new URL(request.url).pathname;
  const canonical = [request.method.toUpperCase(), path, timestamp, nonce, bodyHash].join("\n");
  const expected = await hmacHex(device.secret, canonical);
  if (!constantTimeEqual(signature, expected)) {
    return { ok: false, error: "invalid_device_signature" };
  }

  device.last_seen_at = new Date().toISOString();
  await env.MEDIA_DIGEST_KV.put(deviceKey(device.id), JSON.stringify(device));

  return { ok: true, device };
}

async function getDevice(id: string, env: Env): Promise<RegisteredDevice | null> {
  const value = await env.MEDIA_DIGEST_KV.get(deviceKey(id));
  if (!value) return null;
  return JSON.parse(value) as RegisteredDevice;
}

function normalizeMediaUrl(value: string, env: Env): { ok: true; url: string } | { ok: false; error: string } {
  const trimmed = value.trim();
  if (!trimmed) {
    return { ok: false, error: "missing_url" };
  }

  const maxLength = Number(env.MAX_URL_LENGTH || "2048");
  if (trimmed.length > maxLength) {
    return { ok: false, error: "url_too_long" };
  }

  try {
    const parsed = new URL(trimmed);
    if (!["http:", "https:"].includes(parsed.protocol)) {
      return { ok: false, error: "invalid_url_protocol" };
    }
    return { ok: true, url: parsed.toString() };
  } catch {
    return { ok: false, error: "invalid_url" };
  }
}

function detectPlatform(url: string): MediaCapture["platform"] {
  const host = new URL(url).hostname.replace(/^www\./, "").toLowerCase();
  if (host === "youtu.be" || host.endsWith("youtube.com")) return "youtube";
  if (host.endsWith("instagram.com")) return "instagram";
  return "unknown";
}

function validIsoOrNow(value: string | undefined, fallback: Date): string {
  if (!value) return fallback.toISOString();
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? new Date(parsed).toISOString() : fallback.toISOString();
}

function cleanDeviceName(name?: string): string {
  const cleaned = (name || "Media Digest device").trim().slice(0, 80);
  return cleaned || "Media Digest device";
}

function cleanSource(source?: string): string {
  const cleaned = (source || "ios-share").trim().slice(0, 60);
  return cleaned || "ios-share";
}

function cleanMessage(message?: string): string | undefined {
  const cleaned = (message || "").trim().slice(0, 500);
  return cleaned || undefined;
}

function mediaKey(id: string): string {
  return `media:${id}`;
}

function deviceKey(id: string): string {
  return `device:${id}`;
}

function randomSecret(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return base64Url(bytes);
}

async function hmacHex(secret: string, message: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const signature = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message));
  return toHex(signature);
}

async function sha256Hex(body: ArrayBuffer): Promise<string> {
  return toHex(await crypto.subtle.digest("SHA-256", body));
}

function toHex(buffer: ArrayBuffer): string {
  return [...new Uint8Array(buffer)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

function base64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function constantTimeEqual(left: string, right: string): boolean {
  if (left.length !== right.length) return false;
  let result = 0;
  for (let index = 0; index < left.length; index += 1) {
    result |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return result === 0;
}

function compactIso(date: Date): string {
  return date.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
}

function html(body: string): Response {
  return new Response(body, {
    headers: { "content-type": "text/html; charset=utf-8" }
  });
}

function json(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" }
  });
}
