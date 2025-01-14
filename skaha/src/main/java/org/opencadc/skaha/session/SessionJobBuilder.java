package org.opencadc.skaha.session;

import ca.nrc.cadc.util.StringUtil;
import io.kubernetes.client.openapi.models.*;
import io.kubernetes.client.util.Yaml;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.apache.log4j.Logger;

/**
 * Class to interface with Kubernetes.
 */
public class SessionJobBuilder {
    private static final Logger LOGGER = Logger.getLogger(SessionJobBuilder.class);
    private static final String SOFTWARE_LIMITS_GPUS = "software.limits.gpus";
    private static final String SOFTWARE_IMAGESECRET = "software.imagesecret";

    static final String JOB_QUEUE_LABEL_KEY = "kueue.x-k8s.io/queue-name";

    private final Map<String, String> parameters = new HashMap<>();
    private final Path jobFilePath;

    // Options
    private boolean gpuEnabled;
    private Integer gpuCount;
    private String queueName;

    private SessionJobBuilder(final Path jobFilePath) {
        this.jobFilePath = jobFilePath;

    }

    /**
     * Create a new builder from the provided path.
     *
     * @param jobFilePath The Path of the template file.
     * @return SessionJobBuilder instance.  Never null.
     */
    static SessionJobBuilder fromPath(final Path jobFilePath) {
        return new SessionJobBuilder(jobFilePath);
    }

    private static V1NodeSelectorRequirement getV1NodeSelectorRequirement(
            List<V1NodeSelectorTerm> gpuRequiredNodeSelectorTerms) {
        if (gpuRequiredNodeSelectorTerms.size() != 1) {
            throw new IllegalStateException("GPU Node Selector cannot exceed one selector.");
        }

        final V1NodeSelectorTerm gpuNodeSelectorTerm = gpuRequiredNodeSelectorTerms.get(0);
        final List<V1NodeSelectorRequirement> gpuNodeSelectorMatchExpressions =
                gpuNodeSelectorTerm.getMatchExpressions();

        if (gpuNodeSelectorMatchExpressions == null) {
            throw new IllegalStateException("Preset GPU Node Selector match expressions are missing.");
        } else if (gpuNodeSelectorMatchExpressions.size() != 1) {
            throw new IllegalStateException("Preset GPU Node Selector match expressions must be exactly one (found "
                    + gpuNodeSelectorMatchExpressions.size() + ")");
        }

        return gpuNodeSelectorMatchExpressions.get(0);
    }

    static String setConfigValue(String doc, String key, String value) {
        String regKey = key.replace(".", "\\.");
        String regex = "\\$[{]" + regKey + "[}]";
        return doc.replaceAll(regex, value);
    }

    /**
     * Pass parameters to be replaced in the job file.
     *
     * @param parameters Map of parameter String key to String values to replace.
     * @return This SessionJobBuilder, never null.
     */
    SessionJobBuilder withParameters(final Map<String, String> parameters) {
        this.parameters.putAll(parameters);
        return this;
    }

    /**
     * Enable GPU scheduling.
     *
     * @param enableGPU True if GPU scheduling enabled, False otherwise.
     * @return This SessionJobBuilder, never null.
     */
    SessionJobBuilder withGPUEnabled(final boolean enableGPU) {
        this.gpuEnabled = enableGPU;
        return this;
    }

    /**
     * Request some number of GPUs.
     *
     * @param gpuCount The count of GPUs to request.
     * @return This SessionJobBuilder instance, never null
     */
    SessionJobBuilder withGPUCount(final int gpuCount) {
        this.gpuCount = gpuCount;
        this.withParameter(SessionJobBuilder.SOFTWARE_LIMITS_GPUS, getGPUResourceLimit(gpuCount));
        return this;
    }

    /**
     * Set the queue name for the job to use with Kueue.
     * @param queueName The name of the queue to use.
     * @return  This SessionJobBuilder, never null.
     */
    SessionJobBuilder withQueue(final String queueName) {
        this.queueName = queueName;
        return this;
    }

    /**
     * Build a single parameter into this builder's parameter map.
     *
     * @param key   The key to find.
     * @param value The value to replace with.
     * @return This SessionJobBuilder, never null.
     */
    SessionJobBuilder withParameter(final String key, final String value) {
        this.parameters.put(key, value);
        return this;
    }

