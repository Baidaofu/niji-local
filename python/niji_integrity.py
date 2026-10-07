"""Host-side driver for the Frida Play Integrity agent.

The agent runs inside the official niji・journey process and exposes
``deviceid``, ``prepare``, ``standard``, ``classic`` and ``token`` RPCs.
"""
from __future__ import annotations

import os
import subprocess
import time

import frida

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = "com.spellbrush.nijijourney"


def _adb(*args: str) -> str:
    adb = os.environ.get("ADB", "adb")
    try:
        out = subprocess.run([adb, *args], capture_output=True, text=True, timeout=20)
        return out.stdout.strip()
    except Exception:
        return ""


def build_bundle() -> str:
    """Concatenate the Java bridge and the agent into one loadable script."""
    bridge = os.path.join(HERE, "java_bridge.js")
    agent = os.path.join(HERE, "niji_integrity.js")
    target = os.path.join(HERE, "niji_integrity.bundle.js")
    if not os.path.exists(target) or (
        os.path.getmtime(target) < max(os.path.getmtime(bridge), os.path.getmtime(agent))
    ):
        with open(target, "w", encoding="utf-8") as out:
            with open(bridge, "r", encoding="utf-8") as f:
                out.write(f.read())
            out.write("\n;\n")
            with open(agent, "r", encoding="utf-8") as f:
                out.write(f.read())
    return target


class IntegrityOracle:
    """Attach to the official app and serve Play Integrity tokens."""

    def __init__(self, pkg: str = PKG, pid: int | None = None, timeout: float = 90.0):
        self.pkg = pkg
        self.pid = pid
        self.timeout = timeout
        self.session = None
        self.script = None
        self._prepared = False

    # -- lifecycle ---------------------------------------------------------
    def connect(self) -> "IntegrityOracle":
        if self.pid is None:
            pid_text = _adb("shell", "pidof", self.pkg)
            if not pid_text:
                raise RuntimeError(
                    f"{self.pkg} is not running; open it once (or let register.py spawn it)"
                )
            self.pid = int(pid_text.split()[0])
        dev = frida.get_usb_device(timeout=10)
        self.session = dev.attach(self.pid)
        with open(build_bundle(), "r", encoding="utf-8") as f:
            source = f.read()
        self.script = self.session.create_script(source)
        self.script.load()
        return self

    def close(self) -> None:
        try:
            if self.session:
                self.session.detach()
        except Exception:
            pass

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    # -- rpc ---------------------------------------------------------------
    def device_id(self) -> str:
        return self.script.exports_sync.deviceid()

    def token(self, nonce: str) -> str:
        if not self._prepared:
            try:
                self.script.exports_sync.prepare()
                self._prepared = True
            except Exception:
                self._prepared = False
        return self.script.exports_sync.token(nonce)


def parse_device_id_from_apk() -> str | None:
    """Fallback: read the app-scoped Android ID straight from the app if it is
    debuggable or if we have root. Returns None on failure."""
    out = _adb("shell", "su", "-c", "cat /data/system/users/0/settings_secure.xml 2>/dev/null")
    if out and "android_id" in out:
        import re

        m = re.search(r'name="android_id"[^>]*value="([0-9a-f]+)"', out)
        if m:
            return m.group(1)
    return None


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="niji-local integrity helper")
    ap.add_argument("--devid", action="store_true", help="print the app-scoped Android ID")
    ap.add_argument("--check", action="store_true", help="request a test token")
    ap.add_argument("--pkg", default=PKG)
    args = ap.parse_args()

    with IntegrityOracle(args.pkg) as oracle:
        if args.devid or not args.check:
            print(oracle.device_id())
        if args.check:
            nonce = _adb("shell", "echo", "dGVzdA==") or "dGVzdA=="
            print(oracle.token(nonce)[:80])
