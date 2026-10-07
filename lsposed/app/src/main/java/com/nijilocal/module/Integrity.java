package com.nijilocal.module;

import android.content.Context;
import android.util.Base64;

import java.lang.reflect.Method;
import java.util.concurrent.TimeUnit;

/**
 * Play Integrity helper, reflective so it does not need compile-time access to
 * the host app's Play Core classes.
 *
 * Uses the Standard Integrity API first (same as NijiIntegrityModule) and falls
 * back to the Classic Integrity API.
 */
public final class Integrity {

    public static final long CLOUD_PROJECT = 624942467596L;

    private static volatile Object provider;

    private Integrity() {}

    /** Block on a Google Play Services Task (loaded through the host app's classloader). */
    static Object await(ClassLoader cl, Object task) throws Exception {
        Class<?> tasks = Class.forName("com.google.android.gms.tasks.Tasks", false, cl);
        Class<?> taskCls = Class.forName("com.google.android.gms.tasks.Task", false, cl);
        Method m = tasks.getMethod("await", taskCls, long.class, TimeUnit.class);
        return m.invoke(null, task, 60L, TimeUnit.SECONDS);
    }

    public static String token(Context ctx, String nonce) throws Exception {
        ClassLoader cl = ctx.getClassLoader();
        try {
            return standard(ctx, cl, nonce);
        } catch (Throwable t) {
            return classic(ctx, cl, nonce);
        }
    }

    private static synchronized String standard(Context ctx, ClassLoader cl, String nonce) throws Exception {
        if (provider == null) {
            Class<?> factory = Class.forName(
                    "com.google.android.play.core.integrity.IntegrityManagerFactory", false, cl);
            Class<?> stdMgr = Class.forName(
                    "com.google.android.play.core.integrity.StandardIntegrityManager", false, cl);
            Class<?> prepReq = Class.forName(
                    "com.google.android.play.core.integrity.StandardIntegrityManager$PrepareIntegrityTokenRequest",
                    false, cl);
            Object mgr = factory.getMethod("createStandard", Context.class).invoke(null, ctx);
            Object builder = prepReq.getMethod("builder").invoke(null);
            builder.getClass().getMethod("setCloudProjectNumber", long.class).invoke(builder, CLOUD_PROJECT);
            Object req = builder.getClass().getMethod("build").invoke(builder);
            Object task = stdMgr.getMethod("prepareIntegrityToken", prepReq).invoke(mgr, req);
            provider = await(cl, task);
        }
        Class<?> tokenReq = Class.forName(
                "com.google.android.play.core.integrity.StandardIntegrityManager$StandardIntegrityTokenRequest",
                false, cl);
        Class<?> providerCls = Class.forName(
                "com.google.android.play.core.integrity.StandardIntegrityManager$StandardIntegrityTokenProvider",
                false, cl);
        Class<?> tokenCls = Class.forName(
                "com.google.android.play.core.integrity.StandardIntegrityManager$StandardIntegrityToken",
                false, cl);
        Object builder = tokenReq.getMethod("builder").invoke(null);
        builder.getClass().getMethod("setRequestHash", String.class).invoke(builder, nonce);
        Object req = builder.getClass().getMethod("build").invoke(builder);
        Object task = providerCls.getMethod("request", tokenReq).invoke(provider, req);
        Object tok = await(cl, task);
        return (String) tokenCls.getMethod("token").invoke(tok);
    }

    private static String classic(Context ctx, ClassLoader cl, String nonce) throws Exception {
        Class<?> factory = Class.forName(
                "com.google.android.play.core.integrity.IntegrityManagerFactory", false, cl);
        Class<?> mgrCls = Class.forName(
                "com.google.android.play.core.integrity.IntegrityManager", false, cl);
        Class<?> reqCls = Class.forName(
                "com.google.android.play.core.integrity.IntegrityTokenRequest", false, cl);
        Class<?> respCls = Class.forName(
                "com.google.android.play.core.integrity.IntegrityTokenResponse", false, cl);

        Object mgr = factory.getMethod("create", Context.class).invoke(null, ctx);
        String b64 = Base64.encodeToString(Base64.decode(nonce, 0), 11); // NO_WRAP
        Object builder = reqCls.getMethod("builder").invoke(null);
        builder.getClass().getMethod("setNonce", String.class).invoke(builder, b64);
        builder.getClass().getMethod("setCloudProjectNumber", long.class).invoke(builder, CLOUD_PROJECT);
        Object req = builder.getClass().getMethod("build").invoke(builder);
        Object task = mgrCls.getMethod("requestIntegrityToken", reqCls).invoke(mgr, req);
        Object resp = await(cl, task);
        return (String) respCls.getMethod("token").invoke(resp);
    }
}
