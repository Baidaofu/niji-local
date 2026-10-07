package com.nijilocal.module;

import android.content.Context;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

import de.robv.android.xposed.XposedBridge;

/** Minimal HTTP server exposing OpenAI / NovelAI compatible endpoints. */
public final class LocalServer {

    public static final int PORT = 8199;
    private static volatile boolean started = false;

    private LocalServer() {}

    public static synchronized void start(final Context ctx, final ClassLoader cl) {
        if (started) return;
        started = true;
        final NijiApi api = new NijiApi(ctx, cl);
        Thread t = new Thread(() -> {
            try (ServerSocket server = new ServerSocket(PORT, 32, InetAddress.getByName("127.0.0.1"))) {
                XposedBridge.log("[niji-local] local API on http://127.0.0.1:" + PORT);
                ExecutorService pool = Executors.newCachedThreadPool();
                while (true) {
                    final Socket sock = server.accept();
                    pool.submit(() -> handle(api, sock));
                }
            } catch (Throwable e) {
                XposedBridge.log("[niji-local] server stopped: " + e);
            }
        }, "niji-local-http");
        t.setDaemon(true);
        t.start();
    }

    // ---- request handling --------------------------------------------------
    private static void handle(NijiApi api, Socket sock) {
        try (Socket s = sock) {
            InputStream in = s.getInputStream();
            ByteArrayOutputStream head = new ByteArrayOutputStream();
            int state = 0, b;
            while ((b = in.read()) != -1) {
                head.write(b);
                if (state == 0 && b == '\r') state = 1;
                else if (state == 1 && b == '\n') state = 2;
                else if (state == 2 && b == '\r') state = 3;
                else if (state == 3 && b == '\n') break;
                else state = (b == '\r') ? 1 : 0;
            }
            String headerText = head.toString("UTF-8");
            String[] lines = headerText.split("\r?\n");
            if (lines.length == 0 || lines[0].isEmpty()) return;
            String[] reqLine = lines[0].split(" ");
            String method = reqLine[0];
            String path = reqLine.length > 1 ? reqLine[1] : "/";
            int contentLength = 0;
            for (int i = 1; i < lines.length; i++) {
                int idx = lines[i].indexOf(':');
                if (idx > 0 && lines[i].substring(0, idx).trim().equalsIgnoreCase("content-length")) {
                    contentLength = Integer.parseInt(lines[i].substring(idx + 1).trim());
                }
            }
            byte[] bodyBytes = new byte[contentLength];
            int read = 0;
            while (read < contentLength) {
                int r = in.read(bodyBytes, read, contentLength - read);
                if (r < 0) break;
                read += r;
            }
            String body = new String(bodyBytes, StandardCharsets.UTF_8);

            if (path.startsWith("/v1/models")) {
                json(s, 200, models());
            } else if (path.startsWith("/v1/images/generations")) {
                openai(api, s, new JSONObject(body));
            } else if (path.startsWith("/ai/generate-image")) {
                novelai(api, s, new JSONObject(body));
            } else if (path.startsWith("/niji/usage")) {
                json(s, 200, api.call("GET", "/api/niji-app/user/usage", null, true));
            } else {
                json(s, 404, new JSONObject().put("error", "not_found"));
            }
        } catch (Throwable e) {
            XposedBridge.log("[niji-local] request error: " + e);
        }
    }

    private static JSONObject models() throws Exception {
        JSONArray data = new JSONArray();
        for (String id : new String[]{"niji-7", "niji-6", "niji-5", "niji-4", "midjourney", "v6.1", "v7"}) {
            data.put(new JSONObject().put("id", id).put("object", "model").put("owned_by", "niji"));
        }
        return new JSONObject().put("object", "list").put("data", data);
    }

