package org.biomodstack.mobile.updates;

import android.util.Base64;
import android.webkit.MimeTypeMap;
import android.webkit.WebResourceResponse;

import androidx.webkit.WebViewAssetLoader;

import org.apache.cordova.CallbackContext;
import org.apache.cordova.CordovaPlugin;
import org.apache.cordova.CordovaPluginPathHandler;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

public class BmsUiBundlePlugin extends CordovaPlugin {
    private static final String SERVED_PATH_PREFIX = "__bms_ui__/";
    private static final String ACTIVE_DIRECTORY_NAME = "active";
    private static final String STAGING_DIRECTORY_NAME = "staging";
    private static final String STORAGE_DIRECTORY_NAME = "bms-ui-bundles";
    private static final String DESCRIPTOR_FILE_NAME = "descriptor.json";
    private static final String BASE_PATH = "/__bms_ui__/active/";

    @Override
    public boolean execute(String action, JSONArray args, CallbackContext callbackContext) throws JSONException {
        if ("getStatus".equals(action)) {
            callbackContext.success(buildStatusPayload());
            return true;
        }

        if ("installBundle".equals(action)) {
            final JSONObject descriptor = args.optJSONObject(0);
            final JSONArray files = args.optJSONArray(1) != null ? args.optJSONArray(1) : new JSONArray();
            if (descriptor == null) {
                throw new JSONException("installBundle requires a descriptor object");
            }
            cordova.getThreadPool().execute(() -> {
                try {
                    callbackContext.success(installBundle(descriptor, files));
                } catch (Exception error) {
                    callbackContext.error(error.getMessage() != null ? error.getMessage() : error.toString());
                }
            });
            return true;
        }

        if ("clearBundle".equals(action)) {
            cordova.getThreadPool().execute(() -> {
                try {
                    deleteRecursively(getActiveBundleDirectory());
                    callbackContext.success(buildStatusPayload());
                } catch (Exception error) {
                    callbackContext.error(error.getMessage() != null ? error.getMessage() : error.toString());
                }
            });
            return true;
        }

        return false;
    }

    @Override
    public CordovaPluginPathHandler getPathHandler() {
        return new CordovaPluginPathHandler(new WebViewAssetLoader.PathHandler() {
            @Override
            public WebResourceResponse handle(String path) {
                try {
                    String normalizedPath = sanitizeRelativePath(path);
                    if (normalizedPath == null || !normalizedPath.startsWith(SERVED_PATH_PREFIX)) {
                        return null;
                    }

                    String bundleRelativePath = normalizedPath.substring(SERVED_PATH_PREFIX.length());
                    File candidate = bundleRelativePath.isEmpty()
                            ? new File(getActiveBundleDirectory(), "index.html")
                            : new File(getBundlesRootDirectory(), bundleRelativePath);

                    if (!candidate.isFile()) {
                        return null;
                    }

                    String mimeType = detectMimeType(candidate.getName());
                    String encoding = isTextMimeType(mimeType) ? StandardCharsets.UTF_8.name() : null;
                    InputStream inputStream = new FileInputStream(candidate);
                    return new WebResourceResponse(mimeType, encoding, inputStream);
                } catch (Exception ignored) {
                    return null;
                }
            }
        });
    }

    private JSONObject installBundle(JSONObject descriptor, JSONArray files) throws IOException, JSONException {
        String version = descriptor.optString("version", "").trim();
        if (version.isEmpty()) {
            throw new IOException("descriptor.version is required");
        }

        File rootDirectory = getBundlesRootDirectory();
        ensureDirectory(rootDirectory);

        File stagingDirectory = new File(rootDirectory, STAGING_DIRECTORY_NAME);
        deleteRecursively(stagingDirectory);
        ensureDirectory(stagingDirectory);

        writeBytes(new File(stagingDirectory, DESCRIPTOR_FILE_NAME), descriptor.toString().getBytes(StandardCharsets.UTF_8));

        for (int index = 0; index < files.length(); index += 1) {
            JSONObject file = files.optJSONObject(index);
            if (file == null) {
                continue;
            }

            String relativePath = sanitizeRelativePath(file.optString("path", ""));
            if (relativePath == null || relativePath.isEmpty()) {
                throw new IOException("Every downloaded file must define a safe relative path");
            }

            String base64 = file.optString("dataBase64", file.optString("contentsBase64", ""));
            if (base64.isEmpty()) {
                throw new IOException("Downloaded file " + relativePath + " is missing base64 payload data");
            }

            writeBytes(new File(stagingDirectory, relativePath), Base64.decode(base64, Base64.DEFAULT));
        }

        File activeDirectory = getActiveBundleDirectory();
        deleteRecursively(activeDirectory);
        if (!stagingDirectory.renameTo(activeDirectory)) {
            throw new IOException("Could not promote staged UI bundle into the active slot");
        }

        JSONObject status = buildStatusPayload();
        status.put("descriptor", descriptor);
        return status;
    }

