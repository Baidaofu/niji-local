package com.nijilocal.module;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/** Builds a Midjourney style command line from API parameters. */
public final class Prompt {

    private Prompt() {}

    public static String modelFlag(String model) {
        if (model == null) return null;
        String m = model.toLowerCase(Locale.ROOT).replace("_", "-").trim();
        switch (m) {
            case "niji-7": case "niji7": case "niji 7": return "--niji 7";
            case "niji-6": case "niji6": case "niji 6": return "--niji 6";
            case "niji-5": case "niji5": case "niji 5": return "--niji 5";
            case "niji-4": case "niji4": case "niji 4": return "--niji 4";
            case "midjourney": case "mj": case "v7": return "--v 7";
            case "v6.1": return "--v 6.1";
            case "v6": return "--v 6";
            case "v5": return "--v 5";
            case "v8": return "--v 8";
            case "v8.1": return "--v 8.1";
            default:
                if (m.startsWith("--")) return model;
                // Unknown alias (dall-e-3, gpt-image-1, ...): keep the default model.
                return null;
        }
    }

    public static String sizeToAr(String size) {
        if (size == null) return null;
        switch (size.toLowerCase(Locale.ROOT)) {
            case "1792x1024": return "16:9";
            case "1024x1792": return "9:16";
            case "1536x1024": return "3:2";
            case "1024x1536": return "2:3";
            case "1216x832": return "3:2";
            case "832x1216": return "2:3";
            case "2048x2048": case "1024x1024": case "512x512": case "256x256": return "1:1";
            default: return null;
        }
    }

    public static String build(
            String prompt,
            String model,
            String size,
            String aspectRatio,
            Integer stylize,
            String style,
            String negative,
            Long seed,
            Integer chaos,
            Integer weird,
            boolean hd,
            List<String> imagePrompts,
            List<String> styleRefs,
            Integer styleWeight,
            List<String> characterRefs,
            Integer characterWeight,
            List<String> omniRefs,
            Integer omniWeight,
            String extraFlags) {

        List<String> parts = new ArrayList<>();
        if (imagePrompts != null) parts.addAll(imagePrompts);
        parts.add(prompt == null ? "" : prompt.trim());

        String ar = aspectRatio != null ? aspectRatio : sizeToAr(size);
        if (ar != null && !ar.isEmpty()) parts.add("--ar " + ar);

        String flag = modelFlag(model);
        if (flag != null) parts.add(flag);

        if (stylize != null) parts.add("--stylize " + stylize);
        if (style != null) {
            String s = style.toLowerCase(Locale.ROOT);
            if (s.equals("raw") || s.equals("expressive") || s.equals("cute")
                    || s.equals("scenic") || s.equals("original")) {
                parts.add("--style " + s);
            }
        }
        if (negative != null && !negative.isEmpty()) parts.add("--no " + negative);
        if (seed != null) parts.add("--seed " + seed);
        if (chaos != null) parts.add("--chaos " + chaos);
        if (weird != null) parts.add("--weird " + weird);
        if (styleRefs != null && !styleRefs.isEmpty()) {
            parts.add("--sref " + String.join(" ", styleRefs));
            if (styleWeight != null) parts.add("--sw " + styleWeight);
        }
        if (characterRefs != null && !characterRefs.isEmpty()) {
            parts.add("--cref " + String.join(" ", characterRefs));
            if (characterWeight != null) parts.add("--cw " + characterWeight);
        }
        if (omniRefs != null && !omniRefs.isEmpty()) {
            parts.add("--oref " + String.join(" ", omniRefs));
            if (omniWeight != null) parts.add("--ow " + omniWeight);
        }
        if (hd) parts.add("--hd");
        if (extraFlags != null && !extraFlags.trim().isEmpty()) parts.add(extraFlags.trim());

        StringBuilder sb = new StringBuilder();
        for (String p : parts) {
            if (p == null || p.isEmpty()) continue;
            if (sb.length() > 0) sb.append(' ');
            sb.append(p);
        }
        return sb.toString();
    }
}
