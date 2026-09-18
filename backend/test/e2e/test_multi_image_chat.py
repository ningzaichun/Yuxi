"""Real upload → API → worker → vision model → persisted multi-image history."""

import asyncio
import io
import os
import uuid

import pytest
from PIL import Image, ImageDraw, ImageFont

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow]


async def test_multi_image_chat_reads_both_images_and_preserves_history(e2e_client, e2e_headers):
    model_spec = os.getenv("E2E_VISION_MODEL")
    if not model_spec:
        pytest.skip("Configure E2E_VISION_MODEL with a vision model, for example closeai:gpt-6-astra.")
    slug = f"multi-image-e2e-{uuid.uuid4().hex[:10]}"
    thread_id = None
    run_id = None
    response = await e2e_client.post(
        "/api/agent",
        headers=e2e_headers,
        json={
            "slug": slug,
            "name": "多图识别回归测试",
            "backend_id": "ChatbotAgent",
            "config_json": {
                "context": {
                    "model": model_spec,
                    "system_prompt": "Read the images directly and answer concisely. Do not use tools.",
                    "tools": [],
                    "skills": [],
                    "mcp_servers": [],
                }
            },
        },
    )
    assert response.status_code == 200, response.text
    try:
        response = await e2e_client.post(
            "/api/chat/thread",
            headers=e2e_headers,
            json={"agent_id": slug, "title": "Multi-image E2E"},
        )
        assert response.status_code == 200, response.text
        thread_id = response.json().get("thread_id") or response.json()["id"]
        urls = []
        labels = [f"ORBIT-{uuid.uuid4().hex[:6].upper()}", f"MAPLE-{uuid.uuid4().hex[:6].upper()}"]
        for label, image_format, color in zip(labels, ["PNG", "JPEG"], ["red", "blue"], strict=True):
            image = Image.new("RGB", (600, 250), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((20, 20, 580, 80), fill=color)
            draw.text((30, 120), label, font=ImageFont.load_default(size=50), fill="black")
            buffer = io.BytesIO()
            image.save(buffer, format=image_format)
            upload = await e2e_client.post(
                "/api/chat/image/upload",
                headers=e2e_headers,
                files={"file": (f"test.{image_format.lower()}", buffer.getvalue(), f"image/{image_format.lower()}")},
            )
            assert upload.status_code == 200, upload.text
            data = upload.json()
            urls.append(f"data:{data['mime_type']};base64,{data['image_content']}")

        response = await e2e_client.post(
            "/api/agent/runs",
            headers=e2e_headers,
            json={
                "agent_slug": slug,
                "thread_id": thread_id,
                "query": "Read the code printed in each image. Return both codes in image order, with no other text.",
                "image_urls": urls,
                "model_spec": model_spec,
                "meta": {"request_id": str(uuid.uuid4())},
            },
        )
        assert response.status_code == 200, response.text
        run_id = response.json()["run_id"]
        deadline = asyncio.get_running_loop().time() + 180
        while asyncio.get_running_loop().time() < deadline:
            response = await e2e_client.get(f"/api/agent/runs/{run_id}/result", headers=e2e_headers)
            assert response.status_code == 200, response.text
            result = response.json()
            if result["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                assert result["status"] == "completed", result
                assert all(label in result["output"] for label in labels), result["output"]
                assert result["output"].index(labels[0]) < result["output"].index(labels[1])
                break
            await asyncio.sleep(2)
        else:
            pytest.fail("Multi-image run timed out")

        response = await e2e_client.get(f"/api/chat/thread/{thread_id}/history", headers=e2e_headers)
        assert response.status_code == 200, response.text
        human = next(message for message in response.json()["history"] if message["type"] == "human")
        assert human["message_type"] == "multimodal_image"
        parts = human["extra_metadata"]["raw_message"]["content"]
        assert [part["image_url"]["url"] for part in parts if part["type"] == "image_url"] == urls
    finally:
        if run_id:
            await e2e_client.post(f"/api/agent/runs/{run_id}/cancel", headers=e2e_headers)
        if thread_id:
            assert (await e2e_client.delete(f"/api/chat/thread/{thread_id}", headers=e2e_headers)).status_code == 200
        assert (await e2e_client.delete(f"/api/agent/{slug}", headers=e2e_headers)).status_code == 200