    private static JSONObject generate(NijiApi api, JSONObject req) throws Exception {
        String prompt = req.optString("prompt", req.optString("input", ""));
        if (prompt.isEmpty()) throw new IllegalArgumentException("prompt is required");

        String model = req.optString("model", req.optString("model_version", null));
        String size = req.optString("size", null);
        String ar = req.optString("aspect_ratio", req.optString("ar", null));
        JSONObject params = req.optJSONObject("parameters") == null ? new JSONObject() : req.optJSONObject("parameters");
        int width = params.optInt("width", 0);
        int height = params.optInt("height", 0);
        if (ar == null && width > 0 && height > 0) ar = width + ":" + height;

        int n = req.optInt("n", params.optInt("n_samples", 1));
        n = Math.max(1, Math.min(n, 4));

        Integer stylize = null;
        if (req.has("stylize")) stylize = req.optInt("stylize", 0);
        else if (params.has("scale")) stylize = params.optInt("scale", 0);

        String negative = null;
        if (req.has("negative_prompt")) negative = req.optString("negative_prompt", "");
        else if (params.has("negative_prompt")) negative = params.optString("negative_prompt", "");
        else if (params.has("uc")) negative = params.optString("uc", "");

        Long seed = null;
        if (req.has("seed")) seed = req.optLong("seed", 0L);
        else if (params.has("seed")) seed = params.optLong("seed", 0L);

        Integer chaos = req.has("chaos") ? Integer.valueOf(req.optInt("chaos", 0)) : null;
        Integer weird = req.has("weird") ? Integer.valueOf(req.optInt("weird", 0)) : null;
        Integer styleWeight = req.has("style_weight") ? Integer.valueOf(req.optInt("style_weight", 0)) : null;
        Integer characterWeight = req.has("character_weight") ? Integer.valueOf(req.optInt("character_weight", 0)) : null;
        Integer omniWeight = req.has("omni_weight") ? Integer.valueOf(req.optInt("omni_weight", 0)) : null;

        String built = Prompt.build(
                prompt,
                model,
                size,
                ar,
                stylize,
                req.optString("style", null),
                (negative == null || negative.isEmpty()) ? null : negative,
                seed,
                chaos,
                weird,
                req.optBoolean("hd", false),
                toList(req.opt("image_prompts")),
                toList(req.opt("style_refs")),
                styleWeight,
                toList(req.opt("character_refs")),
                characterWeight,
                toList(req.opt("omni_refs")),
                omniWeight,
                req.optString("extra_flags", ""));

        String mode = req.optString("mode", "fast");
        boolean isPrivate = req.optBoolean("private", req.optBoolean("stealth", false));

        List<String> urls = new ArrayList<>();
        List<String> ids = new ArrayList<>();
        for (int i = 0; i < n; i++) {
            String jobId = api.submit(built, mode, isPrivate);
            ids.add(jobId);
            NijiApi.JobResult jr = api.wait(jobId, 300000);
            if (!"completed".equals(jr.status)) {
                throw new NijiApi.ApiException(502, "job " + jobId + " ended with status " + jr.status, null);
            }
            for (String u : NijiApi.imageUrls(jobId, jr.batchSize)) urls.add(u);
        }
        return new JSONObject()
                .put("prompt", built)
                .put("job_ids", new JSONArray(ids))
                .put("urls", new JSONArray(urls));
    }

    private static List<String> toList(Object o) {
        List<String> out = new ArrayList<>();
        if (o == null || o == JSONObject.NULL) return out;
        if (o instanceof JSONArray) {
            JSONArray a = (JSONArray) o;
            for (int i = 0; i < a.length(); i++) {
                String v = a.optString(i, null);
                if (v != null) out.add(v);
            }
        } else {
            String v = String.valueOf(o);
            if (!v.isEmpty()) out.add(v);
        }
        return out;
    }

    private static void openai(NijiApi api, Socket s, JSONObject req) throws Exception {
        JSONObject result = generate(api, req);
        JSONArray urls = result.getJSONArray("urls");
        JSONArray data = new JSONArray();
        for (int i = 0; i < urls.length(); i++) {
            JSONObject item = new JSONObject();
            item.put("url", urls.getString(i));
            item.put("revised_prompt", result.getString("prompt"));
            data.put(item);
        }
        json(s, 200, new JSONObject().put("created", System.currentTimeMillis() / 1000).put("data", data));
    }

    private static void novelai(NijiApi api, Socket s, JSONObject req) throws Exception {
        JSONObject result = generate(api, req);
        JSONArray urls = result.getJSONArray("urls");
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        try (ZipOutputStream zos = new ZipOutputStream(bos)) {
            for (int i = 0; i < urls.length(); i++) {
                zos.putNextEntry(new ZipEntry("image_" + i + ".png"));
                zos.write(api.download(urls.getString(i)));
                zos.closeEntry();
            }
        }
        byte[] blob = bos.toByteArray();
        s.getOutputStream().write(("HTTP/1.1 200 OK\r\ncontent-type: application/zip\r\n"
                + "content-length: " + blob.length + "\r\nconnection: close\r\n\r\n").getBytes(StandardCharsets.UTF_8));
        s.getOutputStream().write(blob);
        s.getOutputStream().flush();
    }

    private static void json(Socket s, int code, JSONObject payload) throws Exception {
        byte[] data = payload.toString().getBytes(StandardCharsets.UTF_8);
        OutputStream out = new BufferedOutputStream(s.getOutputStream());
        out.write(("HTTP/1.1 " + code + " OK\r\ncontent-type: application/json\r\n"
                + "content-length: " + data.length + "\r\nconnection: close\r\n\r\n").getBytes(StandardCharsets.UTF_8));
        out.write(data);
        out.flush();
    }
}
