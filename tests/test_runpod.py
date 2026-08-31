import httpx
import pytest

from ai_vedio.config import RunPodPodSettings, RunPodSettings
from ai_vedio.runpod import RunPodClient, RunPodError, RunPodPodClient


def settings() -> RunPodSettings:
    return RunPodSettings(api_key="secret", endpoint_id="endpoint")


def wan_settings() -> RunPodSettings:
    return RunPodSettings(
        api_key="secret",
        endpoint_id="wan-endpoint",
        model_id="nvidia/Wan2.2-T2V-A14B-Diffusers-FP8",
        model_version="wan-revision",
        workflow_version="wan22-t2v-fp8-adult-lora-audio-v4",
        ui_model_id="wan-2.2-a14b-adult-v2",
        adult_adapter_id="lopi999/Wan2.2-I2V_General-NSFW-LoRA",
        adult_adapter_version="adapter-revision",
        adult_adapter_strength=0.9,
    )


def wan_pod_settings() -> RunPodPodSettings:
    return RunPodPodSettings(
        api_key="secret",
        template_id="template",
        network_volume_id="volume",
        callback_url="https://private.example/generate/api/internal/pod-result",
        callback_token="callback-secret",
        fallback_data_center_id="US-NE-1",
        fallback_network_volume_id="fallback-volume",
        additional_region_volumes=(("US-NC-2", "nc2-volume"),),
    )


def test_runpod_client_submits_ltx_job_and_normalizes_status() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "rp-1", "status": "IN_QUEUE"})

    client = RunPodClient(settings())
    client._client.close()
    client._management_client.close()
    client._client = httpx.Client(
        base_url="https://api.runpod.ai/v2/endpoint",
        transport=httpx.MockTransport(handler),
    )
    client._management_client = httpx.Client(
        base_url="https://api.runpod.io/v2",
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
    assert requests[0].method == "PATCH"
    assert requests[0].url.path == "/v2/serverless/endpoint"
    assert __import__("json").loads(requests[0].content) == {
        "workers": {"max": 1, "min": 0},
    }
    assert requests[1].url.path == "/v2/endpoint/run"
    assert requests[1].url.params["ttl"] == "7200000"
    payload = __import__("json").loads(requests[1].content)
    assert payload["input"]["model_version"] == "PinkCherry_FineTune_bf16_v1_8_LTX23"
    client.close()


def test_runpod_client_reads_persisted_video_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "rp-1",
                "status": "COMPLETED",
                "output": {
                    "video_url": "https://media.example/video.mp4",
                    "engine": "sglang",
                    "quantization": "nvidia-modelopt-fp8",
                    "video_inference_seconds": 480.5,
                    "audio_inference_seconds": 22.0,
                    "peak_memory_mb": 43100.0,
                },
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
    assert result["content"]["engine"] == "sglang"
    assert result["content"]["quantization"] == "nvidia-modelopt-fp8"
    assert result["content"]["video_inference_seconds"] == 480.5
    assert result["content"]["audio_inference_seconds"] == 22.0
    assert result["content"]["peak_memory_mb"] == 43100.0
    client.close()


def test_failed_submission_immediately_closes_gpu_gate() -> None:
    management_payloads = []

    def job_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "submission failed"})

    def management_handler(request: httpx.Request) -> httpx.Response:
        management_payloads.append(__import__("json").loads(request.content))
        return httpx.Response(200, json={"id": "endpoint"})

    client = RunPodClient(settings())
    client._client.close()
    client._management_client.close()
    client._client = httpx.Client(
        base_url="https://api.runpod.ai/v2/endpoint",
        transport=httpx.MockTransport(job_handler),
    )
    client._management_client = httpx.Client(
        base_url="https://api.runpod.io/v2",
        transport=httpx.MockTransport(management_handler),
    )
    with pytest.raises(RunPodError):
        client.create_text_video(prompt="测试", model="pinkcherry-ltx-2.3-v1.8")
    assert management_payloads == [
        {"workers": {"max": 1, "min": 0}},
        {"workers": {"max": 0, "min": 0}},
    ]
    client.close()


def test_submission_retries_endpoint_activation_propagation() -> None:
    job_attempts = 0
    management_payloads = []

    def job_handler(request: httpx.Request) -> httpx.Response:
        nonlocal job_attempts
        job_attempts += 1
        if job_attempts < 3:
            return httpx.Response(
                409,
                json={
                    "status": 409,
                    "detail": "Endpoint is paused",
                    "code": "ENDPOINT_PAUSED",
                },
            )
        return httpx.Response(200, json={"id": "rp-retried", "status": "IN_QUEUE"})

    def management_handler(request: httpx.Request) -> httpx.Response:
        management_payloads.append(__import__("json").loads(request.content))
        return httpx.Response(200, json={"id": "endpoint"})

    client = RunPodClient(settings())
    client._activation_retry_delays = (0.0, 0.0)
    client._client.close()
    client._management_client.close()
    client._client = httpx.Client(
        base_url="https://api.runpod.ai/v2/endpoint",
        transport=httpx.MockTransport(job_handler),
    )
    client._management_client = httpx.Client(
        base_url="https://api.runpod.io/v2",
        transport=httpx.MockTransport(management_handler),
    )
    result = client.create_text_video(
        prompt="测试",
        model="pinkcherry-ltx-2.3-v1.8",
    )
    assert result["id"] == "rp-retried"
    assert job_attempts == 3
    assert management_payloads == [{"workers": {"max": 1, "min": 0}}]
    client.close()


