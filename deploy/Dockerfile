# syntax=docker/dockerfile:1
#
# Shared image for the four CAF testbed services and the Stripe stub.
# RST extraction NEVER runs in this image; it only *runs* the services so the
# prototype can measure real behaviour (latency, faults, repair).
FROM python:3.12-slim

WORKDIR /app

# Install dependencies first for better layer caching. The runtime extra serves
# the testbed; the reason extra adds the (SDK-less) hosted-model reasoner used by
# the sidecar agents when CAF_AGENT_REASONER=openai. Extraction is purely static
# and needs neither. No API key is baked in; it is read from the environment.
COPY pyproject.toml ./
COPY caf ./caf
RUN pip install --no-cache-dir ".[runtime,reason]"

# Service sources are run directly from the tree (not packaged).
COPY testbed ./testbed
COPY deploy/stripe_stub ./deploy/stripe_stub
# Compiled RST artifact the sidecar agents reason against.
COPY rst.json ./rst.json

# Run as an unprivileged user (principle of least privilege).
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8080
