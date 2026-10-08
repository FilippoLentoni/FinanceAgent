# No `# syntax=` directive: the built-in BuildKit frontend suffices (no Docker Hub pull per build).
#
# financeagent-runtime: the FinanceAgent AgentCore Runtime image (design D1; task 2.2).
# The AgentCore Runtime HTTP protocol contract requires an ARM64 container listening on 0.0.0.0:8080
# (/invocations, /ping). Build it ONCE per commit in the build stage, on an arm64 build host:
#
#   docker build --platform linux/arm64 -f container/Dockerfile -t financeagent-runtime .
#
# and promote it by digest to beta, gamma and prod (scripts/runtime_image.py). Before the push the
# built image must import the agent and ALL its runtime dependencies with its own interpreter
# (lesson L3: an artifact shipped without its dependencies failed at start).
#
# Dependencies come only from uv.lock (exact versions and hashes) plus the vendored, digest-pinned
# finplan-contracts wheel. No dev or test dependencies, tests, fixtures, credentials, model IDs or
# account identifiers are copied into the image; configuration values come from SSM at run time.
ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.23

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/financeagent/venv
WORKDIR /src
COPY pyproject.toml uv.lock README.md ./
COPY vendor/finplan-contracts/ ./vendor/finplan-contracts/
RUN uv sync --frozen --no-dev --no-install-project
COPY agent/ ./agent/
RUN uv sync --frozen --no-dev --no-editable

FROM ${PYTHON_IMAGE} AS runtime
RUN groupadd --system --gid 10001 financeagent \
    && useradd --system --uid 10001 --gid financeagent --home-dir /opt/financeagent --shell /usr/sbin/nologin financeagent
COPY --from=build /opt/financeagent/venv /opt/financeagent/venv
COPY config/ /opt/financeagent/config/
ENV PATH=/opt/financeagent/venv/bin:${PATH} \
    FINPLAN_CONFIG_DIR=/opt/financeagent/config \
    DOCKER_CONTAINER=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /opt/financeagent
USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "finplan_agent"]