def test_wan_job_always_submits_locked_adult_adapter() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "wan-job", "status": "IN_QUEUE"})

    client = RunPodClient(wan_settings())
    client._client.close()
    client._management_client.close()
    client._client = httpx.Client(
        base_url="https://api.runpod.ai/v2/wan-endpoint",
        transport=httpx.MockTransport(handler),
    )
    client._management_client = httpx.Client(
        base_url="https://api.runpod.io/v2",
        transport=httpx.MockTransport(handler),
    )
    client.create_text_video(
        prompt="测试",
        model="wan-2.2-a14b-adult-v2",
        ratio="16:9",
        resolution="480p",
        duration=5,
        generate_audio=True,
    )
    payload = __import__("json").loads(requests[1].content)["input"]
    assert payload["adult_adapter_id"] == "lopi999/Wan2.2-I2V_General-NSFW-LoRA"
    assert payload["adult_adapter_version"] == "adapter-revision"
    assert payload["adult_adapter_strength"] == 0.9
    assert payload["generate_audio"] is True
    client.close()


def test_wan_pod_uses_exact_gpu_volume_callback_and_price_cap() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"env": {"MODEL_ROOT": "/runpod-volume/models"}})
        return httpx.Response(
            201,
            json={
                "id": "pod-123",
                "cost": 2.09,
                "gpu": {"id": "NVIDIA RTX PRO 6000 Blackwell Server Edition", "count": 1},
            },
        )

    client = RunPodPodClient(wan_pod_settings())
    client._client.close()
    client._client = httpx.Client(
        base_url="https://api.runpod.io/v2",
        transport=httpx.MockTransport(handler),
    )
    result = client.create_text_video(
        prompt="测试",
        model="wan-2.2-a14b-adult-v2",
        task_id="local-task",
        ratio="21:9",
        duration=15,
        generate_audio=True,
    )
    assert result["id"] == "pod-123"
    payload = __import__("json").loads(requests[1].content)
    assert payload["gpu"] == {
        "id": "NVIDIA RTX PRO 6000 Blackwell Server Edition",
        "count": 1,
        "minCudaVersion": "13.0",
    }
    assert payload["mounts"]["network"] == [
        {"volumeId": "volume", "path": "/runpod-volume"}
    ]
    assert payload["env"]["POD_RESULT_CALLBACK_URL"].endswith("/local-task")
    assert payload["env"]["POD_RESULT_CALLBACK_TOKEN"] == "callback-secret"
    assert payload["env"]["MODEL_ROOT"] == "/runpod-volume/models"
    smoke_input = __import__("json").loads(payload["env"]["SMOKE_INPUT_JSON"])
    assert smoke_input["duration"] == 15
    assert smoke_input["adult_adapter_strength"] == 0.9
    client.close()


def test_wan_pod_is_deleted_when_actual_price_exceeds_cap() -> None:
    methods = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"env": {}})
        if request.method == "POST":
            return httpx.Response(201, json={"id": "over-cap", "cost": 3.49})
        return httpx.Response(204)

    client = RunPodPodClient(wan_pod_settings())
    client._client.close()
    client._client = httpx.Client(
        base_url="https://api.runpod.io/v2",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RunPodError, match="exceeds"):
        client.create_text_video(
            prompt="测试",
            model="wan-2.2-a14b-adult-v2",
            task_id="local-task",
        )
    assert methods == [
        ("GET", "/v2/templates/template"),
        ("POST", "/v2/pods"),
        ("DELETE", "/v2/pods/over-cap"),
    ]
    client.close()


def test_runpod_v1_rollback_keeps_legacy_endpoint_gate_shape() -> None:
    # rp-migrate: keep-v1 start
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "endpoint"})

    rollback_settings = RunPodSettings(
        api_key="secret",
        endpoint_id="endpoint",
        management_api_base_url="https://rest.runpod.io/v1",
        use_management_api_v1=True,
    )
    client = RunPodClient(rollback_settings)
    client._management_client.close()
    client._management_client = httpx.Client(
        base_url="https://rest.runpod.io/v1",
        transport=httpx.MockTransport(handler),
    )
    client.set_workers_max(1)
    assert requests[0].url.path == "/v1/endpoints/endpoint"
    assert __import__("json").loads(requests[0].content) == {
        "workersMax": 1,
        "workersMin": 0,
    }
    client.close()
    # rp-migrate: keep-v1 end


def test_wan_pod_retries_each_pinned_region_volume_on_capacity_error() -> None:
    posted = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"env": {}})
        payload = __import__("json").loads(request.content)
        posted.append(payload)
        if len(posted) < 3:
            return httpx.Response(
                500,
                json={"error": "create pod: There are no instances currently available"},
            )
        return httpx.Response(
            201,
            json={
                "id": "fallback-pod",
                "cost": 2.09,
                "gpu": {"id": "NVIDIA RTX PRO 6000 Blackwell Server Edition", "count": 1},
            },
        )

    client = RunPodPodClient(wan_pod_settings())
    client._client.close()
    client._client = httpx.Client(
        base_url="https://api.runpod.io/v2",
        transport=httpx.MockTransport(handler),
    )
    result = client.create_text_video(
        prompt="测试",
        model="wan-2.2-a14b-adult-v2",
        task_id="local-task",
    )
    assert result["id"] == "fallback-pod"
    assert result["content"]["pod_data_center_id"] == "US-NC-2"
    assert posted[0]["dataCenterIds"] == ["US-KS-2"]
    assert posted[0]["mounts"]["network"][0]["volumeId"] == "volume"
    assert posted[1]["dataCenterIds"] == ["US-NE-1"]
    assert posted[1]["mounts"]["network"][0]["volumeId"] == "fallback-volume"
    assert posted[2]["dataCenterIds"] == ["US-NC-2"]
    assert posted[2]["mounts"]["network"][0]["volumeId"] == "nc2-volume"
    client.close()
