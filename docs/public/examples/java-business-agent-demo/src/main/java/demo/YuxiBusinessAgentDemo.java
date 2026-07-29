package demo;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Set;
import java.util.UUID;

/**
 * 外部 Java 服务调用 Yuxi 施工规则校验智能体的 Demo。
 *
 * <p>所有接入参数都在 main 方法中填写；业务代码也可以直接调用
 * {@link #validateConstructionRules(String, String, String, String, Duration, Duration)}。
 */
public final class YuxiBusinessAgentDemo {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final Set<String> TERMINAL_STATUSES =
            Set.of("completed", "failed", "cancelled", "interrupted");

    private final String baseUrl;
    private final String apiKey;
    private final boolean debugEnabled;
    private final HttpClient httpClient;

    public YuxiBusinessAgentDemo(String baseUrl, String apiKey, boolean debugEnabled) {
        if (baseUrl == null || baseUrl.isBlank()) {
            throw new IllegalArgumentException("baseUrl 不能为空");
        }
        if (apiKey == null || apiKey.isBlank() || apiKey.contains("替换")) {
            throw new IllegalArgumentException("请在 main 方法中填写真实 apiKey");
        }
        this.baseUrl = baseUrl.replaceAll("/+$", "");
        this.apiKey = apiKey;
        this.debugEnabled = debugEnabled;
        this.httpClient = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(Duration.ofSeconds(10))
                .build();
    }

    public static void main(String[] args) {
        // 1. 直接在这里填写 Yuxi 接入参数，不需要环境变量或命令行参数。
        String baseUrl = "http://127.0.0.1:5050";
        String apiKey = "yxkey_4202b6002dc1f9be1825f84f9e77aa2bb4196ccb0b754c50";
        String agentSlug = "k";
        String requestId = "construction-validation-" + UUID.randomUUID();

        // 2. 填写本次激活的规则数组。
        String activeRulesJson = """
                [
                  {
                    "rule_id": "CONCRETE-POUR-001",
                    "rule_name": "混凝土浇筑前置验收检查",
                    "rule_type": "prerequisite",
                    "condition": "process_data.rebar_inspection_status == 'passed'",
                    "severity": "critical",
                    "missing_field_policy": "manual_review"
                  }
                ]
                """;

        // 3. 填写本次施工场景数据。
        String sceneDataJson = """
                {
                  "construction_context": {
                    "construction_stage": "主体结构",
                    "work_type": "混凝土浇筑",
                    "building_no": "1号楼",
                    "floor": "8层",
                    "component_type": "梁板",
                    "inspection_batch_id": "PC-2026-001"
                  },
                  "process_data": {
                    "rebar_inspection_status": "pending"
                  }
                }
                """;

        Duration waitTimeout = Duration.ofMinutes(10);
        Duration pollInterval = Duration.ofSeconds(2);
        boolean debugEnabled = true;

        try {
            YuxiBusinessAgentDemo demo =
                    new YuxiBusinessAgentDemo(baseUrl, apiKey, debugEnabled);

            // 4. 普通函数调用：内部创建异步 run 并轮询，函数最终返回规则校验 JSON。
            JsonNode validationResult = demo.validateConstructionRules(
                    agentSlug,
                    requestId,
                    activeRulesJson,
                    sceneDataJson,
                    waitTimeout,
                    pollInterval
            );

            System.out.println(
                    JSON.writerWithDefaultPrettyPrinter().writeValueAsString(validationResult)
            );
        } catch (Exception exception) {
            System.err.println("[ERROR] 调用 Yuxi 失败：" + exception.getMessage());
            exception.printStackTrace(System.err);
            System.exit(1);
        }
    }

