# wellgraph MCP server (read-only, streamable HTTP on port 7860).
#
# Build with the graph baked in (KG_URL is a .tar.gz of the Parquet files, for example a
# GitHub Release asset or a Hugging Face "resolve" link):
#
#   tar -czf wellgraph-kg.tar.gz -C data/kg .
#   docker build --build-arg KG_URL=https://.../wellgraph-kg.tar.gz -t wellgraph-mcp .
#   docker run --rm -p 7860:7860 wellgraph-mcp
#
# Or put the Parquet files in a kg/ folder next to this Dockerfile (note that .dockerignore
# excludes *.parquet, so build from a staging copy without it):
#
#   git archive HEAD | tar -x -C /tmp/wg && cp -R data/kg /tmp/wg/kg && rm /tmp/wg/.dockerignore
#   docker build -t wellgraph-mcp /tmp/wg
#
# Or build without data and mount a local graph folder:
#
#   docker build -t wellgraph-mcp .
#   docker run --rm -p 7860:7860 -v "$PWD/data/kg:/app/kg:ro" wellgraph-mcp
#
# The endpoint is http://localhost:7860/mcp. Port 7860 and the non-root user with id 1000
# are what Hugging Face Docker Spaces expect.

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.6 /uv /usr/local/bin/uv

RUN useradd --create-home --uid 1000 app
WORKDIR /app

# Dependencies first, so code changes do not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project && rm -rf /root/.cache

COPY schema.yaml ontology.yaml ./
COPY src ./src

# Graph data, either way:
#   - a kg/ folder of Parquet files in the build context is copied in (schema.yaml is
#     listed first only so this COPY still succeeds when there is no kg/ folder), or
#   - KG_URL points at a .tar.gz of them.
COPY schema.yaml kg* /app/kg/
ARG KG_URL=""
RUN mkdir -p /app/kg \
 && if [ -n "$KG_URL" ]; then \
      python -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], '/tmp/kg.tar.gz')" "$KG_URL" \
      && tar -xzf /tmp/kg.tar.gz -C /app/kg && rm /tmp/kg.tar.gz; \
    fi \
 && chown -R app:app /app

USER app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    WELLGRAPH_SCHEMA=/app/schema.yaml \
    WELLGRAPH_KG_DIR=/app/kg

EXPOSE 7860
CMD ["python", "-m", "wellgraph.mcp_server", "--http", "--host", "0.0.0.0", "--port", "7860"]
