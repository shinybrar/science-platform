package org.opencadc.skaha.utils;

import ca.nrc.cadc.util.StringUtil;
import org.apache.log4j.Logger;
import org.json.JSONObject;

import java.io.*;
import java.nio.ByteBuffer;
import java.nio.channels.Channels;
import java.nio.channels.ReadableByteChannel;
import java.nio.channels.WritableByteChannel;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import org.opencadc.skaha.K8SUtil;
import org.opencadc.skaha.registry.ImageRegistryAuth;

public class CommandExecutioner {
    private static final Logger log = Logger.getLogger(CommandExecutioner.class);

    public static String execute(String[] command) throws IOException, InterruptedException {
        return execute(command, true);
    }

    public static String execute(String[] command, boolean allowError) throws IOException, InterruptedException {
        Process p = Runtime.getRuntime().exec(command);
        String stdout = readStream(p.getInputStream());
        String stderr = readStream(p.getErrorStream());
        log.debug("stdout: " + stdout);
        log.debug("stderr: " + stderr);
        int status = p.waitFor();
        log.debug("Status=" + status + " for command: " + Arrays.toString(command));
        if (status != 0) {
            if (allowError) {
                return stderr;
            } else {
                String message = "Error executing command: " + Arrays.toString(command) + " Error: " + stderr;
                throw new IOException(message);
            }
        }
        return stdout.trim();
    }

    public static void execute(String[] command, OutputStream out) throws IOException, InterruptedException {
        Process p = Runtime.getRuntime().exec(command);

        WritableByteChannel wbc = Channels.newChannel(out);
        ReadableByteChannel rbc = Channels.newChannel(p.getInputStream());

        int count = 0;
        ByteBuffer buffer = ByteBuffer.allocate(512);
        while (count != -1) {
            if (Thread.interrupted()) {
                throw new InterruptedException();
            }
            count = rbc.read(buffer);
            if (count != -1) {
                wbc.write(buffer.flip());
                buffer.flip();
            }
        }
    }

    public static void execute(final String[] command, final OutputStream standardOut, final OutputStream standardErr)
            throws IOException, InterruptedException {
        final Process p = Runtime.getRuntime().exec(command);
        final int code = p.waitFor();
        try (final InputStream stdOut = new BufferedInputStream(p.getInputStream());
             final InputStream stdErr = new BufferedInputStream(p.getErrorStream())) {
            final String commandOutput = readStream(stdOut);
            if (code != 0) {
                final String errorOutput = readStream(stdErr);
                log.error("Code (" + code + ") found from command " + Arrays.toString(command));
                log.error(errorOutput);
                standardErr.write(errorOutput.getBytes());
                standardErr.flush();
            } else {
                log.debug(commandOutput);
                log.debug("Executing " + Arrays.toString(command) + ": OK");
            }
            standardOut.write(commandOutput.getBytes());
            standardOut.flush();
        }
    }

    public static void ensureRegistrySecret(final ImageRegistryAuth registryAuth, final String secretName)
        throws Exception {
        // delete any old secret by this name
        final String[] deleteCmd = new String[] {"kubectl", "--namespace", K8SUtil.getWorkloadNamespace(), "delete", "secret", secretName};
        log.debug("delete secret command: " + Arrays.toString(deleteCmd));
        try {
            String deleteResult = CommandExecutioner.execute(deleteCmd);
            log.debug("delete secret result: " + deleteResult);
        } catch (IOException notFound) {
            log.debug("no secret to delete", notFound);
        }

        // create new secret
        final String[] createCmd = new String[] {
            "kubectl", "--namespace", K8SUtil.getWorkloadNamespace(), "create", "secret", "docker-registry",
            secretName,
            "--docker-server=" + registryAuth.getHost(),
            "--docker-username=" + registryAuth.getUsername(),
            "--docker-password=" + new String(registryAuth.getSecret())
        };
        log.debug("create secret command: " + Arrays.toString(createCmd));

        try {
            String createResult = CommandExecutioner.execute(createCmd);
            log.debug("create secret result: " + createResult);
        } catch (IOException e) {
            if (e.getMessage() != null && e.getMessage().toLowerCase().contains("already exists")) {
                // This can happen with concurrent posts by same user.
                // Considered making secrets unique with the session id,
                // but that would lead to a large number of secrets and there
                // is no k8s option to have them cleaned up automatically.
                // Should look at supporting multiple job creations on a post,
                // specifically for the headless use case.  That way only one
                // secret per post.
                log.warn("secret creation failed, moving on: " + e);
            } else {
                log.error(e.getMessage(), e);
                throw new IOException("error creating image pull secret");
            }
        }
    }

    public static JSONObject getSecretData(final String secretName, final String secretNamespace) throws Exception {
        // Check the current secret
        final String[] getSecretCommand = new String[] {
                "kubectl", "--namespace", secretNamespace, "get", "--ignore-not-found", "secret",
                secretName, "-o", "jsonpath=\"{.data}\""
        };

        final String data = CommandExecutioner.execute(getSecretCommand);

        // The data from the output begins with a double-quote and ends with one, so strip them.
        return StringUtil.hasText(data) ? new JSONObject(data.replaceFirst("\"", "")
                                                             .substring(0, data.lastIndexOf("\"")))
                                        : new JSONObject();
    }

    protected static String readStream(InputStream in) throws IOException {
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        int nRead;
        byte[] data = new byte[1024];
        while ((nRead = in.read(data, 0, data.length)) != -1) {
            buffer.write(data, 0, nRead);
        }
        return buffer.toString(StandardCharsets.UTF_8);
    }

    public static void changeOwnership(String path, int posixId, int groupId) throws IOException, InterruptedException {
        String[] chown = new String[]{"chown", posixId + ":" + groupId, path};
        execute(chown);
    }
}
