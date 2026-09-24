# CPU-Basis. Kein CUDA: die Zielmaschine ist eine NAS mit Intel-CPU.
# Fuer die Intel-iGPU zusaetzlich mit --build-arg WITH_OPENVINO=1 bauen
# und /dev/dri durchreichen (siehe docker-compose.intel-gpu.yml).
FROM python:3.11-slim-bookworm

ARG WITH_OPENVINO=0

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
# Keine feste Threadzahl: CTranslate2 nimmt per Default die Zahl der
# physischen Kerne. Ein hartes OMP_NUM_THREADS wuerde eine 6-12-Kern-CPU
# ausbremsen. Feinsteuerung ueber WHISPER_CPU_THREADS.

RUN apt-get update && apt-get install -y --no-install-recommends \
        espeak-ng \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Intel Compute Runtime: nur fuer den OpenVINO-GPU-Pfad. Ohne das sieht
# OpenVINO die iGPU nicht, selbst wenn /dev/dri im Container liegt.
RUN if [ "$WITH_OPENVINO" = "1" ]; then \
        apt-get update && apt-get install -y --no-install-recommends \
            intel-opencl-icd ocl-icd-libopencl1 && \
        rm -rf /var/lib/apt/lists/*; \
    fi

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --upgrade pip && python -m pip install -r requirements.txt
RUN if [ "$WITH_OPENVINO" = "1" ]; then \
        python -m pip install "openvino-genai>=2025.0" "openvino>=2025.0"; \
    fi

COPY scripts ./scripts
COPY app ./app

EXPOSE 8765
ENTRYPOINT ["/app/scripts/entrypoint.sh"]
