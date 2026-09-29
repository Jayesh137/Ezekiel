# Card-free always-on host for Ezekiel on a free Hugging Face Space (CPU Basic).
# The Space repo needs only this file. It clones the Ezekiel repo at container
# start (so the Space always runs the latest code), installs deps, and runs the
# loop, which commits data back to GitHub. See deploy/space/README.md.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# HF injects secrets as env vars at runtime: GITHUB_TOKEN, GITHUB_REPOSITORY
# (owner/repo), ETHERSCAN_API_KEY, NTFY_TOPIC, optional TELEGRAM_*.
ENV PYTHONUNBUFFERED=1 PORT=7860
WORKDIR /app
COPY bootstrap.sh /bootstrap.sh
RUN chmod +x /bootstrap.sh
EXPOSE 7860
CMD ["/bootstrap.sh"]
