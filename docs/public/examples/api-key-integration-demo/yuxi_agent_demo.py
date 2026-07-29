#!/usr/bin/env python3
"""Yuxi API Key external integration demo using only the Python standard library."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted"}


class YuxiAPIError(RuntimeError):
    def __init__(self, status_code: int | None, detail: object):
        self.status_code = status_code
        self.detail = detail
        prefix = f"HTTP {status_code}" if status_code is not None else "网络错误"
        super().__init__(f"{prefix}: {format_detail(detail)}")


def format_detail(detail: object) -> str:
    if isinstance(detail, (dict, list)):
        return json.dumps(detail, ensure_ascii=False)
    return str(detail)


class YuxiClient:
    def __init__(self, base_url: str, api_key: str, *, http_timeout: float = 300.0):
        normalized_base_url = base_url.strip().rstrip("/")
        if not normalized_base_url:
            raise ValueError("YUXI_BASE_URL 不能为空")
        if normalized_base_url.endswith("/api"):
            raise ValueError("YUXI_BASE_URL 应为实例根地址，末尾不要包含 /api")
        if not api_key.startswith("yxkey_"):
            raise ValueError("YUXI_API_KEY 格式错误，应以 yxkey_ 开头")

        self.base_url = normalized_base_url
        self.api_key = api_key
        self.http_timeout = http_timeout

    def list_agents(self) -> list[dict[str, Any]]:
        response = self._request("GET", "/api/agent")
        agents = response.get("agents")
        if not isinstance(agents, list):
            raise RuntimeError("服务端响应缺少 agents 列表")
        return [agent for agent in agents if isinstance(agent, dict)]

    def invoke(
        self,
        *,
        agent_slug: str,
        question: str,
        request_id: str,
        async_mode: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "agent_slug": agent_slug,
            "messages": [{"role": "user", "content": question}],
            "request_id": request_id,
            "async_mode": async_mode,
        }
        return self._request("POST", "/api/agent-invocation/agent-call/runs", payload)

    def get_result(self, run_id: str, *, agent_slug: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"run_id": run_id}
        if agent_slug:
            payload["agent_slug"] = agent_slug
        return self._request("POST", "/api/agent-invocation/agent-call/runs/result", payload)

    def wait_for_result(
        self,
        run_id: str,
        *,
        agent_slug: str,
        wait_timeout: float,
        poll_interval: float,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + wait_timeout
        while True:
            result = self.get_result(run_id, agent_slug=agent_slug)
            status = str(result.get("status") or "unknown")
            print(f"run_id={run_id} status={status}", file=sys.stderr)
            if status in TERMINAL_STATUSES:
                return result
            if time.monotonic() >= deadline:
                raise TimeoutError(f"等待运行结果超时，run_id={run_id}")
            time.sleep(poll_interval)

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "yuxi-api-key-demo/1.0",
            },
        )
        try:
            with urlopen(request, timeout=self.http_timeout) as response:
                response_body = response.read().decode("utf-8")
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            try:
                error_payload = json.loads(error_body)
                detail = error_payload.get("detail", error_payload)
            except json.JSONDecodeError:
                detail = error_body or exc.reason
            raise YuxiAPIError(exc.code, detail) from exc
        except URLError as exc:
            raise YuxiAPIError(None, exc.reason) from exc

        try:
            decoded = json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("服务端没有返回有效 JSON") from exc
        if not isinstance(decoded, dict):
            raise RuntimeError("服务端返回的 JSON 不是对象")
        return decoded


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="使用 API Key 调用 Yuxi Agent")
    parser.add_argument("question", nargs="?", help="发送给 Agent 的问题")
    parser.add_argument("--base-url", default=os.getenv("YUXI_BASE_URL"), help="Yuxi 实例根地址")
    parser.add_argument("--api-key", default=os.getenv("YUXI_API_KEY"), help="API Key；建议通过环境变量传入")
    parser.add_argument(
        "--agent-slug",
        default=os.getenv("YUXI_AGENT_SLUG", "construction-process-rule-validator"),
        help="目标 Agent slug",
    )
    parser.add_argument("--request-id", default=None, help="最多 64 字符的幂等 ID")
    parser.add_argument("--async-mode", action="store_true", help="创建异步 run 并轮询结果")
    parser.add_argument("--list-agents", action="store_true", help="列出当前身份可见的 Agent 后退出")
    parser.add_argument("--http-timeout", type=float, default=300.0, help="单次 HTTP 超时秒数")
    parser.add_argument("--wait-timeout", type=float, default=600.0, help="异步轮询总等待秒数")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="异步轮询间隔秒数")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.base_url:
        print("错误：请设置 YUXI_BASE_URL 或传入 --base-url", file=sys.stderr)
        return 2
    if not args.api_key:
        print("错误：请设置 YUXI_API_KEY 或传入 --api-key", file=sys.stderr)
        return 2

    try:
        client = YuxiClient(args.base_url, args.api_key, http_timeout=args.http_timeout)
        if args.list_agents:
            print(json.dumps(client.list_agents(), ensure_ascii=False, indent=2))
            return 0

        if not args.question:
            print("错误：请提供问题，或使用 --list-agents", file=sys.stderr)
            return 2

        request_id = args.request_id or f"construction-validation-{uuid.uuid4()}"
        result = client.invoke(
            agent_slug=args.agent_slug,
            question=args.question,
            request_id=request_id,
            async_mode=args.async_mode,
        )
        if args.async_mode:
            run_id = str(result.get("run_id") or "")
            if not run_id:
                raise RuntimeError("异步创建响应缺少 run_id")
            result = client.wait_for_result(
                run_id,
                agent_slug=args.agent_slug,
                wait_timeout=args.wait_timeout,
                poll_interval=args.poll_interval,
            )

        if result.get("status") != "completed":
            raise RuntimeError(f"Yuxi 运行未完成：{result.get('status', 'unknown')}")
        output = result.get("output")
        if not isinstance(output, str) or not output.strip():
            raise RuntimeError("Yuxi 响应中的 output 为空")
        try:
            validation_result = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError("规则校验 output 不是有效 JSON") from exc
        if not isinstance(validation_result, dict):
            raise RuntimeError("规则校验 output 必须是 JSON 对象")
        if (
            not isinstance(validation_result.get("validation_status"), str)
            or not isinstance(validation_result.get("summary"), dict)
            or not isinstance(validation_result.get("results"), list)
        ):
            raise RuntimeError("规则校验 output 缺少 validation_status、summary 或 results")
        print(json.dumps(validation_result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") == "completed" else 1
    except (ValueError, RuntimeError, TimeoutError, YuxiAPIError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
