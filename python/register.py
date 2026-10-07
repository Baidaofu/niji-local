"""Register / refresh the device-based guest trial account.

Examples
--------
Read the app-scoped Android ID from the running official app and register with
Play Integrity tokens obtained from that same process::

    python register.py --frida --proxy socks5h://127.0.0.1:12080

Use an explicit device id and an externally supplied attestation::

    python register.py --device-id 8c06f94a20dece2c --integrity-token <jwt> \
        --proxy socks5h://127.0.0.1:12080
"""
from __future__ import annotations

import argparse
import sys

from niji_client import NijiClient, NijiError, new_device_id


def main() -> int:
    ap = argparse.ArgumentParser(description="niji・journey trial registration")
    ap.add_argument("--device-id", default=None, help="Android ID used as the device subject")
    ap.add_argument("--proxy", default=None, help="e.g. socks5h://127.0.0.1:12080")
    ap.add_argument("--frida", action="store_true", help="obtain Play Integrity tokens from the official app")
    ap.add_argument("--integrity-token", default=None, help="pre-obtained Play Integrity attestation")
    ap.add_argument("--output", default=".niji_session.json")
    args = ap.parse_args()

    oracle = None
    integrity = None
    device_id = args.device_id

    if args.frida:
        from niji_integrity import IntegrityOracle

        oracle = IntegrityOracle()
        try:
            oracle.connect()
        except Exception as exc:
            print(f"[!] could not attach to the official app: {exc}", file=sys.stderr)
            return 2
        integrity = oracle.token
        if not device_id:
            device_id = oracle.device_id()
            print(f"[*] app-scoped android_id = {device_id}")

    if not device_id:
        device_id = new_device_id()
        print(f"[*] no device id supplied, generated {device_id}")

    client = NijiClient(proxy=args.proxy, integrity=integrity)

    if args.integrity_token:
        nonce = client.request_nonce(device_id)
        client.integrity = lambda _n: args.integrity_token
        # device_login re-requests a nonce; keep the externally provided token
        try:
            j = client._json(
                "POST",
                "/api/niji-app/auth/google-device",
                {
                    "device_id": device_id,
                    "only_use_receipt": False,
                    "integrity_token": args.integrity_token,
                    "key_id": None,
                    "client_data": nonce,
                    "receipt": None,
                },
                auth=False,
            )
            client.session["device_id"] = device_id
            if j.get("idp_token"):
                client.firebase_signin(j["idp_token"])
            client.fetch_session()
            try:
                client.accept_tos()
            except NijiError:
                pass
            result = client.ensure_trial_generations()
        except NijiError as exc:
            print(f"[!] {exc}", file=sys.stderr)
            if exc.body:
                print(exc.body, file=sys.stderr)
            return 1
    else:
        try:
            result = client.register(device_id)
        except NijiError as exc:
            print(f"[!] {exc}", file=sys.stderr)
            if exc.body:
                print(exc.body, file=sys.stderr)
            return 1

    client.save(args.output)
    print(f"[*] session saved to {args.output}")
    if result.get("error"):
        print(f"[!] trial grant refused: {result.get('message')}")
        print("[!] the account exists but has no free generations; a valid Play Integrity")
        print("[!] attestation is required (use --frida on a device that passes integrity).")
    else:
        print(f"[*] ensure-trial-generations -> {result}")

    try:
        usage = client.usage()
        print(f"[*] usage -> {usage.get('usage')}")
    except NijiError:
        pass

    if oracle:
        oracle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
