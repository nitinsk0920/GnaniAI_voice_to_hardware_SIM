# Voice-to-Hardware Simulator: one container for Hugging Face Spaces (Docker SDK).
# It serves the page and the API on port 7860. Visitors bring their own API keys (no secrets needed).
#
# Local test:  docker build -t v2h .   then   docker run --rm -p 7860:7860 v2h   (open http://localhost:7860)

# ---- Stage 1: build the page (Vite) and install the verifier's Node package (avr8js) ----
FROM node:24-slim AS web
WORKDIR /app
COPY sim/package.json sim/package-lock.json sim/
RUN cd sim && npm ci
COPY backend/verify/package.json backend/verify/package-lock.json backend/verify/
RUN cd backend/verify && npm ci --omit=dev
COPY backend/boards backend/boards
COPY sim sim
RUN cd sim && npm run build

# ---- Stage 2: Python (FastAPI + LangGraph), Node (headless verify) and arduino-cli ----
FROM python:3.12-slim

# Node: only the binary is needed (verify.mjs uses no global packages).
COPY --from=web /usr/local/bin/node /usr/local/bin/node

ARG ARDUINO_CLI_VERSION=1.5.1
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh \
    | BINDIR=/usr/local/bin sh -s ${ARDUINO_CLI_VERSION} \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Hugging Face runs the container as user 1000: install and run everything as that user.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1

# The Arduino AVR core (Uno, Nano, Mega), the same version as on the development laptop.
RUN arduino-cli core update-index && arduino-cli core install arduino:avr@1.8.8

WORKDIR /home/user/app
COPY --chown=user backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir --user -r backend/requirements.txt

COPY --chown=user . .
COPY --chown=user --from=web /app/sim/dist sim/dist
COPY --chown=user --from=web /app/backend/verify/node_modules backend/verify/node_modules

# Compile once now, so the first visitor does not wait for the Arduino core to build.
RUN arduino-cli compile --fqbn arduino:avr:uno --libraries arduino/libraries --output-dir /tmp/warm blink \
 && rm -rf /tmp/warm

# Public server: never use server-side keys, limit requests per visitor.
ENV REQUIRE_USER_KEYS=1 RATE_LIMIT=1
WORKDIR /home/user/app/backend
EXPOSE 7860
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860", "--proxy-headers", "--forwarded-allow-ips", "*"]
