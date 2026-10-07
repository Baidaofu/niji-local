# niji-local

A lightweight, open-source local image-generation API for the
`niji・journey` Android app (`com.spellbrush.nijijourney`, v1.44.0), plus an
LSPosed module that runs it inside the official app process.

It exposes the same generation backend through:

* an **OpenAI-compatible** endpoint (`POST /v1/images/generations`, `GET /v1/models`)
* a **NovelAI-compatible** endpoint (`POST /ai/generate-image`, returns a zip)

No bulky third-party UI, no device spoofing, no infinite farming: it uses the
device parameters and the official guest trial exactly like the real client.

## Components

| path | what it is |
|---|---|
| [`lsposed/`](lsposed/) | **Recommended.** LSPosed module. Runs the API inside the official app process, so it reuses the app's Android ID, Firebase session, Play Integrity token and network stack. |
| [`python/`](python/) | Standalone Python client / local server / Frida Play-Integrity oracle. Useful for debugging and for driving the API from a PC. |

## Quick start (LSPosed)

```
gradle :app:assembleDebug          # or use the prebuilt APK / CI artifact
adb install -r app-debug.apk
```

Enable **niji-local** in the LSPosed manager, set its scope to
`com.spellbrush.nijijourney`, restart the app, then:

```
adb forward tcp:8199 tcp:8199
curl http://127.0.0.1:8199/v1/models
curl http://127.0.0.1:8199/v1/images/generations \
  -H 'content-type: application/json' \
  -d '{"prompt":"a small green cactus in a clay pot, studio light",
       "model":"niji-6","size":"1024x1024","n":1}'
```

## Third-party clients

### Kelivo (on the phone)

Kelivo's OpenAI Images provider posts to `{baseUrl}/images/generations` with
`{model, prompt}` and accepts `data[].url` or `data[].b64_json`. It can talk to
the module directly:

1. Add a custom OpenAI-compatible provider: base URL
   `http://127.0.0.1:8199/v1`, any API key.
2. Fetch models (they come from `/v1/models`), or add one manually.
3. Edit the model spec and set its type to **image**.
4. Generate from a chat/tool.

### TauriTavern (on the PC)

Use `adb forward tcp:8199 tcp:8199` first.

* **Stable Diffusion → source `OpenAI`**: set the OpenAI base URL to
  `http://127.0.0.1:8199/v1` and pick a model such as `gpt-image-1`/`dall-e-3`
  (the model is only a hint; the server maps unknown names to the default niji
  model). The server replies with `b64_json`, which TauriTavern expects.
* **Stable Diffusion → source `A1111`/`Forge`**: set the SD WebUI URL to
  `http://127.0.0.1:8199`, then pick one of the checkpoints listed by
  `/sdapi/v1/sd-models` (`niji-6`, `niji-7`, ...).

A1111-compatible endpoints implemented: `/sdapi/v1/txt2img`,
`/sdapi/v1/img2img`, `/sdapi/v1/sd-models`, `/sdapi/v1/options`,
`/sdapi/v1/samplers`, `/sdapi/v1/schedulers`, `/sdapi/v1/upscalers`,
`/sdapi/v1/progress`, `/sdapi/v1/interrupt`.

## Supported generation parameters

`prompt` / `input`, `model` (`niji-7/6/5/4`, `midjourney`, `v6.1`, `v7`),
`size` / `aspect_ratio`, `n`, `stylize`, `style`, `negative_prompt`, `seed`,
`chaos`, `weird`, `image_prompts`, `style_refs` + `style_weight`,
`character_refs` + `character_weight`, `omni_refs` + `omni_weight`, `hd`,
`mode` (`fast`/`relaxed`/`turbo`), `private`.

## How it works (reverse-engineering notes)

The app is Expo / React Native with Hermes v98 bytecode and a native Expo module
`NijiIntegrity`. The guest/trial flow is:

```
device_id = Settings.Secure.getString(cr, "android_id")
POST /api/niji-app/auth/integrity-token  {"method":"google_device","sub":device_id}
   -> {"token": "<server nonce JWT>"}
Play Integrity (cloudProjectNumber = 624942467596)
POST /api/niji-app/auth/google-device    {"device_id", "integrity_token",
                                          "client_data": nonce, ...}
   -> {"idp_token": <firebase custom token>, "did_integrity_succeed": true}
POST identitytoolkit .../signInWithCustomToken
GET  /api/niji-app/auth/session
POST /api/niji-app/user/accept-tos
POST /api/niji-app/user/ensure-trial-generations   -> 20 trial generations
```

Generation:

```
POST /api/niji-app/job/submit
{"channelName":"Home Workspace","jobType":"imagine",
 "flags":{"mode":"fast","private":false},
 "prompt":"... --ar 1:1 --niji 6","isMobile":true,
 "token":{"type":"android","data":"<Play Integrity JWT>"}}
   -> {"job_id":"<uuid>","type":"v6_diffusion_anime",
       "meta":{"width":1024,"height":1024,"batch_size":4}}
POST /api/niji-app/job/status    {"job_ids":[...]}
image: https://cdn.midjourney.com/<job_id>/0_<index>.png
```

Cloudflare bot management in front of `nijijourney.com` can be passed with a
browser TLS fingerprint (`curl_cffi` with a Safari/Chrome impersonation); the
LSPosed module sidesteps it entirely because it uses the app's own stack.

A valid Play Integrity token is required for the trial grant **and** for every
`job/submit`. It is signed by Google and bound to the app package, which is why
the LSPosed module runs inside the official app.

Full notes: [`python/README.md`](python/README.md).

## Build

The Android module is built by GitHub Actions on every push; the APK is uploaded
as the `niji-local-module` artifact. Locally:

```
cd lsposed
./gradlew assembleDebug
```

## Disclaimer

For personal use with your own device and account. Respect the service's terms
of service.