    private JSONObject buildStatusPayload() throws JSONException {
        JSONObject status = new JSONObject();
        File activeDirectory = getActiveBundleDirectory();
        status.put("installed", activeDirectory.isDirectory());
        status.put("basePath", BASE_PATH);
        status.put("bundlesRoot", getBundlesRootDirectory().getAbsolutePath());

        File descriptorFile = new File(activeDirectory, DESCRIPTOR_FILE_NAME);
        if (descriptorFile.isFile()) {
            try {
                status.put("descriptor", new JSONObject(readFileToString(descriptorFile)));
            } catch (Exception ignored) {
                // Ignore malformed descriptor files and only report the path status.
            }
        }

        return status;
    }

    private File getBundlesRootDirectory() {
        return new File(cordova.getContext().getFilesDir(), STORAGE_DIRECTORY_NAME);
    }

    private File getActiveBundleDirectory() {
        return new File(getBundlesRootDirectory(), ACTIVE_DIRECTORY_NAME);
    }

    private void ensureDirectory(File directory) throws IOException {
        if (directory.isDirectory()) {
            return;
        }
        if (!directory.mkdirs() && !directory.isDirectory()) {
            throw new IOException("Could not create directory " + directory.getAbsolutePath());
        }
    }

    private void writeBytes(File destination, byte[] bytes) throws IOException {
        File parent = destination.getParentFile();
        if (parent != null) {
            ensureDirectory(parent);
        }
        FileOutputStream outputStream = new FileOutputStream(destination);
        try {
            outputStream.write(bytes);
            outputStream.flush();
        } finally {
            outputStream.close();
        }
    }

    private String readFileToString(File file) throws IOException {
        InputStream inputStream = new FileInputStream(file);
        try {
            byte[] buffer = new byte[(int) file.length()];
            int read = inputStream.read(buffer);
            if (read < 0) {
                return "";
            }
            return new String(buffer, 0, read, StandardCharsets.UTF_8);
        } finally {
            inputStream.close();
        }
    }

    private void deleteRecursively(File target) throws IOException {
        if (target == null || !target.exists()) {
            return;
        }

        if (target.isDirectory()) {
            File[] children = target.listFiles();
            if (children != null) {
                for (File child : children) {
                    deleteRecursively(child);
                }
            }
        }

        if (!target.delete() && target.exists()) {
            throw new IOException("Could not delete " + target.getAbsolutePath());
        }
    }

    private String sanitizeRelativePath(String rawPath) {
        if (rawPath == null) {
            return null;
        }

        String normalized = rawPath.replace('\\', '/').trim();
        while (normalized.startsWith("/")) {
            normalized = normalized.substring(1);
        }
        if (normalized.isEmpty()) {
            return "";
        }

        StringBuilder sanitized = new StringBuilder();
        String[] segments = normalized.split("/");
        for (String segment : segments) {
            if (segment.isEmpty() || ".".equals(segment)) {
                continue;
            }
            if ("..".equals(segment)) {
                return null;
            }
            if (sanitized.length() > 0) {
                sanitized.append('/');
            }
            sanitized.append(segment);
        }

        return sanitized.toString();
    }

    private String detectMimeType(String filename) {
        if (filename.endsWith(".js") || filename.endsWith(".mjs")) {
            return "application/javascript";
        }
        if (filename.endsWith(".css")) {
            return "text/css";
        }
        if (filename.endsWith(".html")) {
            return "text/html";
        }
        if (filename.endsWith(".json")) {
            return "application/json";
        }
        if (filename.endsWith(".wasm")) {
            return "application/wasm";
        }
        if (filename.endsWith(".svg")) {
            return "image/svg+xml";
        }
        String extension = MimeTypeMap.getFileExtensionFromUrl(filename);
        String mimeType = extension != null ? MimeTypeMap.getSingleton().getMimeTypeFromExtension(extension) : null;
        return mimeType != null ? mimeType : "application/octet-stream";
    }

    private boolean isTextMimeType(String mimeType) {
        return mimeType != null && (
                mimeType.startsWith("text/")
                        || "application/javascript".equals(mimeType)
                        || "application/json".equals(mimeType)
                        || "image/svg+xml".equals(mimeType)
        );
    }
}
