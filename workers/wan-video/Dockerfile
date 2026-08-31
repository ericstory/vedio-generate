# Blackwell/CUDA 13 image with native Wan 2.2 FP8 and dynamic LoRA support.
ARG BASE_IMAGE=lmsysorg/sglang:v0.5.16@sha256:984699c298a95b73c469b2191403ddc85fd780506e13c39c4afff3845e27bc6c
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
    HF_HUB_DISABLE_TELEMETRY=1 \
    EAGER_LOAD_MODELS=1

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --break-system-packages --no-cache-dir \
      "imageio-ffmpeg==0.6.0" \
      "scipy==1.17.0" \
      "boto3>=1.40,<2" \
      "httpx>=0.28,<1" \
    && pip install --break-system-packages --no-cache-dir --ignore-installed cryptography \
      "runpod>=1.8,<2"

# Diffusers needs the imageio Python frontend in addition to imageio-ffmpeg.
# A separate layer lets this small fix reuse the large CUDA/dependency layer.
RUN pip install --break-system-packages --no-cache-dir "imageio==2.37.0"

# AudioLDM2's enable_model_cpu_offload() hard-requires accelerate, which the
# SGLang base image does not ship; without it the audio stage fails after a
# fully billed video inference.
RUN pip install --break-system-packages --no-cache-dir "accelerate>=1.6,<2"

# SGLang defaults SM12.x (RTX PRO 6000 Blackwell) to plain Torch SDPA because
# its FlashAttention wheels do not cover that arch. SageAttention 2.2 provides
# the fast quantized attention path there; PyPI only hosts 1.x, so it builds
# from the pinned v2.2.0 source with kernels compiled for SM12.0 at image
# build time — no GPU is needed on the build host.
RUN nvcc --version \
    && TORCH_CUDA_ARCH_LIST="12.0" MAX_JOBS=2 pip install --break-system-packages \
      --no-cache-dir --no-build-isolation \
      "sageattention @ git+https://github.com/thu-ml/SageAttention.git@eb615cf6cf4d221338033340ee2de1c37fbdba4a" \
    && python -c "import sageattention"

WORKDIR /app
COPY handler.py worker_config.py smoke.py download_models.sh /app/
RUN chmod 0755 /app/download_models.sh
CMD ["python", "/app/handler.py"]
