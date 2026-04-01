package com.wg816.hfsfinder;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Context;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.webkit.*;
import java.io.*;
import java.net.*;
import java.util.*;
import java.util.regex.*;
import java.util.zip.GZIPInputStream;

public class MainActivity extends Activity {
    private WebView webView;
    private SharedPreferences prefs;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences("hfs_prefs", Context.MODE_PRIVATE);

        webView = new WebView(this);
        setContentView(webView);

        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccessFromFileURLs(true);
        s.setAllowUniversalAccessFromFileURLs(true);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);
        s.setSupportZoom(false);

        webView.setWebViewClient(new WebViewClient());
        webView.addJavascriptInterface(new HfsBridge(), "Android");
        webView.loadUrl("file:///android_asset/index.html");
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    /** JavaScript bridge — all methods run on a background thread via JS. */
    class HfsBridge {

        @JavascriptInterface
        public String getPref(String key, String def) {
            return prefs.getString(key, def);
        }

        @JavascriptInterface
        public void setPref(String key, String val) {
            prefs.edit().putString(key, val).apply();
        }

        /** GET a text file from HFS with Basic Auth. */
        @JavascriptInterface
        public String fetchText(String urlStr, String user, String pass) {
            try {
                HttpURLConnection c = openConn(urlStr, user, pass);
                if (c.getResponseCode() != 200)
                    return "ERROR:" + c.getResponseCode();
                return readStream(c.getInputStream());
            } catch (Exception e) {
                return "ERROR:" + e.getMessage();
            }
        }

        /** GET Last-Modified header (epoch millis) for caching. */
        @JavascriptInterface
        public long getLastModified(String urlStr, String user, String pass) {
            try {
                HttpURLConnection c = openConn(urlStr, user, pass);
                c.setRequestMethod("HEAD");
                long lm = c.getLastModified();
                c.disconnect();
                return lm;
            } catch (Exception e) {
                return 0;
            }
        }

        /** List .vfs filenames in a HFS folder (returns JSON array). */
        @JavascriptInterface
        public String listVfsFiles(String folderUrl, String user, String pass) {
            try {
                HttpURLConnection c = openConn(folderUrl, user, pass);
                String html = readStream(c.getInputStream());
                List<String> names = new ArrayList<>();
                Matcher m = Pattern.compile("href=\"([^\"]*\\.vfs)\"").matcher(html);
                while (m.find()) {
                    String raw = m.group(1);
                    // strip path, decode %20
                    String name = URLDecoder.decode(
                        raw.substring(raw.lastIndexOf('/') + 1), "UTF-8");
                    names.add(name);
                }
                Collections.sort(names);
                // Return newest (last alphabetically by date in filename)
                StringBuilder sb = new StringBuilder("[");
                for (int i = 0; i < names.size(); i++) {
                    if (i > 0) sb.append(",");
                    sb.append("\"").append(names.get(i).replace("\"","\\\"")).append("\"");
                }
                sb.append("]");
                return sb.toString();
            } catch (Exception e) {
                return "[]";
            }
        }

        /**
         * Download a .vfs file, decompress, extract folder map.
         * Returns JSON: {"username": ["FOLDER1","FOLDER2"], ...}
         */
        @JavascriptInterface
        public String parseFolderMap(String vfsUrl, String user, String pass) {
            try {
                HttpURLConnection c = openConn(vfsUrl, user, pass);
                byte[] raw = readBytes(c.getInputStream());

                // Decompress: skip first 70 bytes, then GZIP
                byte[] decompressed;
                try (GZIPInputStream gz = new GZIPInputStream(
                        new ByteArrayInputStream(raw, 70, raw.length - 70))) {
                    ByteArrayOutputStream bos = new ByteArrayOutputStream();
                    byte[] buf = new byte[8192];
                    int n;
                    while ((n = gz.read(buf)) != -1) bos.write(buf, 0, n);
                    decompressed = bos.toByteArray();
                }

                // Extract printable ASCII strings (length >= 2)
                List<String> strings = new ArrayList<>();
                StringBuilder cur = new StringBuilder();
                for (byte b : decompressed) {
                    if (b >= 0x20 && b <= 0x7E) {
                        cur.append((char) b);
                    } else {
                        if (cur.length() >= 2) strings.add(cur.toString());
                        cur.setLength(0);
                    }
                }
                if (cur.length() >= 2) strings.add(cur.toString());

                // Build folder map: drive_path -> usernames -> folder_name
                Set<String> skip = new HashSet<>(Arrays.asList("admin","wg","beats","slop"));
                Map<String, List<String>> map = new LinkedHashMap<>();

                for (int i = 0; i < strings.size() - 2; i++) {
                    String s = strings.get(i);
                    if (isDrivePath(s)) {
                        int j = i + 1;
                        while (j < strings.size() && isJunk(strings.get(j))) j++;
                        if (j < strings.size()) {
                            String users = strings.get(j);
                            if (users.matches("^[a-z0-9;]+$") && users.length() < 100) {
                                int k = j + 1;
                                while (k < strings.size() && isJunk(strings.get(k))) k++;
                                if (k < strings.size() && isFolderName(strings.get(k))) {
                                    String folder = strings.get(k);
                                    for (String u : users.split(";")) {
                                        u = u.trim().toLowerCase();
                                        if (!u.isEmpty() && !skip.contains(u)) {
                                            List<String> list = map.get(u);
                                            if (list == null) { list = new ArrayList<>(); map.put(u, list); }
                                            if (!list.contains(folder)) list.add(folder);
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                // Serialize to JSON
                StringBuilder json = new StringBuilder("{");
                boolean first = true;
                for (Map.Entry<String, List<String>> e : map.entrySet()) {
                    if (!first) json.append(",");
                    first = false;
                    json.append("\"").append(e.getKey().replace("\"","\\\"")).append("\":[");
                    for (int i = 0; i < e.getValue().size(); i++) {
                        if (i > 0) json.append(",");
                        json.append("\"").append(e.getValue().get(i).replace("\"","\\\"")).append("\"");
                    }
                    json.append("]");
                }
                json.append("}");
                return json.toString();

            } catch (Exception e) {
                return "{}";
            }
        }

        // ── helpers ──────────────────────────────────────────────────────
        private boolean isDrivePath(String s) {
            return s.length() > 3 && s.charAt(1) == ':' && s.charAt(2) == '\\';
        }
        private boolean isJunk(String s) {
            return s.length() <= 3 && !s.matches("^[a-z0-9]+$");
        }
        private boolean isFolderName(String s) {
            if (isDrivePath(s) || s.length() < 2) return false;
            return s.matches(".*[A-Z]{2,}.*");
        }

        private HttpURLConnection openConn(String urlStr, String user, String pass)
                throws Exception {
            URL url = new URL(urlStr);
            HttpURLConnection c = (HttpURLConnection) url.openConnection();
            String creds = android.util.Base64.encodeToString(
                (user + ":" + pass).getBytes("UTF-8"),
                android.util.Base64.NO_WRAP);
            c.setRequestProperty("Authorization", "Basic " + creds);
            c.setConnectTimeout(15000);
            c.setReadTimeout(30000);
            return c;
        }

        private String readStream(InputStream is) throws IOException {
            BufferedReader r = new BufferedReader(new InputStreamReader(is, "UTF-8"));
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = r.readLine()) != null) sb.append(line).append('\n');
            r.close();
            return sb.toString();
        }

        private byte[] readBytes(InputStream is) throws IOException {
            ByteArrayOutputStream bos = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = is.read(buf)) != -1) bos.write(buf, 0, n);
            is.close();
            return bos.toByteArray();
        }
    }
}
