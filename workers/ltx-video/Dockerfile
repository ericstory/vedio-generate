ARG BASE_IMAGE=nvidia/cuda:13.2.0-cudnn-runtime-ubuntu24.04@sha256:7a31e9bfb2086e4b1ac08aa8e4718d7860730ecc6a9882d2f1e5ed6239f8ef5b
FROM ${BASE_IMAGE}

# Pin the official inference code; update deliberately after a GPU smoke test.
ARG LTX_GIT_REF=a95ab856bf29407b6b066ede0abe1846050db56c
ENV DEBIAN_FRONTEND=noninteractive \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/LTX-2/.venv \
    UV_HTTP_TIMEOUT=120 \
    UV_HTTP_RETRIES=8 \
    PATH=/opt/LTX-2/.venv/bin:/root/.local/bin:$PATH \
    PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
    EAGER_LOAD_MODELS=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl ffmpeg git python3.12 && \
    rm -rf /var/lib/apt/lists/* && \
    curl -LsSf https://astral.sh/uv/install.sh | sh

RUN git clone https://github.com/Lightricks/LTX-2.git /opt/LTX-2 && \
    git -C /opt/LTX-2 checkout "$LTX_GIT_REF" && \
    cd /opt/LTX-2 && \
    uv venv "$UV_PROJECT_ENVIRONMENT" --python /usr/bin/python3.12 && \
    uv pip install --index https://download.pytorch.org/whl/cu132 \
      "torch==2.13.0" torchvision && \
    uv pip install --index https://download.pytorch.org/whl/test/cu132/ torchaudio && \
    uv pip install --find-links https://whl.natten.org \
      "natten==0.21.7+torch2130cu132" && \
    uv pip install "nvidia-cudnn-cu13==9.24.0.43" && \
    uv pip install -e ./packages/ltx-core -e ./packages/ltx-pipelines && \
    uv pip install "runpod>=1.8,<2" "boto3>=1.40,<2"

RUN uv pip install "httpx>=0.28,<1"

# PyTorch/Triton compiles a small CUDA driver shim on the first real inference.
# Keep this after the expensive Python dependency layer so CI can reuse its cache.
ENV CC=/usr/bin/gcc \
    CXX=/usr/bin/g++
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential python3.12-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY handler.py /app/handler.py
COPY smoke.py /app/smoke.py
COPY worker_config.py /app/worker_config.py
COPY download_models.sh /app/download_models.sh
RUN chmod 0755 /app/download_models.sh
CMD ["python", "/app/handler.py"]
