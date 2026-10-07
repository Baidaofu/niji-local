package com.nijilocal.module;

import android.app.Application;
import android.content.Context;

import de.robv.android.xposed.IXposedHookLoadPackage;
import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;
import de.robv.android.xposed.XposedHelpers;
import de.robv.android.xposed.callbacks.XC_LoadPackage;

/**
 * LSPosed entry point.
 *
 * Runs inside the official niji・journey process, grabs the application context
 * and starts a local OpenAI / NovelAI compatible HTTP API on 127.0.0.1:8199.
 * Because everything runs in the official app process, the requests use the
 * app's own Android ID, Firebase session and Play Integrity, and the same
 * network stack, so no Cloudflare / anti-bot workarounds are needed.
 */
public class XposedEntry implements IXposedHookLoadPackage {

    public static final String PKG = "com.spellbrush.nijijourney";

    @Override
    public void handleLoadPackage(XC_LoadPackage.LoadPackageParam lpparam) {
        if (!PKG.equals(lpparam.packageName)) return;
        XposedBridge.log("[niji-local] loading in " + lpparam.packageName);
        XposedHelpers.findAndHookMethod(Application.class, "onCreate", new XC_MethodHook() {
            @Override
            protected void afterHookedMethod(MethodHookParam param) {
                try {
                    final Application app = (Application) param.thisObject;
                    final ClassLoader cl = lpparam.classLoader;
                    LocalServer.start(app, cl);
                } catch (Throwable t) {
                    XposedBridge.log(t);
                }
            }
        });
    }
}
