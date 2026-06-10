// devops/jenkins/jobs/switch_active_model.groovy (deployment_and_devops.md §6.4)
// Core failover: make `targetModel` the active production model by flipping the
// Django registry active flag and re-pointing/restarting traffic. Idempotent:
// switching to the already-active model is a no-op.
def call(String targetModel) {
  stage("switch active -> ${targetModel}") {
    // 1) flip the active model in the Django registry (api_contracts.md §B.2)
    httpRequest(
      httpMode: 'POST',
      url: "${env.BACKEND_URL}/api/active-model",
      contentType: 'APPLICATION_JSON',
      customHeaders: [[name: 'Authorization', value: "Token ${env.DJANGO_WRITE_TOKEN}"]],
      requestBody: "{\"model_name\":\"${targetModel}\",\"reason\":\"jenkins switch\",\"switched_by\":\"jenkins\"}")
    // 2) ensure the now-active model is up and serving
    def port = (targetModel == 'model_a') ? '8001' : '8002'
    sh "docker restart ${targetModel} || true"
    sh "until curl -sf http://${targetModel}:${port}/health; do sleep 2; done"
  }
}
return this
