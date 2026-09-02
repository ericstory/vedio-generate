# Blackwell/CUDA 13 image for MiniMax H3 joint video+audio generation.
# v0.5.18 is the first tagged release whose MiniMax H3 sampling contract matches
# main byte for byte; H3 diffusion serving is not yet a stable SGLang release
# contract, so the tag is pinned by digest and moved only after a GPU smoke test.
ARG BASE_IMAGE=lmsysorg/sglang:v0.5.18-cu130@sha256:9e148f5ac788e856a06166bd6347a831831eb9fcfab4d1770874823a7c29a1a1
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
    HF_HUB_DISABLE_TELEMETRY=1 \
    EAGER_LOAD_MODELS=1

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

# huggingface_hub[cli] provides the `hf` command download_models.sh drives at
# start-up when the Pod has no network volume attached.
RUN pip install --break-system-packages --no-cache-dir \
      "imageio-ffmpeg==0.6.0" \
      "boto3>=1.40,<2" \
      "huggingface_hub[cli]>=0.35,<1" \
      "httpx>=0.28,<1" \
    && pip install --break-system-packages --no-cache-dir --ignore-installed cryptography \
      "runpod>=1.8,<2"

# SGLang defaults SM12.x (RTX PRO 6000 Blackwell) to plain Torch SDPA because
# its FlashAttention wheels do not cover that arch. SageAttention 2.2 provides
# the fast quantized attention path there; PyPI only hosts 1.x, so it builds
# from the pinned v2.2.0 source at image build time — no GPU is needed on the
# build host. MiniMax measured this backend as the single largest H3 speedup on
# consumer Blackwell (2.32x over BF16+FA on a 4090), and attention cost grows
# quadratically with pixels and duration, so it matters more at 768p than it
# did for Wan at 480p.
#
# Kernels cover three architectures on purpose. A single-arch (12.0) build ties
# this worker to the RTX PRO 6000 family, and that family went globally
# unavailable on RunPod — secure and community, every data centre — while other
# cards still had capacity. Paying build time once buys the scheduler a real
# choice: 8.9 = L40S / RTX 6000 Ada, 9.0 = H100 / H200, 12.0 = RTX PRO 6000 /
# 5000 / 4500 and RTX 5090.
#
# SM 10.0 (B200) is deliberately absent: SageAttention v2.2.0 pins
# SUPPORTED_ARCHS = {8.0, 8.6, 8.9, 9.0, 12.0} and raises on anything else, so
# asking for it fails the build outright. B200 is over the price cap regardless.
RUN nvcc --version \
    && TORCH_CUDA_ARCH_LIST="8.9;9.0;12.0" MAX_JOBS=2 pip install --break-system-packages \
      --no-cache-dir --no-build-isolation \
      "sageattention @ git+https://github.com/thu-ml/SageAttention.git@eb615cf6cf4d221338033340ee2de1c37fbdba4a" \
    && python -c "import sageattention"

WORKDIR /app
COPY handler.py worker_config.py smoke.py download_models.sh /app/
RUN chmod 0755 /app/download_models.sh
CMD ["python", "/app/handler.py"]