    /**
     * Use the provided Kubernetes secret to authenticate with the Image Registry to pull the Image.
     * @param imageRegistrySecretName   String existing secret name.
     * @return  This SessionJobBuilder, never null.
     */
    SessionJobBuilder withImageSecret(final String imageRegistrySecretName) {
        this.withParameter(SessionJobBuilder.SOFTWARE_IMAGESECRET, imageRegistrySecretName);
        return this;
    }

    /**
     * Construct the Job YAML output of this builder.
     *
     * @return String of Job YAML, never null.
     * @throws IOException If the provided Path cannot be read.
     */
    String build() throws IOException {
        final byte[] jobFileBytes = Files.readAllBytes(jobFilePath);
        String jobFileString = new String(jobFileBytes, StandardCharsets.UTF_8);
        for (final Map.Entry<String, String> entry : this.parameters.entrySet()) {
            jobFileString = SessionJobBuilder.setConfigValue(jobFileString, entry.getKey(), entry.getValue());
        }

        final V1Job launchJob = (V1Job) Yaml.load(jobFileString);

        mergeQueue(launchJob);
        return mergeAffinity(launchJob);
    }

    /**
     * For the given Job, determine if it's queue-able, and set the appropriate label and suspend information.
     * @param launchJob The Job to modify.
     */
    void mergeQueue(final V1Job launchJob) {
        if (StringUtil.hasText(this.queueName)) {
            LOGGER.debug("Setting queue name to " + this.queueName);
            final V1ObjectMeta jobMetadata;
            final V1ObjectMeta existingJobMetadata = launchJob.getMetadata();
            if (existingJobMetadata == null) {
                jobMetadata = new V1ObjectMeta();
                launchJob.setMetadata(jobMetadata);
            } else {
                jobMetadata = existingJobMetadata;
            }

            final Map<String, String> labels = jobMetadata.getLabels();
            if (labels != null) {
                labels.put(SessionJobBuilder.JOB_QUEUE_LABEL_KEY, this.queueName);
            } else {
                jobMetadata.setLabels(Collections.singletonMap(SessionJobBuilder.JOB_QUEUE_LABEL_KEY, this.queueName));
            }

            final V1JobSpec jobSpec;
            final V1JobSpec existingJobSpec = launchJob.getSpec();
            if (existingJobSpec == null) {
                jobSpec = new V1JobSpec();
                launchJob.setSpec(jobSpec);
            } else {
                jobSpec = existingJobSpec;
            }

            jobSpec.setSuspend(true);
        } else {
            LOGGER.debug("No queue name provided.");
        }
    }

