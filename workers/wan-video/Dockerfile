# check=skip=InvalidBaseImagePlatform
ARG BASE_IMAGE=runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404@sha256:4d1721e62b56d345c83b4fd6090664be6daf9312caab5b2e76f23d8231941851
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
    HF_HUB_DISABLE_TELEMETRY=1 \
    EAGER_LOAD_MODELS=1

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --break-system-packages --no-cache-dir \
      "diffusers==0.40.0" \
      "transformers==5.16.1" \
      "accelerate==1.14.0" \
      "peft==0.20.0" \
      "safetensors==0.8.0" \
      "huggingface-hub==1.29.0" \
      "imageio-ffmpeg==0.6.0" \
      "scipy==1.17.0" \
      "ftfy>=6.3,<7" \
      "boto3>=1.40,<2" \
      "httpx>=0.28,<1" \
    && pip install --break-system-packages --no-cache-dir --ignore-installed cryptography \
      "runpod>=1.8,<2"

# Diffusers needs the imageio Python frontend in addition to imageio-ffmpeg.
# A separate layer lets this small fix reuse the large CUDA/dependency layer.
RUN pip install --break-system-packages --no-cache-dir "imageio==2.37.0"

WORKDIR /app
COPY handler.py worker_config.py download_models.sh /app/
RUN chmod 0755 /app/download_models.sh
CMD ["python", "/app/handler.py"]
