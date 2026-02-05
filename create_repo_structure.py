import os

# This script assumes it is run from the REPO ROOT
BASE_PATH = os.getcwd()

STRUCTURE = {
    "model-services": {
        "model_a": [
            "app.py",
            "metrics.py",
            "model.pkl",
            "sample_input.csv",
            "requirements.txt",
            "Dockerfile",
        ],
        "model_b": [
            "app.py",
            "metrics.py",
            "model.pkl",
            "sample_input.csv",
            "requirements.txt",
            "Dockerfile",
        ],
    },

    "control-plane": {
        "backend": {
            "config": [
                "settings.py",
                "urls.py",
                "wsgi.py",
            ],
            "monitoring_app": [
                "models.py",
                "views.py",
                "serializers.py",
                "urls.py",
            ],
            "registry_app": [
                "models.py",
                "views.py",
                "serializers.py",
                "urls.py",
            ],
            "actions_app": [
                "models.py",
                "views.py",
                "urls.py",
            ],
            "dashboard_app": [
                "views.py",
            ],
            "_files": [
                "manage.py",
                "requirements.txt",
                "Dockerfile",
            ],
        },

        "agent_core": {
            "monitoring": [
                "model_probe.py",
                "prediction_probe.py",
                "data_loader.py",
            ],
            "detection": [
                "threshold_detector.py",
                "anomaly_detector.py",
                "drift_detector.py",
            ],
            "decision_engine": [
                "severity_classifier.py",
                "policy_rules.py",
                "decision.py",
            ],
            "actions": [
                "switch_model.py",
                "alert.py",
                "no_op.py",
            ],
            "verification": [
                "health_check.py",
                "rollback_guard.py",
            ],
            "clients": [
                "django_client.py",
                "jenkins_client.py",
            ],
            "_files": [
                "agent.py",
                "schemas.py",
                "config.py",
                "requirements.txt",
                "Dockerfile",
            ],
        },
    },

    "devops": {
        "jenkins": {
            "jobs": [
                "deploy_model.groovy",
                "switch_active_model.groovy",
                "rollback_model.groovy",
            ],
            "_files": [
                "Jenkinsfile",
            ],
        },
        "docker": [
            "docker-compose.yml",
            "networks.yml",
        ],
    },

    "docs": [
        "architecture.md",
        "agent_logic.md",
        "api_contracts.md",
        "failure_scenarios.md",
    ],
}


def create_structure(base_path, structure):
    for name, content in structure.items():
        path = os.path.join(base_path, name)

        # Folder with nested content
        if isinstance(content, dict):
            os.makedirs(path, exist_ok=True)
            create_structure(path, content)

        # Folder with files
        elif isinstance(content, list):
            os.makedirs(path, exist_ok=True)
            for file_name in content:
                file_path = os.path.join(path, file_name)
                if not os.path.exists(file_path):
                    open(file_path, "w").close()


def main():
    create_structure(BASE_PATH, STRUCTURE)
    print("Folder and file structure created inside the current repo.")


if __name__ == "__main__":
    main()
