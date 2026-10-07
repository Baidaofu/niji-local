package com.nijilocal.module;

import android.content.Context;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.lang.reflect.Method;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/** niji・journey API client that runs inside the official app process. */
public final class NijiApi {

    public static final String BASE = "https://nijijourney.com";
    public static final String CDN = "https://cdn.midjourney.com";
    public static final String APP_VERSION = "1.44.0";

    private final Context ctx;
    private final ClassLoader cl;
    private volatile String idToken;

    public NijiApi(Context ctx, ClassLoader cl) {
        this.ctx = ctx.getApplicationContext();
        this.cl = cl;
    }

    // ---- Firebase ---------------------------------------------------------
    public synchronized String idToken() throws Exception {
        if (idToken != null) return idToken;
        Class<?> appCls = Class.forName("com.google.firebase.FirebaseApp", false, cl);
        Class<?> authCls = Class.forName("com.google.firebase.auth.FirebaseAuth", false, cl);

        Object user = null;
        try {
            java.util.List<?> apps = (java.util.List<?>) appCls.getMethod("getApps", Context.class).invoke(null, ctx);
            for (Object app : apps) {
                Object auth = authCls.getMethod("getInstance", appCls).invoke(null, app);
                Object u = authCls.getMethod("getCurrentUser").invoke(auth);
                if (u != null) { user = u; break; }
            }
        } catch (Throwable ignored) {
        }
        if (user == null) {
            Object auth = authCls.getMethod("getInstance").invoke(null);
            user = authCls.getMethod("getCurrentUser").invoke(auth);
        }
        if (user == null) {
            de.robv.android.xposed.XposedBridge.log("[niji-local] no Firebase user available yet");
            return null;
        }
        Object task = user.getClass().getMethod("getIdToken", boolean.class).invoke(user, false);
        Object result = Integrity.await(cl, task);
        idToken = (String) result.getClass().getMethod("getToken").invoke(result);
        return idToken;
    }

    // ---- HTTP -------------------------------------------------------------
    private HttpURLConnection open(String method, String path, boolean auth) throws Exception {
        String url = path.startsWith("http") ? path : BASE + path;
        if (!url.contains("app_version=")) {
            url += (url.contains("?") ? "&" : "?") + "app_version=" + APP_VERSION;
        }
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setRequestMethod(method);
        c.setConnectTimeout(30000);
        c.setReadTimeout(120000);
        c.setRequestProperty("x-app-version", APP_VERSION);
        c.setRequestProperty("x-csrf-protection", "1");
        c.setRequestProperty("content-type", "application/json");
        if (auth) {
            String t = idToken();
            if (t != null) c.setRequestProperty("authorization", "Bearer " + t);
        }
        return c;
    }

    private static byte[] readAll(InputStream in) throws Exception {
        if (in == null) return new byte[0];
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        byte[] buf = new byte[16384];
        int n;
        while ((n = in.read(buf)) > 0) bos.write(buf, 0, n);
        return bos.toByteArray();
    }

    public JSONObject call(String method, String path, JSONObject body, boolean auth) throws Exception {
        HttpURLConnection c = open(method, path, auth);
        if (body != null) {
            c.setDoOutput(true);
            try (OutputStream os = c.getOutputStream()) {
                os.write(body.toString().getBytes(StandardCharsets.UTF_8));
            }
        }
        int code = c.getResponseCode();
        InputStream in = code >= 400 ? c.getErrorStream() : c.getInputStream();
        String text = new String(readAll(in), StandardCharsets.UTF_8);
        JSONObject json = text.isEmpty() ? new JSONObject() : new JSONObject(text);
        if (code == 401 && auth && idToken != null) {
            idToken = null; // force a refresh and retry once
            return call(method, path, body, true);
        }
        if (code >= 400) throw new ApiException(code, json.optString("message", text), json);
        return json;
    }

    public byte[] download(String url) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(30000);
        c.setReadTimeout(120000);
        return readAll(c.getInputStream());
    }

    // ---- generation -------------------------------------------------------
    public String submit(String prompt, String mode, boolean isPrivate) throws Exception {
        JSONObject job = new JSONObject();
        job.put("channelName", "Home Workspace");
        job.put("jobType", "imagine");
        JSONObject flags = new JSONObject();
        flags.put("mode", mode == null ? "fast" : mode);
        flags.put("private", isPrivate);
        job.put("flags", flags);
        job.put("prompt", prompt);
        job.put("isMobile", true);

        try {
            JSONObject res = call("POST", "/api/niji-app/job/submit", job, true);
            return res.getString("job_id");
        } catch (ApiException e) {
            if (e.code == 400 && "need_integrity_token".equals(e.message)) {
                String nonce = e.body.optString("nonce", null);
                if (nonce == null) throw e;
                String token = Integrity.token(ctx, nonce);
                JSONObject tok = new JSONObject();
                tok.put("type", "android");
                tok.put("data", token);
                job.put("token", tok);
                JSONObject res = call("POST", "/api/niji-app/job/submit", job, true);
                return res.getString("job_id");
            }
            throw e;
        }
    }

    public JSONObject jobStatus(String jobId) throws Exception {
        JSONObject body = new JSONObject();
        body.put("job_ids", new JSONArray().put(jobId));
        return call("POST", "/api/niji-app/job/status", body, true);
    }

    public static final class JobResult {
        public String status;
        public int batchSize = 4;
        public int width;
        public int height;
    }

    public JobResult wait(String jobId, long timeoutMs) throws Exception {
        long deadline = System.currentTimeMillis() + timeoutMs;
        JobResult last = new JobResult();
        while (System.currentTimeMillis() < deadline) {
            JSONObject res = jobStatus(jobId);
            JSONArray jobs = res.optJSONArray("jobs");
            if (jobs != null) {
                for (int i = 0; i < jobs.length(); i++) {
                    JSONObject j = jobs.optJSONObject(i);
                    if (j == null || !jobId.equals(j.optString("id"))) continue;
                    last.status = j.optString("current_status", "running");
                    JSONObject ev = j.optJSONObject("event");
                    if (ev != null) {
                        last.batchSize = ev.optInt("batchSize", 4);
                        last.width = ev.optInt("width", 0);
                        last.height = ev.optInt("height", 0);
                    }
                    if ("completed".equals(last.status) || "error".equals(last.status)
                            || "canceled".equals(last.status) || "interrupted".equals(last.status)) {
                        return last;
                    }
                }
            }
            Thread.sleep(4000);
        }
        return last;
    }

    public static String[] imageUrls(String jobId, int batch) {
        String[] urls = new String[Math.max(1, batch)];
        for (int i = 0; i < urls.length; i++) {
            urls[i] = CDN + "/" + jobId + "/0_" + i + ".png";
        }
        return urls;
    }

    public static final class ApiException extends Exception {
        public final int code;
        public final String message;
        public final JSONObject body;

        ApiException(int code, String message, JSONObject body) {
            super("HTTP " + code + ": " + message);
            this.code = code;
            this.message = message;
            this.body = body == null ? new JSONObject() : body;
        }
    }
}
