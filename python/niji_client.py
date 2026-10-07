"""niji・journey API client.

Implements the device/guest authentication flow, session management and the
image generation (job submit / poll) API used by the official Android app.

Cloudflare is bypassed with ``curl_cffi`` browser impersonation.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from curl_cffi import requests

BASE = "https://nijijourney.com"
CDN = "https://cdn.midjourney.com"
APP_VERSION = "1.44.0"
FIREBASE_API_KEY = "AIzaSyBzVBIfh_wNNScBDcNeuKd4cyspiR8qrMo"   # authjourney project
GOOGLE_CLOUD_PROJECT = 624942467596                            # Play Integrity project
IMPERSONATE = "safari17_0"

# model alias -> the midjourney flag appended to the prompt
MODEL_FLAGS = {
    "niji-7": "--niji 7", "niji7": "--niji 7", "niji 7": "--niji 7",
    "niji-6": "--niji 6", "niji6": "--niji 6", "niji 6": "--niji 6",
    "niji-5": "--niji 5", "niji5": "--niji 5", "niji 5": "--niji 5",
    "niji-4": "--niji 4", "niji4": "--niji 4", "niji 4": "--niji 4",
    "midjourney": "--v 7", "mj": "--v 7", "v7": "--v 7",
    "v6.1": "--v 6.1", "v6": "--v 6", "v5": "--v 5", "v8": "--v 8",
    "v8.1": "--v 8.1",
}

# classic aspect ratios / sizes -> --ar
SIZE_TO_AR = {
    "256x256": "1:1", "512x512": "1:1", "1024x1024": "1:1", "2048x2048": "1:1",
    "1792x1024": "16:9", "1024x1792": "9:16", "1536x1024": "3:2",
    "1024x1536": "2:3", "1216x832": "3:2", "832x1216": "2:3",
    "1344x768": "7:4", "768x1344": "4:7",
}

STYLE_FLAGS = {"raw", "expressive", "cute", "scenic", "original"}


class NijiError(Exception):
    def __init__(self, status: int, message: str, body: Any = None):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message
        self.body = body


@dataclass
class Job:
    id: str
    status: str = "running"
    prompt: str = ""
    width: int = 0
    height: int = 0
    batch_size: int = 4
    image_indices: list[int] = field(default_factory=list)

    def image_urls(self) -> list[str]:
        return [f"{CDN}/{self.id}/0_{i}.png" for i in self.image_indices or range(self.batch_size)]

    def grid_url(self) -> str:
        return f"{CDN}/{self.id}_grid_0.webp"


class NijiClient:
    def __init__(
        self,
        session: dict | None = None,
        proxy: str | None = None,
        integrity: Callable[[str], str] | None = None,
        timeout: float = 60.0,
    ):
        self.proxy = proxy
        self.timeout = timeout
        self.integrity = integrity
        self.proxies = {"https": proxy, "http": proxy} if proxy else None
        self.http = requests.Session(impersonate=IMPERSONATE, proxies=self.proxies)
        self.session: dict = session or {}

    # -- low level ---------------------------------------------------------
    def _headers(self, auth: bool) -> dict:
        h = {
            "x-app-version": APP_VERSION,
            "x-csrf-protection": "1",
            "content-type": "application/json",
        }
        if auth and self.session.get("idToken"):
            h["authorization"] = "Bearer " + self.session["idToken"]
        return h

    def _request(
        self,
        method: str,
        path: str,
        body: Any = None,
        auth: bool = True,
        retries: int = 5,
        raise_for_status: bool = True,
    ):
        url = path if path.startswith("http") else BASE + path
        if "app_version" not in url:
            url += ("&" if "?" in url else "?") + "app_version=" + APP_VERSION
        last = None
        for attempt in range(retries):
            try:
                r = self.http.request(
                    method, url, headers=self._headers(auth), json=body, timeout=self.timeout
                )
                if r.status_code == 401 and auth and self.session.get("refreshToken"):
                    self.refresh()
                    r = self.http.request(
                        method, url, headers=self._headers(auth), json=body, timeout=self.timeout
                    )
                if raise_for_status and r.status_code >= 400:
                    try:
                        payload = r.json()
                    except Exception:
                        payload = {"message": r.text[:300]}
                    raise NijiError(r.status_code, payload.get("message", ""), payload)
                return r
            except NijiError:
                raise
            except Exception as exc:  # transient TLS / proxy errors
                last = exc
                time.sleep(1.5 * (attempt + 1))
        raise NijiError(0, f"request failed: {last}")

    def _json(self, method: str, path: str, body: Any = None, **kw):
        return self._request(method, path, body, **kw).json()

    # -- authentication ----------------------------------------------------
    def request_nonce(self, device_id: str) -> str:
        j = self._json(
            "POST",
            "/api/niji-app/auth/integrity-token",
            {"method": "google_device", "sub": device_id},
            auth=False,
        )
        return j["token"]

    def device_login(
        self,
        device_id: str,
        integrity_token: str | None = None,
        is_bind: bool = False,
    ) -> dict:
        nonce = self.request_nonce(device_id)
        if integrity_token is None and self.integrity is not None:
            integrity_token = self.integrity(nonce)
        body = {
            "device_id": device_id,
            "only_use_receipt": False,
            "integrity_token": integrity_token,
            "key_id": None,
            "client_data": nonce,
            "receipt": None,
        }
        if is_bind:
            body["is_bind"] = True
        j = self._json("POST", "/api/niji-app/auth/google-device", body, auth=False)
        self.session["device_id"] = device_id
        if j.get("idp_token"):
            self.firebase_signin(j["idp_token"])
        self.session["last_login"] = j
        return j

    def _retry_http(self, fn, retries: int = 6, delay: float = 1.5):
        last = None
        for attempt in range(retries):
            try:
                return fn()
            except Exception as exc:  # transient TLS / proxy
                last = exc
                time.sleep(delay * (attempt + 1))
        raise NijiError(0, f"request failed: {last}")

    def firebase_signin(self, custom_token: str) -> dict:
        def do():
            r = self.http.post(
                "https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken?key="
                + FIREBASE_API_KEY,
                json={"token": custom_token, "returnSecureToken": True},
                timeout=self.timeout,
            )
            return r.json()

        j = self._retry_http(do)
        if "idToken" not in j:
            raise NijiError(0, "firebase sign-in failed", j)
        self.session.update(
            idToken=j["idToken"],
            refreshToken=j.get("refreshToken"),
            uid=j.get("localId"),
        )
        return j

    def refresh(self) -> dict:
        token = self.session.get("refreshToken")
        if not token:
            raise NijiError(401, "no refresh token")

        def do():
            r = self.http.post(
                "https://securetoken.googleapis.com/v1/token?key=" + FIREBASE_API_KEY,
                data={"grant_type": "refresh_token", "refresh_token": token},
                headers={"content-type": "application/x-www-form-urlencoded"},
                timeout=self.timeout,
            )
            return r.json()

        j = self._retry_http(do)
        if "id_token" not in j:
            raise NijiError(0, "token refresh failed", j)
        self.session["idToken"] = j["id_token"]
        self.session["refreshToken"] = j.get("refresh_token", token)
        return j

    def fetch_session(self) -> dict:
        j = self._json("GET", "/api/niji-app/auth/session")
        self.session["session"] = j.get("session")
        return j

    def accept_tos(self) -> dict:
        return self._json("POST", "/api/niji-app/user/accept-tos", {})

    def ensure_trial_generations(self) -> dict:
        return self._json("POST", "/api/niji-app/user/ensure-trial-generations")

    def usage(self) -> dict:
        return self._json("GET", "/api/niji-app/user/usage")

    def register(self, device_id: str, accept_tos: bool = True) -> dict:
        """Full guest registration including the free-trial grant."""
        self.device_login(device_id)
        self.fetch_session()
        if accept_tos:
            try:
                self.accept_tos()
            except NijiError:
                pass
        try:
            return self.ensure_trial_generations()
        except NijiError as exc:
            # The account exists even when the trial grant is refused (usually
            # because Play Integrity could not be verified).
            return {"error": True, "message": exc.message}

    # -- generation --------------------------------------------------------
    def build_prompt(
        self,
        prompt: str,
        model: str | None = None,
        size: str | None = None,
        aspect_ratio: str | None = None,
        stylize: int | None = None,
        style: str | None = None,
        negative: str | Iterable[str] | None = None,
        seed: int | None = None,
        chaos: int | None = None,
        weird: int | None = None,
        hd: bool = False,
        image_prompts: Iterable[str] = (),
        style_refs: Iterable[str] = (),
        style_weight: int | None = None,
        character_refs: Iterable[str] = (),
        character_weight: int | None = None,
        omni_refs: Iterable[str] = (),
        omni_weight: int | None = None,
        extra_flags: str = "",
    ) -> str:
        parts: list[str] = []

        def norm(ref: str, weight: float | None = None) -> str:
            if weight is None:
                return ref
            return f"{ref}::{weight}"

        for ref in image_prompts:
            parts.append(norm(ref))

        text = (prompt or "").strip()
        parts.append(text)

        ar = aspect_ratio or (SIZE_TO_AR.get(size or "", None) if size else None)
        if ar:
            parts.append(f"--ar {ar}")
        if model:
            key = model.lower().replace("_", "-")
            flag = MODEL_FLAGS.get(key)
            if flag is None and key.startswith("--"):
                flag = model
            # unknown aliases (dall-e-3, gpt-image-1, ...) keep the default model
            if flag is not None:
                parts.append(flag)
        if stylize is not None:
            parts.append(f"--stylize {int(stylize)}")
        if style and style.lower() in STYLE_FLAGS:
            parts.append(f"--style {style.lower()}")
        if negative:
            neg = negative if isinstance(negative, str) else ",".join(negative)
            parts.append(f"--no {neg}")
        if seed is not None:
            parts.append(f"--seed {int(seed)}")
        if chaos is not None:
            parts.append(f"--chaos {int(chaos)}")
        if weird is not None:
            parts.append(f"--weird {int(weird)}")
        if style_refs:
            # only the last style ref is used by MJ; keep them all for clarity
            joined = " ".join(style_refs)
            parts.append(f"--sref {joined}")
            if style_weight is not None:
                parts.append(f"--sw {int(style_weight)}")
        if character_refs:
            joined = " ".join(character_refs)
            parts.append(f"--cref {joined}")
            if character_weight is not None:
                parts.append(f"--cw {int(character_weight)}")
        if omni_refs:
            joined = " ".join(omni_refs)
            parts.append(f"--oref {joined}")
            if omni_weight is not None:
                parts.append(f"--ow {int(omni_weight)}")
        if hd:
            parts.append("--hd")
        if extra_flags:
            parts.append(extra_flags.strip())
        return " ".join(p for p in parts if p)

    def submit(
        self,
        prompt: str,
        mode: str = "fast",
        private: bool = False,
        parent_job: dict | None = None,
        channel: str = "Home Workspace",
        job_type: str = "imagine",
        params: dict | None = None,
    ) -> dict:
        job: dict[str, Any] = {
            "channelName": channel,
            "flags": {"mode": mode, "private": private},
            "prompt": prompt,
            "isMobile": True,
        }
        if job_type:
            job["jobType"] = job_type
        if parent_job:
            job["parentJob"] = parent_job
        if params:
            job.update(params)
        # the official client POSTs the job object directly (not wrapped in {"jobs": [...]})
        body = job
        try:
            return self._json("POST", "/api/niji-app/job/submit", body)
        except NijiError as exc:
            if exc.status == 400 and exc.message == "need_integrity_token":
                nonce = (exc.body or {}).get("nonce")
                if not nonce or self.integrity is None:
                    raise NijiError(
                        exc.status,
                        "server requires a Play Integrity token "
                        "(run with the Frida oracle or pass --integrity-token)",
                        exc.body,
                    )
                token = self.integrity(nonce)
                # the official client sends it as {"token": {"type": "android", "data": <jwt>}}
                body["token"] = {"type": "android", "data": token}
                return self._json("POST", "/api/niji-app/job/submit", body)
            raise

    def job_status(self, job_ids: list[str]) -> list[dict]:
        j = self._json("POST", "/api/niji-app/job/status", {"job_ids": job_ids})
        if isinstance(j, dict):
            jobs = j.get("jobs") or j.get("data") or j.get("results") or []
        else:
            jobs = j
        return jobs if isinstance(jobs, list) else []

    def wait(
        self,
        job_id: str,
        timeout: float = 600.0,
        interval: float = 4.0,
    ) -> Job:
        deadline = time.time() + timeout
        last: Job | None = None
        while time.time() < deadline:
            items = self.job_status([job_id])
            for raw in items:
                if str(raw.get("id", "")) != job_id:
                    continue
                event = raw.get("event") or {}
                status = raw.get("current_status") or event.get("current_status") or "running"
                job = Job(
                    id=job_id,
                    status=status,
                    prompt=raw.get("full_command", ""),
                    width=int((event.get("width") or 0)),
                    height=int((event.get("height") or 0)),
                    batch_size=int(event.get("batchSize") or 4),
                )
                last = job
                if status in ("completed", "error", "canceled", "interrupted"):
                    return job
            time.sleep(interval)
        if last:
            return last
        raise TimeoutError(f"job {job_id} did not finish within {timeout}s")

    # -- persistence -------------------------------------------------------
    def save(self, path: str = ".niji_session.json") -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.session, f, indent=2)

    @classmethod
    def load(cls, path: str = ".niji_session.json", **kw) -> "NijiClient":
        with open(path, "r", encoding="utf-8") as f:
            return cls(session=json.load(f), **kw)


def new_device_id() -> str:
    """Random 16-hex device id (same shape as the app-scoped Android ID)."""
    return uuid.uuid4().hex[:16]
