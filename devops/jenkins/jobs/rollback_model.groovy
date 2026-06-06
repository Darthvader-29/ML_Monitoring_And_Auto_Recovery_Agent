// devops/jenkins/jobs/rollback_model.groovy (deployment_and_devops.md §6.5)
// Undo a bad deploy/switch by reverting to the last version marked STABLE in the
// registry. This is the reversibility guarantee behind every other action.
def call(String targetModel) {
  stage("rollback ${targetModel} -> last STABLE") {
    // 1) ask the registry for the last known-good version (api_contracts.md §B.2)
    def stable = sh(returnStdout: true, script:
      "curl -sf '${env.BACKEND_URL}/api/models?model=${targetModel}' " +
      "| python3 -c \"import sys,json; vs=[v for v in json.load(sys.stdin) if v['status']=='STABLE']; " +
      "print(vs[0]['version'] if vs else '')\"").trim()
    if (!stable) { error("no STABLE version to roll back to for ${targetModel}") }
    // 2) re-deploy the known-good artifact and re-point to it
    deploy_model(targetModel, stable)
    switch_active_model(targetModel)
  }
}
return this
