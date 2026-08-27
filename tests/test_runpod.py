import httpx

from ai_vedio.config import RunPodSettings
from ai_vedio.runpod import RunPodClient


def settings() -> RunPodSettings:
    return RunPodSettings(api_key="secret", endpoint_id="endpoint")


def test_runpod_client_submits_ltx_job_and_normalizes_status() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "rp-1", "status": "IN_QUEUE"})

    client = RunPodClient(settings())
    client._client.close()
    client._client = httpx.Client(
        base_url="https://api.runpod.ai/v2/endpoint",
        transport=httpx.MockTransport(handler),
    )
    result = client.create_text_video(
        prompt="测试",
        model="pinkcherry-ltx-2.3-v1.8",
        ratio="16:9",
        resolution="720p",
        duration=6,
    )
    assert result == {
        "id": "rp-1",
        "status": "queued",
        "content": {"video_url": None},
        "error": None,
    }
    assert requests[0].url.path == "/v2/endpoint/run"
    assert requests[0].url.params["ttl"] == "7200000"
    payload = __import__("json").loads(requests[0].content)
    assert payload["input"]["model_version"] == "PinkCherry_FineTune_bf16_v1_8_LTX23"
    client.close()


def test_runpod_client_reads_persisted_video_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "rp-1",
                "status": "COMPLETED",
                "output": {"video_url": "https://media.example/video.mp4"},
            },
        )

    client = RunPodClient(settings())
    client._client.close()
    client._client = httpx.Client(
        base_url="https://api.runpod.ai/v2/endpoint",
        transport=httpx.MockTransport(handler),
    )
    result = client.get_task("rp-1")
    assert result["status"] == "succeeded"
    assert result["content"]["video_url"] == "https://media.example/video.mp4"
    client.close()
