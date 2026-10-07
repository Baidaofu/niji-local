# niji-local

An open-source, lightweight local API server and registration tool for the
`niji・journey` Android app (`com.spellbrush.nijijourney`, v1.44.0).

It talks to the app's own HTTP API (`nijijourney.com/api/niji-app/...`) using the
same device-based guest account that the official app creates. It exposes the
generation service through:

* an **OpenAI-compatible** image endpoint (`POST /v1/images/generations`, `GET /v1/models`)
* a **NovelAI-compatible** image endpoint (`POST /ai/generate-image`)

This README documents the reverse-engineering results so the project can be
maintained without re-doing the work.

---

## 1. What the app actually does

The client is an Expo / React Native app. The JS logic ships as **Hermes
bytecode v98** (`assets/index.android.bundle`). The device-specific and
Play-Integrity logic lives in a native Expo module `NijiIntegrity`
(`expo/modules/nijiintegrity/NijiIntegrityModule`).

### 1.1 Guest / trial registration

On "continue as guest" the app runs:

```
1. device_id = Settings.Secure.getString(cr, "android_id")          # NijiIntegrity.getDeviceID()
2. POST /api/niji-app/auth/integrity-token?app_version=1.44.0
      {"method":"google_device","sub": device_id}
   -> {"token": "<server-signed nonce JWT>"}
3. Play Integrity:
      StandardIntegrityManager.prepareIntegrityToken(cloudProjectNumber = 624942467596)
      provider.request(requestHash = token)
      (fallback) IntegrityManager.requestIntegrityToken(nonce = b64(token), cloudProjectNumber)
   -> <Play Integrity attestation JWT>
4. POST /api/niji-app/auth/google-device?app_version=1.44.0
      {"device_id": device_id,
       "only_use_receipt": false,
       "integrity_token": <attestation JWT or null>,
       "key_id": null,
       "client_data": <nonce JWT>,
       "receipt": null}
   -> {"message":"success","idp_token":"<firebase custom token>",
       "did_integrity_succeed": true|false, "is_new_user": true|false,
       "new_device_id": "<optional>"}
5. Firebase Auth REST:
      POST https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken
           ?key=AIzaSyBzVBIfh_wNNScBDcNeuKd4cyspiR8qrMo
      {"token": idp_token, "returnSecureToken": true}
   -> {"idToken": ..., "refreshToken": ...}
6. GET  /api/niji-app/auth/session              (Bearer idToken)
7. POST /api/niji-app/user/accept-tos
8. POST /api/niji-app/user/ensure-trial-generations
   -> {"message":"success","amount":20}   (20 trial generations)
```

Key facts:

* `device_id` is the **Android ID as seen by the official app** (on Android 8+
  this is scoped to the app signing key, so a third-party APK gets a different
  value unless the value is read from root or the official app process).
* The account is bound to `device_id`, so re-running with the same id returns the
  same account (`is_new_user:false`).
* `did_integrity_succeed` is `true` only when the server can validate a real
  Play Integrity attestation. With a missing / forged token the account is still
  created but `ensure-trial-generations` returns
  `400 {"message":"failed_to_grant"}`. **A valid Play Integrity token is required
  to receive the 20 trial generations.**

### 1.2 Generation

```
POST /api/niji-app/job/submit?app_version=1.44.0
     {"jobs":[ { "channelName":"Home Workspace",
                 "flags":{"mode":"fast"|"relaxed"|"turbo","private":bool},
                 "prompt":"<text prompt + midjourney flags>",
                 "parentJob":{"jobId":..,"imageNum":..} | null,
                 "isMobile":true,
                 ...params } ]}
  -> 400 {"message":"need_integrity_token","nonce":"<base64 nonce>"}
     then retry the same body with "integrity_token": <Play Integrity token>
  -> 200 {"message":"success","job_ids":[...]}

POST /api/niji-app/job/status?app_version=1.44.0   {"job_ids":[...]}
POST /api/niji-app/job/queue?app_version=1.44.0
GET  /api/niji-app/user/usage
```

