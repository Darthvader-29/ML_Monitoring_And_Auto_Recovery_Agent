# agent_core — the agent (deployment_and_devops.md §2.4). venvd. No EXPOSE: pure
# outbound client. Build context: control-plane/agent_core/ (dockerfile: _files/Dockerfile).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY _files/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# whole agent package: _files/{agent,schemas,config}.py + monitoring/, detection/,
# decision_engine/, actions/, verification/, clients/, detection/reference_window.json
COPY . .

# No EXPOSE. agent.py bootstraps sys.path for the _files singletons + subpackages.
CMD ["python", "_files/agent.py"]