    /**
     * 执行一次独立的施工规则校验。
     *
     * @return Agent 最终输出解析后的 JSON 对象
     */
    public JsonNode validateConstructionRules(
            String agentSlug,
            String requestId,
            String activeRulesJson,
            String sceneDataJson,
            Duration waitTimeout,
            Duration pollInterval
    ) throws IOException, InterruptedException {
        validateCallParameters(agentSlug, requestId, waitTimeout, pollInterval);

        JsonNode activeRules = parseInputJson("activeRulesJson", activeRulesJson);
        if (!activeRules.isArray()) {
            throw new IllegalArgumentException("activeRulesJson 必须是 JSON 数组");
        }
        JsonNode sceneData = parseInputJson("sceneDataJson", sceneDataJson);
        if (!sceneData.isObject()) {
            throw new IllegalArgumentException("sceneDataJson 必须是 JSON 对象");
        }

        String question = """
                【当前激活规则 active_rules】
                %s

                【当前施工场景数据 scene_data】
                %s
                """.formatted(
                JSON.writeValueAsString(activeRules),
                JSON.writeValueAsString(sceneData)
        );

        debug("准备调用 Yuxi");
        debug("baseUrl=%s", baseUrl);
        debug("agentSlug=%s", agentSlug);
        debug("requestId=%s", requestId);
        debug("activeRules=%s", JSON.writeValueAsString(activeRules));
        debug("sceneData=%s", JSON.writeValueAsString(sceneData));

        ObjectNode createResult = createRun(agentSlug, requestId, question);
        String runId = createResult.path("run_id").asText();
        if (runId.isBlank()) {
            throw new IllegalStateException("Yuxi 创建响应中缺少 run_id");
        }
        debug(
                "run 创建成功：runId=%s, status=%s",
                runId,
                createResult.path("status").asText("unknown")
        );

        ObjectNode runResult = waitForResult(
                runId,
                agentSlug,
                waitTimeout,
                pollInterval
        );
        return parseValidationResult(runResult);
    }

    private ObjectNode createRun(
            String agentSlug,
            String requestId,
            String question
    ) throws IOException, InterruptedException {
        ObjectNode payload = JSON.createObjectNode()
                .put("agent_slug", agentSlug)
                .put("request_id", requestId)
                .put("async_mode", true);
        payload.putArray("messages")
                .addObject()
                .put("role", "user")
                .put("content", question);

        return postJson(
                "/api/agent-invocation/agent-call/runs",
                payload
        );
    }

    private ObjectNode waitForResult(
            String runId,
            String agentSlug,
            Duration timeout,
            Duration pollInterval
    ) throws IOException, InterruptedException {
        long startedAt = System.nanoTime();
        long deadline = startedAt + timeout.toNanos();

        while (true) {
            ObjectNode payload = JSON.createObjectNode()
                    .put("run_id", runId)
                    .put("agent_slug", agentSlug);
            ObjectNode result = postJson(
                    "/api/agent-invocation/agent-call/runs/result",
                    payload
            );

            String status = result.path("status").asText("unknown");
            long elapsedSeconds =
                    Duration.ofNanos(System.nanoTime() - startedAt).toSeconds();
            debug(
                    "轮询结果：runId=%s, status=%s, elapsed=%ds",
                    runId,
                    status,
                    elapsedSeconds
            );

            if (TERMINAL_STATUSES.contains(status)) {
                return result;
            }
            if (System.nanoTime() >= deadline) {
                throw new IllegalStateException(
                        "等待运行结果超时，runId="
                                + runId
                                + "，最后状态="
                                + status
                                + "。服务端任务可能仍在继续，请检查 Yuxi Worker。"
                );
            }
            Thread.sleep(pollInterval.toMillis());
        }
    }

    private JsonNode parseValidationResult(ObjectNode runResult) {
        String runId = runResult.path("run_id").asText("unknown");
        String status = runResult.path("status").asText("unknown");
        if (!"completed".equals(status)) {
            throw new IllegalStateException(
                    "Yuxi 运行未完成，runId="
                            + runId
                            + "，status="
                            + status
                            + "，errorType="
                            + runResult.path("error_type").asText("")
                            + "，errorMessage="
                            + runResult.path("error_message").asText("")
            );
        }

        String output = runResult.path("output").asText();
        if (output.isBlank()) {
            throw new IllegalStateException(
                    "Yuxi 响应中的 output 为空，runId=" + runId
            );
        }

        JsonNode validationResult;
        try {
            validationResult = JSON.readTree(output);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException(
                    "Agent 未返回合法 JSON，runId="
                            + runId
                            + "，原始 output="
                            + output,
                    exception
            );
        }

        if (!validationResult.isObject()) {
            throw new IllegalStateException("规则校验 output 必须是 JSON 对象");
        }
        if (!validationResult.path("validation_status").isTextual()
                || !validationResult.path("summary").isObject()
                || !validationResult.path("results").isArray()) {
            throw new IllegalStateException(
                    "规则校验 output 缺少 validation_status、summary 或 results"
            );
        }
        debug("Agent 最终 output 已成功解析为 JSON");
        return validationResult;
    }