The `prompt` field is a Midjourney command line: text plus flags such as
`--ar 3:2`, `--niji 6`, `--v 7`, `--stylize 250`, `--style expressive`,
`--sref <url>`, `--cref <url>`, `--no a,b`. Image prompts are placed in front of
the text as `https://...png::<weight>`.

The submit body is a **single job object** (not wrapped in `jobs`), and the
Play Integrity attestation is the top-level `token` object:

```json
{"channelName":"Home Workspace","jobType":"imagine",
 "flags":{"mode":"fast","private":false},
 "prompt":"a red apple --ar 1:1 --niji 6","isMobile":true,
 "token":{"type":"android","data":"<Play Integrity JWT>"}}
```

The success response is the job itself:
`{"job_id":"<uuid>","type":"v6_diffusion_anime","meta":{"width":1024,
"height":1024,"batch_size":4},...}`.

Image URLs are built as:

```
https://cdn.midjourney.com/<job_id>/0_<grid_index>.png
https://cdn.midjourney.com/<job_id>_grid_0.webp   (grid preview)
```

Other endpoints: `/api/mobile/upload-file`, `/api/mobile/upload-mask`,
`wss://ws.nijijourney.com/ws` (websocket_token from the session).

---

## 1.2.1 What was verified live (2026-10, app 1.44.0)

Confirmed against the real server through a non-CN exit:

* the whole guest flow: device id -> nonce -> Play Integrity -> `/auth/google-device`
  (`did_integrity_succeed:true`) -> Firebase -> `/auth/session` -> `ensure-trial-generations`
  -> `num_free_generations: 20`
* `POST /api/niji-app/job/submit` with the job object + `token:{type:"android",data:<PI>}`
  -> job object with `job_id`; `usage_lifetime_count` increments
* `POST /api/niji-app/job/status` `{"job_ids":[...]}` -> `{"message":"success","jobs":[...]}`
  with `current_status` and `event.batchSize`/`event.width`/`event.height`
* images download from `https://cdn.midjourney.com/<job_id>/0_<i>.png`

A Play Integrity token is required for the trial grant **and** for every
`job/submit`. The Frida oracle (`niji_integrity.py`) or the LSPosed module obtain
them from the official app process.`ensure-trial-generations` also returned
`network_error` while the exit node could not reach Google's services and
`failed_to_grant` for accounts first created without integrity, so do the first
login with a working integrity token.

### 1.3 Models / flags

Model versions (from `decodeSingle(full_command, 'niji 6')` defaults and the
model enum): `niji 4`, `niji 5`, `niji 6`, `niji 7`, `1`,`2`,`3`,`4`,`5`,`5.1`,
`5.2`,`6`,`6.1`,`7`,`8`,`8.1`,`8.2`, `video 1`.

### 1.4 Cloudflare

`nijijourney.com` is behind Cloudflare bot management. Plain `curl` / Node TLS
gets a managed challenge (`HTTP 403`, `cf-mitigated: challenge`). A real browser
TLS fingerprint passes:

* `curl_cffi` with `impersonate="chrome"` / `"safari*"` and a Safari user agent
  passes. (`impersonate` alone with a Chrome UA may still be challenged; the
  working combination observed is a Chrome/Safari fingerprint + Safari UA.)

The server uses `curl_cffi`.

### 1.5 The Play Integrity problem

`job/submit` returns `need_integrity_token` on every request and the trial grant
needs `did_integrity_succeed:true`. Play Integrity attestations are signed by
Google and bound to the **requesting package** (`com.spellbrush.nijijourney`)
and cloud project `624942467596`. A standalone third-party APK cannot mint a
token for that package/project.

