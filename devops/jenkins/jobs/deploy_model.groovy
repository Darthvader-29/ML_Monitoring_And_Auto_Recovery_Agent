// devops/jenkins/jobs/deploy_model.groovy (deployment_and_devops.md §6.3)
// Make a specific model VERSION available and running in its container (stage the
// artifact, restart, wait healthy, register the version). Does NOT flip live
// traffic — that is switch_active_model's job.
def call(String targetModel, String version) {
  stage("deploy ${targetModel}@${version}") {
    def port = (targetModel == 'model_a') ? '8001' : '8002'
    // 1) stage the versioned artifact onto the target's volume (idempotent copy)
    sh "cp /var/artifacts/${targetModel}/${version}/model.pkl " +
       "/var/lib/docker/volumes/${targetModel}_artifacts/_data/model.pkl || true"
    // 2) restart the container so it loads the new artifact
    sh "docker restart ${targetModel} || true"
    // 3) wait until it answers healthy
    sh "until curl -sf http://${targetModel}:${port}/health; do sleep 2; done"
    // 4) register the staged version in the Django registry (api_contracts.md §B.2)
    httpRequest(
      httpMode: 'POST',
      url: "${env.BACKEND_URL}/api/models",
      contentType: 'APPLICATION_JSON',
      customHeaders: [[name: 'Authorization', value: "Token ${env.DJANGO_WRITE_TOKEN}"]],
      requestBody: "{\"model_name\":\"${targetModel}\",\"version\":\"${version}\",\"status\":\"STABLE\"}",
      validResponseCodes: '100:599')
  }
}
return this
