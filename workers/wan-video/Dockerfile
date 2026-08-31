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
      "runpod>=1.8,<2"

# Diffusers needs the imageio Python frontend in addition to imageio-ffmpeg.
# A separate layer lets this small fix reuse the large CUDA/dependency layer.
RUN pip install --break-system-packages --no-cache-dir "imageio==2.37.0"

WORKDIR /app
COPY handler.py worker_config.py download_models.sh /app/
RUN chmod 0755 /app/download_models.sh
CMD ["python", "/app/handler.py"]