Therefore this project obtains Play Integrity tokens by attaching to the
**installed official app process** with Frida (`niji_integrity.js`) and calling
the app's own Play Integrity APIs. The official app is only used as a token
oracle; its UI is never used. If your device is stock (passes
`MEETS_DEVICE_INTEGRITY`) and the official app can register the trial, this
works.

---

## 2. Requirements

* Windows / Linux / macOS with Python 3.10+
* `adb` with the phone connected and a working non-CN proxy on the phone
* the official `niji・journey` app installed (Play Integrity oracle)
* root + `frida-server` on the phone (for the integrity oracle), **or** you
  already have a valid Play Integrity attestation

Install:

```
pip install -r requirements.txt
```

`frida-server` must match the host `frida` version. Push and run it as root:

```
adb push frida-server /data/local/tmp/frida-server
adb shell su -c 'chmod 755 /data/local/tmp/frida-server'
adb shell su -c '/data/local/tmp/frida-server -D &'
```

Expose the phone proxy to the host (the project connects through it so the exit
IP stays non-CN):

```
adb forward tcp:12080 tcp:2080     # 2080 = sing-box/clash mixed/socks inbound
```

---

## 3. Usage

### 3.1 Read the device parameters

```
# real Android ID (the official app may report a different, app-scoped value)
adb shell settings get secure android_id

# the app-scoped value: attach and call NijiIntegrity/getDeviceID, or read it
# from the app process (see niji_integrity.py --devid)
python niji_integrity.py --devid
```

### 3.2 Register the trial

```
python register.py --device-id <android_id> --proxy socks5h://127.0.0.1:12080
```

With the Frida oracle (official app running):

```
python register.py --device-id <android_id> --frida --proxy socks5h://127.0.0.1:12080
```

Session is written to `.niji_session.json`.

### 3.3 Run the local API

```
python niji_server.py --proxy socks5h://127.0.0.1:12080 --frida --port 8000
```

Endpoints:

```
GET  /v1/models
POST /v1/images/generations        # OpenAI
POST /ai/generate-image            # NovelAI (returns zip)
GET  /niji/usage
GET  /niji/session
```

OpenAI example:

```
curl http://127.0.0.1:8000/v1/images/generations \
  -H 'content-type: application/json' \
  -d '{"prompt":"1girl, silver hair, cafe, cinematic","model":"niji-6",
       "size":"1024x1024","n":1,"response_format":"url"}'
```

NovelAI example:

```
curl http://127.0.0.1:8000/ai/generate-image \
  -H 'content-type: application/json' \
  -d '{"input":"1girl, silver hair, cafe","model":"niji-6",
       "parameters":{"width":832,"height":1216,"n_samples":1}}' \
  -o out.zip
```

---

## 4. Supported generation parameters

| concept | how it is sent |
|---|---|
| prompt | `prompt` text |
| model | `--niji 4/5/6/7`, `--v 6/6.1/7/8/8.1` |
| size / aspect ratio | `--ar W:H` |
| stylize | `--stylize 0..1000` |
| style | `--style raw / expressive / cute / scenic / original` |
| negative prompt | `--no item1,item2` |
| seed | `--seed N` |
| chaos / weird | `--chaos N`, `--weird N` |
| image prompt (reference) | `https://...png::<weight>` before the text, or NovelAI `parameters.reference_image` |
| style reference | `--sref <url_or_code>` `--sw 0..1000` |
| character reference | `--cref <url>` `--cw 0..100` |
| omni reference | `--oref <url>` `--ow 0..1000` |
| HD | `--hd` |
| batch / count | `n` submits `n` jobs (each job returns a grid) |
| mode | `flags.mode` = `fast` \| `relaxed` \| `turbo` |
| visibility | `flags.private` |

---

## 5. Files

```
niji_client.py       API client (auth, session, submit, poll, image URL)
niji_integrity.js    Frida agent: Play Integrity tokens + NijiIntegrity.getDeviceID
niji_integrity.py    Frida host driver
register.py          device trial registration CLI
niji_server.py       OpenAI / NovelAI compatible local API
```
