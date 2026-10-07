"""Local OpenAI / NovelAI compatible image API backed by niji・journey.

    python niji_server.py --proxy socks5h://127.0.0.1:12080 --frida --port 8000

Endpoints
---------
GET  /v1/models
POST /v1/images/generations        OpenAI
POST /ai/generate-image            NovelAI (returns a zip of PNGs)
GET  /niji/usage
GET  /niji/session
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import threading
import time
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from curl_cffi import requests

from niji_client import MODEL_FLAGS, NijiClient, NijiError

STATE: dict = {"client": None, "oracle": None, "lock": threading.Lock()}

OPENAI_MODELS = [
    {"id": "niji-7", "object": "model", "owned_by": "niji"},
    {"id": "niji-6", "object": "model", "owned_by": "niji"},
    {"id": "niji-5", "object": "model", "owned_by": "niji"},
    {"id": "niji-4", "object": "model", "owned_by": "niji"},
    {"id": "midjourney", "object": "model", "owned_by": "niji"},
    {"id": "v6.1", "object": "model", "owned_by": "niji"},
    {"id": "v7", "object": "model", "owned_by": "niji"},
]


def download(url: str, proxy: str | None) -> bytes:
    proxies = {"https": proxy, "http": proxy} if proxy else None
    r = requests.get(url, impersonate="safari17_0", proxies=proxies, timeout=120)
    r.raise_for_status()
    return r.content


def generate(req: dict, proxy: str | None) -> dict:
    """Run one generation request and return {urls, b64, prompt, job_id}."""
    client: NijiClient = STATE["client"]
    prompt = req.get("prompt") or req.get("input") or ""
    if not prompt:
        raise ValueError("prompt is required")

    model = req.get("model") or req.get("model_version")
    size = req.get("size")
    ar = req.get("aspect_ratio") or req.get("ar")
    params = req.get("parameters") or {}

    width = params.get("width")
    height = params.get("height")
    if not ar and width and height:
        ar = f"{int(width)}:{int(height)}"

    n = int(req.get("n") or params.get("n_samples") or 1)
    n = max(1, min(n, 4))

    built = client.build_prompt(
        prompt,
        model=model,
        size=size,
        aspect_ratio=ar,
        stylize=req.get("stylize") or params.get("scale"),
        style=req.get("style"),
        negative=req.get("negative_prompt") or params.get("negative_prompt") or params.get("uc"),
        seed=req.get("seed") if req.get("seed") is not None else params.get("seed"),
        chaos=req.get("chaos"),
        weird=req.get("weird"),
        hd=bool(req.get("hd")),
        image_prompts=req.get("image_prompts") or req.get("image_prompt") or [],
        style_refs=req.get("style_refs") or req.get("style_ref") or [],
        style_weight=req.get("style_weight"),
        character_refs=req.get("character_refs") or req.get("character_ref") or [],
        character_weight=req.get("character_weight"),
        omni_refs=req.get("omni_refs") or req.get("omni_ref") or [],
        omni_weight=req.get("omni_weight"),
        extra_flags=req.get("extra_flags", ""),
    )

    mode = req.get("mode", "fast")
    private = bool(req.get("private") or req.get("stealth") or False)

    urls: list[str] = []
    job_ids: list[str] = []
    for _ in range(n):
        with STATE["lock"]:
            res = client.submit(built, mode=mode, private=private)
            job_id = res.get("job_id") or res.get("id")
            if not job_id:
                ids = res.get("job_ids") or res.get("ids") or []
                job_id = str(ids[0]) if ids else None
            if not job_id:
                raise NijiError(500, f"submit returned no job id: {res}", res)
        job = client.wait(job_id)
        job_ids.append(job.id)
        if job.status != "completed":
            raise NijiError(502, f"job {job.id} ended with status {job.status}")
        urls.extend(job.image_urls())

    want_b64 = req.get("response_format") == "b64_json"
    out: dict = {"prompt": built, "job_ids": job_ids, "urls": urls}
    if want_b64:
        out["b64"] = [base64.b64encode(download(u, proxy)).decode() for u in urls]
    return out


class Handler(BaseHTTPRequestHandler):
    proxy: str | None = None
    server_version = "niji-local/0.1"

    def log_message(self, fmt, *args):
        print("[http]", self.address_string(), fmt % args, flush=True)

    # -- helpers -----------------------------------------------------------
    def _json(self, code: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.send_header("access-control-allow-origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        length = int(self.headers.get("content-length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except Exception:
            return {}

    # -- routes ------------------------------------------------------------
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/v1/models"):
            self._json(200, {"object": "list", "data": OPENAI_MODELS})
        elif self.path.startswith("/niji/usage"):
            try:
                self._json(200, STATE["client"].usage())
            except NijiError as exc:
                self._json(exc.status or 500, {"error": exc.message})
        elif self.path.startswith("/niji/session"):
            self._json(200, STATE["client"].session)
        else:
            self._json(404, {"error": "not_found"})

    def do_POST(self):  # noqa: N802
        try:
            body = self._body()
            if self.path.startswith("/v1/images/generations"):
                self._openai(body)
            elif self.path.startswith("/ai/generate-image"):
                self._novelai(body)
            else:
                self._json(404, {"error": "not_found"})
        except NijiError as exc:
            self._json(exc.status or 500, {"error": {"message": exc.message, "type": "niji_error"}})
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": {"message": str(exc), "type": "server_error"}})

    def _openai(self, body: dict) -> None:
        result = generate(body, self.proxy)
        data = []
        for i, url in enumerate(result["urls"]):
            item = {"revised_prompt": result["prompt"]}
            if "b64" in result:
                item["b64_json"] = result["b64"][i]
            else:
                item["url"] = url
            data.append(item)
        self._json(200, {"created": int(time.time()), "data": data})

    def _novelai(self, body: dict) -> None:
        result = generate(body, self.proxy)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            for i, url in enumerate(result["urls"]):
                blob = download(url, self.proxy)
                name = f"image_{i}_{uuid.uuid4().hex[:8]}.png"
                z.writestr(name, blob)
        blob = buf.getvalue()
        self.send_response(200)
        self.send_header("content-type", "application/zip")
        self.send_header("content-length", str(len(blob)))
        self.send_header("access-control-allow-origin", "*")
        self.end_headers()
        self.wfile.write(blob)


def main() -> int:
    ap = argparse.ArgumentParser(description="niji-local API server")
    ap.add_argument("--session", default=".niji_session.json")
    ap.add_argument("--proxy", default=None)
    ap.add_argument("--frida", action="store_true", help="use the official app as Play Integrity oracle")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    oracle = None
    integrity = None
    if args.frida:
        from niji_integrity import IntegrityOracle

        oracle = IntegrityOracle().connect()
        integrity = oracle.token
        STATE["oracle"] = oracle

    client = NijiClient.load(args.session, proxy=args.proxy, integrity=integrity)
    try:
        client.refresh()
    except NijiError:
        pass
    STATE["client"] = client

    Handler.proxy = args.proxy
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[*] niji-local listening on http://{args.host}:{args.port}", flush=True)
    print(f"[*] device_id={client.session.get('device_id')} frida={'on' if oracle else 'off'}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if oracle:
            oracle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