    private ObjectNode postJson(String path, JsonNode payload)
            throws IOException, InterruptedException {
        String requestBody = JSON.writeValueAsString(payload);
        URI uri = URI.create(baseUrl + path);

        debug("HTTP -> POST %s", uri);
        debug("HTTP -> requestBody=%s", requestBody);
        long startedAt = System.nanoTime();

        HttpRequest request = HttpRequest.newBuilder(uri)
                .timeout(Duration.ofSeconds(30))
                .header("Authorization", "Bearer " + apiKey)
                .header("Accept", "application/json")
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(
                        requestBody,
                        StandardCharsets.UTF_8
                ))
                .build();

        HttpResponse<String> response;
        try {
            response = httpClient.send(
                    request,
                    HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8)
            );
        } catch (IOException | InterruptedException exception) {
            debug(
                    "HTTP !! 请求异常：%s: %s",
                    exception.getClass().getSimpleName(),
                    exception.getMessage()
            );
            throw exception;
        }

        long elapsedMillis =
                Duration.ofNanos(System.nanoTime() - startedAt).toMillis();
        String responseBody = response.body();
        debug(
                "HTTP <- status=%d, elapsed=%dms",
                response.statusCode(),
                elapsedMillis
        );
        debug("HTTP <- responseBody=%s", responseBody);

        if (response.statusCode() < 200 || response.statusCode() >= 300) {
            throw new IllegalStateException(
                    "Yuxi API 调用失败，HTTP "
                            + response.statusCode()
                            + "，responseBody="
                            + responseBody
            );
        }
        if (responseBody == null || responseBody.isBlank()) {
            throw new IllegalStateException("Yuxi API 返回了空响应");
        }

        JsonNode parsedResponse;
        try {
            parsedResponse = JSON.readTree(responseBody);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException(
                    "Yuxi API 返回的不是合法 JSON，responseBody=" + responseBody,
                    exception
            );
        }
        if (!parsedResponse.isObject()) {
            throw new IllegalStateException(
                    "Yuxi API 响应必须是 JSON 对象，responseBody=" + responseBody
            );
        }
        return (ObjectNode) parsedResponse;
    }

    private static JsonNode parseInputJson(String name, String value) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(name + " 不能为空");
        }
        try {
            return JSON.readTree(value);
        } catch (JsonProcessingException exception) {
            throw new IllegalArgumentException(
                    name + " 不是合法 JSON：" + exception.getOriginalMessage(),
                    exception
            );
        }
    }

    private static void validateCallParameters(
            String agentSlug,
            String requestId,
            Duration waitTimeout,
            Duration pollInterval
    ) {
        if (agentSlug == null || agentSlug.isBlank()) {
            throw new IllegalArgumentException("agentSlug 不能为空");
        }
        if (requestId == null || requestId.isBlank()) {
            throw new IllegalArgumentException("requestId 不能为空");
        }
        if (waitTimeout == null || waitTimeout.isZero() || waitTimeout.isNegative()) {
            throw new IllegalArgumentException("waitTimeout 必须大于 0");
        }
        if (pollInterval == null || pollInterval.isZero() || pollInterval.isNegative()) {
            throw new IllegalArgumentException("pollInterval 必须大于 0");
        }
    }

    private void debug(String format, Object... arguments) {
        if (!debugEnabled) {
            return;
        }
        System.err.printf("[YUXI-DEBUG] " + format + "%n", arguments);
    }
}
