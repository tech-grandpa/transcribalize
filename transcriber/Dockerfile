# GPU-accelerated transcription service using faster-whisper
# Using CUDA 12.x (faster-whisper wheels are built against CUDA 12)
# Driver 580.x is backward compatible with CUDA 12
FROM nvidia/cuda:13.3.1-cudnn-devel-ubuntu24.04

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 \
    python3.12-venv \
    libpython3.12t64 \
    python3-pip \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Set Python 3.12 as default
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.12 1 \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
COPY requirements-granite.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Optional experimental IBM Granite Speech backends. These are kept out of the
# default image until the model/library surface settles.
ARG INSTALL_GRANITE=false
RUN if [ "$INSTALL_GRANITE" = "true" ]; then \
        pip install --no-cache-dir --break-system-packages -r requirements-granite.txt; \
    fi

# Pre-download the Whisper model during build for fast startup
ENV HF_HOME=/models
ENV HF_HUB_CACHE=/models/hub
ENV HF_HUB_DISABLE_XET=1

RUN python -c "from faster_whisper import WhisperModel; WhisperModel('deepdml/faster-whisper-large-v3-turbo-ct2', device='cpu', compute_type='int8', download_root='/models')"

# Pre-download Silero VAD model for smart chunking
RUN python -c "import torch; torch.hub.load('snakers4/silero-vad', 'silero_vad', trust_repo=True, force_reload=False)"

# Copy application code
COPY app/ ./app/
COPY static/ ./static/

# Expose port
EXPOSE 8000

# Run the server
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
