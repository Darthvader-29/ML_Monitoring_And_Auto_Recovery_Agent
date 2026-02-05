## Architecture:

> "One repo, many services, many environments, HTTP everywhere."

```
autonomous-ml-platform/                 # 📦 SINGLE GIT REPO (MONOREPO)
│
├── model-services/                     # 🐳 INFERENCE LAYER
│   │                                  # (Each runs in its OWN container + Python env)
│   │
│   ├── model_a/                        # 🔵 ACTIVE MODEL
│   │   │                              # Runtime:
│   │   │                              # - Docker container
│   │   │                              # - Python env: model_a_env
│   │   │                              # - Port: 8001
│   │   │
│   │   ├── app.py                     # FastAPI server
│   │   ├── model.pkl
│   │   ├── sample_input.csv
│   │   ├── metrics.py                 # latency, error-rate tracking
│   │   ├── requirements.txt           # fastapi, sklearn, numpy
│   │   └── Dockerfile                 # creates isolated env
│   │
│   ├── model_b/                        # 🟡 BACKUP MODEL
│   │   │                              # Runtime:
│   │   │                              # - Docker container
│   │   │                              # - Python env: model_b_env
│   │   │                              # - Port: 8002
│   │   │
│   │   ├── app.py
│   │   ├── model.pkl
│   │   ├── sample_input.csv
│   │   ├── metrics.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│
│
├── control-plane/                      # 🧠 OBSERVABILITY + BRAIN COORDINATION
│   │
│   ├── backend/                        # 🌐 DJANGO CONTROL PLANE
│   │   │                              # Runtime:
│   │   │                              # - Docker container
│   │   │                              # - Python env: django_env
│   │   │                              # - Port: 8000
│   │   │
│   │   ├── manage.py
│   │   ├── config/
│   │   │   ├── settings.py
│   │   │   ├── urls.py
│   │   │   └── wsgi.py
│   │   │
│   │   ├── monitoring_app/             # 📊 Stores metrics from models
│   │   │   ├── models.py               # latency, error rate, status
│   │   │   ├── views.py                # /api/metrics
│   │   │   └── urls.py
│   │   │
│   │   ├── registry_app/               # 📦 Model registry
│   │   │   ├── models.py               # model_name, version, active_flag
│   │   │   ├── views.py                # /api/active-model
│   │   │   └── urls.py
│   │   │
│   │   ├── actions_app/                # 🧾 Agent decisions & audit logs
│   │   │   ├── models.py               # action, severity, outcome
│   │   │   ├── views.py
│   │   │   └── urls.py
│   │   │
│   │   ├── dashboard_app/              # 📈 Optional UI
│   │   │   ├── views.py
│   │   │   └── templates/
│   │   │
│   │   ├── requirements.txt            # django, djangorestframework
│   │   └── Dockerfile
│
│
│   ├── agent_core/                     # 🤖 AUTONOMOUS AGENT
│   │   │                              # Runtime:
│   │   │                              # - Docker container OR bare Python
│   │   │                              # - Python env: agent_env
│   │   │                              # - No web server
│   │   │
│   │   ├── agent.py                    # 🔁 Main loop
│   │   │                              # Observe → Detect → Decide → Act → Verify
│   │   │
│   │   ├── monitoring/                 # 👀 OBSERVE
│   │   │   ├── model_probe.py          # calls model /health, /metrics
│   │   │   ├── prediction_probe.py     # calls /predict
│   │   │   └── data_loader.py          # loads CSV input
│   │   │
│   │   ├── detection/                  # 🚨 DETECT
│   │   │   ├── threshold_detector.py   # latency/error thresholds
│   │   │   ├── anomaly_detector.py
│   │   │   └── drift_detector.py       # optional (later)
│   │   │
│   │   ├── decision_engine/            # 🧠 DECIDE
│   │   │   ├── severity_classifier.py  # LOW / MEDIUM / HIGH
│   │   │   ├── policy_rules.py         # maps severity → action
│   │   │   └── decision.py
│   │   │
│   │   ├── actions/                    # 🚀 ACT
│   │   │   ├── switch_model.py         # trigger Jenkins
│   │   │   ├── alert.py
│   │   │   └── no_op.py
│   │   │
│   │   ├── verification/               # ✅ VERIFY
│   │   │   ├── health_check.py
│   │   │   └── rollback_guard.py
│   │   │
│   │   ├── clients/                    # 🔌 OUTBOUND COMMUNICATION
│   │   │   ├── django_client.py        # REST calls to Django
│   │   │   └── jenkins_client.py       # REST calls to Jenkins
│   │   │
│   │   ├── schemas.py
│   │   ├── config.py
│   │   ├── requirements.txt            # requests, pydantic
│   │   └── Dockerfile
│
│
├── devops/                             # ⚙️ EXECUTION & ORCHESTRATION
│   │
│   ├── jenkins/                        # 🧰 CI/CD SYSTEM
│   │   │                              # Runtime:
│   │   │                              # - Jenkins container
│   │   │                              # - NOT Python
│   │   │
│   │   ├── Jenkinsfile                # pipeline definition
│   │   ├── jobs/
│   │   │   ├── deploy_model.groovy
│   │   │   ├── switch_active_model.groovy
│   │   │   └── rollback_model.groovy
│   │
│   ├── docker/
│   │   ├── docker-compose.yml          # orchestrates ALL containers
│   │   └── networks.yml
│
│
├── docs/                               # 📚 DOCUMENTATION
│   ├── architecture.md
│   ├── agent_logic.md
│   ├── api_contracts.md
│   └── failure_scenarios.md
│
├── .env                                # shared config (ports, URLs)
├── README.md
└── Makefile                            # helper commands


```