FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    REEL_TRANSPORT=http \
    REEL_OUTPUT_DIR=/data \
    HOST=0.0.0.0 \
    PORT=8000

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        python3 \
        python3-pip \
        python3-venv \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies and the browser first, from pyproject.toml alone, so every
# build that only changes reel-studio's code reuses these layers. They are
# the heavy part: Chromium, its system libraries and the Python packages,
# about 1 GB, and Playwright's CDN once took 17 minutes to deliver the
# browser on a redeploy that changed one function (2026-10-13). A stub
# package lets pip resolve the project; the real one replaces it below.
COPY pyproject.toml ./
RUN mkdir -p reel_studio && touch reel_studio/__init__.py \
    && python3 -m pip install --break-system-packages . \
    && python3 -m playwright install --with-deps chromium \
    && python3 -m pip uninstall -y --break-system-packages reel-studio \
    && rm -rf reel_studio build *.egg-info

COPY README.md ./
COPY reel_studio ./reel_studio
COPY examples ./examples
RUN python3 -m pip install --break-system-packages --no-deps .

VOLUME ["/data"]
EXPOSE 8000

CMD ["python3", "-m", "reel_studio.server"]
