# niji-local — LSPosed module

Runs a local **OpenAI / NovelAI compatible image API** inside the official
`niji・journey` app process. The official UI is never used; the app is only the
host, because it already has

* the app-scoped Android ID,
* the Firebase session (authjourney project),
* the Play Integrity token (cloud project `624942467596`),
* and the same network stack that Cloudflare accepts.

So no device spoofing, no certificate bypass, no Cloudflare workaround.

## Build

```
gradle :app:assembleDebug
# -> app/build/outputs/apk/debug/app-debug.apk
```

Requires JDK 17, Android SDK 35, and the Xposed API
(`https://api.xposed.info/`). The prebuilt APK is `niji-local-module.apk`.

## Install / enable

```
adb install -r niji-local-module.apk
```

Then open the LSPosed manager, enable **niji-local**, set its scope to
**niji・journey** (`com.spellbrush.nijijourney`), and restart the app. If you use
KernelSU, use its LSPosed entry.

The module starts the server when the app process starts. You should see in
logcat:

```
[niji-local] loading in com.spellbrush.nijijourney
[niji-local] local API on http://127.0.0.1:8199
```

## Use

The server listens on `127.0.0.1:8199` inside the phone. From the PC:

```
adb forward tcp:8199 tcp:8199
curl http://127.0.0.1:8199/v1/models
```

OpenAI:

```
curl http://127.0.0.1:8199/v1/images/generations \
  -H 'content-type: application/json' \
  -d '{"prompt":"a small green cactus in a clay pot, studio light",
       "model":"niji-6","size":"1024x1024","n":1}'
```

NovelAI (returns a zip of PNGs):

```
curl http://127.0.0.1:8199/ai/generate-image \
  -H 'content-type: application/json' \
  -d '{"input":"a tiny orange fox, watercolor","model":"niji-6",
       "parameters":{"width":1024,"height":1024,"n_samples":1}}' \
  -o out.zip
```

Other endpoints:

```
GET  /v1/models                          OpenAI model list
GET  /niji/usage                         trial usage
POST /sdapi/v1/txt2img                   A1111 / Forge (returns {images:[b64]})
POST /sdapi/v1/img2img
GET  /sdapi/v1/sd-models                 checkpoint list (niji-6, niji-7, ...)
GET  /sdapi/v1/options
GET  /sdapi/v1/samplers|schedulers|upscalers
GET  /sdapi/v1/progress
POST /sdapi/v1/interrupt
```

`/v1/images/generations` honours `"response_format": "b64_json"` (returns
base64, which SillyTavern/TauriTavern expect) and defaults to `url`.
Unknown model names (`dall-e-3`, `gpt-image-1`, ...) are ignored and the default
niji model is used, so OpenAI-image clients work without tweaking the model.

## Compatible clients

* **Kelivo** (Android): custom OpenAI provider, base URL
  `http://127.0.0.1:8199/v1`; set the model spec type to `image`.
* **TauriTavern** (desktop, `adb forward tcp:8199 tcp:8199`):
  * SD source `OpenAI` with base URL `http://127.0.0.1:8199/v1`
  * SD source `A1111`/`Forge` with URL `http://127.0.0.1:8199`

## Parameters

| field | meaning |
|---|---|
| `prompt` / `input` | text prompt |
| `model` / `model_version` | `niji-7`, `niji-6`, `niji-5`, `niji-4`, `midjourney`, `v6.1`, `v7` |
| `size` | e.g. `1024x1024`, `1216x832` (mapped to `--ar`) |
| `aspect_ratio` / `ar` | e.g. `16:9` |
| `n` / `parameters.n_samples` | number of jobs (1–4) |
| `stylize` / `parameters.scale` | `--stylize N` |
| `style` | `raw`, `expressive`, `cute`, `scenic`, `original` |
| `negative_prompt` / `parameters.uc` | `--no ...` |
| `seed` | `--seed N` |
| `chaos`, `weird` | `--chaos N`, `--weird N` |
| `image_prompts` | list of image URLs (image prompt) |
| `style_refs` + `style_weight` | `--sref` / `--sw` |
| `character_refs` + `character_weight` | `--cref` / `--cw` |
| `omni_refs` + `omni_weight` | `--oref` / `--ow` |
| `hd` | `--hd` |
| `mode` | `fast` / `relaxed` / `turbo` |
| `private` / `stealth` | stealth mode |
| `extra_flags` | raw midjourney flags appended to the prompt |

Image URLs returned are
`https://cdn.midjourney.com/<job_id>/0_<grid_index>.png` (4 per job).

## Notes

* The trial account and its generations belong to the app install; use the
  official guest login once (or the `niji-local` Python tool) so the app has a
  signed-in user. The module then reuses it.
* The module only reads the Firebase user, it never changes app data.
* Binding is `127.0.0.1` only. Expose it with `adb forward` (or change `PORT`
  binding in `LocalServer.java` if you need LAN access).