    /**
     * Merge the Node Affinity, if present, with the GPU affinity, if present, with any existing affinity.
     * @param launchJob The Job to modify.
     * @return  The YAML representation of the Job.  Never null.
     */
    private String mergeAffinity(final V1Job launchJob) {
        final V1Affinity gpuAffinity = getGPUSchedulingAffinity();
        if (gpuAffinity != null) {
            final V1JobSpec podTemplate = launchJob.getSpec();
            if (podTemplate != null) {
                // spec.template.spec
                final V1PodSpec podTemplateSpec = podTemplate.getTemplate().getSpec();
                if (podTemplateSpec != null) {
                    final V1Affinity affinity = podTemplateSpec.getAffinity();

                    // spec.template.spec.affinity
                    if (affinity == null) {
                        podTemplateSpec.setAffinity(gpuAffinity);
                    } else {
                        final V1NodeAffinity nodeAffinity = affinity.getNodeAffinity();

                        // spec.template.spec.affinity.nodeAffinity
                        if (nodeAffinity == null) {
                            affinity.setNodeAffinity(gpuAffinity.getNodeAffinity());
                        } else {
                            final List<V1PreferredSchedulingTerm> existingPreferredSchedulingTerms =
                                    nodeAffinity.getPreferredDuringSchedulingIgnoredDuringExecution();
                            final V1NodeAffinity gpuNodeAffinity = gpuAffinity.getNodeAffinity();

                            if (gpuNodeAffinity != null) {
                                final List<V1PreferredSchedulingTerm> gpuAffinityPreferredSchedulingTerms =
                                        gpuNodeAffinity.getPreferredDuringSchedulingIgnoredDuringExecution();

                                final List<V1PreferredSchedulingTerm> mergedPreferredSchedulingTerms =
                                        new ArrayList<>();
                                if (existingPreferredSchedulingTerms != null) {
                                    mergedPreferredSchedulingTerms.addAll(existingPreferredSchedulingTerms);
                                }

                                if (gpuAffinityPreferredSchedulingTerms != null) {
                                    mergedPreferredSchedulingTerms.addAll(gpuAffinityPreferredSchedulingTerms);
                                }

                                if (!mergedPreferredSchedulingTerms.isEmpty()) {
                                    nodeAffinity.setPreferredDuringSchedulingIgnoredDuringExecution(
                                            mergedPreferredSchedulingTerms);
                                }

                                // spec.template.spec.affinity.nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution
                                final V1NodeSelector requiredNodeSelector =
                                        nodeAffinity.getRequiredDuringSchedulingIgnoredDuringExecution();
                                final V1NodeSelector gpuRequiredNodeSelector =
                                        gpuNodeAffinity.getRequiredDuringSchedulingIgnoredDuringExecution();

                                // No preset one from the configuration, so assume the GPU setting is the only one.
                                if (requiredNodeSelector == null) {
                                    nodeAffinity.setRequiredDuringSchedulingIgnoredDuringExecution(
                                            gpuRequiredNodeSelector);
                                } else if (gpuRequiredNodeSelector != null) {
                                    final List<V1NodeSelectorTerm> requiredNodeSelectorTerms =
                                            requiredNodeSelector.getNodeSelectorTerms();
                                    final List<V1NodeSelectorTerm> gpuRequiredNodeSelectorTerms =
                                            gpuRequiredNodeSelector.getNodeSelectorTerms();

                                    if (requiredNodeSelectorTerms.isEmpty()) {
                                        requiredNodeSelector.setNodeSelectorTerms(gpuRequiredNodeSelectorTerms);
                                    } else {
                                        final V1NodeSelectorRequirement gpuNodeSelectorMatchExpression =
                                                SessionJobBuilder.getV1NodeSelectorRequirement(
                                                        gpuRequiredNodeSelectorTerms);
                                        requiredNodeSelectorTerms.forEach(requiredNodeSelectorTerm -> {
                                            final List<V1NodeSelectorRequirement> requiredNodeSelectorMatchExpressions =
                                                    requiredNodeSelectorTerm.getMatchExpressions();
                                            if (requiredNodeSelectorMatchExpressions == null) {
                                                requiredNodeSelectorTerm.setMatchExpressions(
                                                        Collections.singletonList(gpuNodeSelectorMatchExpression));
                                            } else {
                                                requiredNodeSelectorTerm.addMatchExpressionsItem(
                                                        gpuNodeSelectorMatchExpression);
                                            }
                                        });
                                    }
                                } else {
                                    LOGGER.debug("Nothing to alter for Node Affinity.");
                                }
                            }
                        }
                    }
                }
            }
        }

        return Yaml.dump(launchJob);
    }

    /**
     * Obtain the existing GPU scheduling affinity.
     * @return V1Affinity instance, or null if not enabled.
     */
    private V1Affinity getGPUSchedulingAffinity() {
        if (!this.gpuEnabled) {
            return null;
        }

        final V1Affinity gpuAffinity = new V1Affinity();
        final V1NodeAffinity gpuNodeAffinity = new V1NodeAffinity();
        final V1NodeSelector gpuRequiredNodeSelector = new V1NodeSelector();
        final List<V1NodeSelectorTerm> nodeSelectorTerms = new ArrayList<>();
        final V1NodeSelectorTerm nodeSelectorTerm = new V1NodeSelectorTerm();
        final V1NodeSelectorRequirement nodeSelectorRequirement = new V1NodeSelectorRequirement();
        nodeSelectorRequirement.setKey("nvidia.com/gpu.count");

        if (this.gpuCount == null || this.gpuCount <= 0) {
            nodeSelectorRequirement.setOperator("DoesNotExist");
        } else {
            nodeSelectorRequirement.setOperator("Gt");
            nodeSelectorRequirement.setValues(Collections.singletonList("0"));
        }

        nodeSelectorTerm.addMatchExpressionsItem(nodeSelectorRequirement);
        nodeSelectorTerms.add(nodeSelectorTerm);
        gpuRequiredNodeSelector.setNodeSelectorTerms(nodeSelectorTerms);
        gpuNodeAffinity.setRequiredDuringSchedulingIgnoredDuringExecution(gpuRequiredNodeSelector);
        gpuAffinity.setNodeAffinity(gpuNodeAffinity);

        return gpuAffinity;
    }

    private String getGPUResourceLimit(int gpus) {
        if (!this.gpuEnabled) {
            return "";
        }
        return "nvidia.com/gpu: ".concat(Integer.toString(gpus));
    }
}
