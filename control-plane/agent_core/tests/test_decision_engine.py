"""Unit tests for the decision engine (severity, policy, assembly)."""
from decision_engine import decision as decision_engine
from decision_engine import policy_rules, severity_classifier
from schemas import ActionType, DetectionResult, Severity


def _thr(metric, observed):
    return DetectionResult(detector="threshold_detector", anomaly_detected=True,
                           metric=metric, observed=observed)


def test_severity_bands_error_rate():
    assert severity_classifier.classify([_thr("error_rate", 0.6)]) == Severity.HIGH
    assert severity_classifier.classify([_thr("error_rate", 0.05)]) == Severity.MEDIUM
    assert severity_classifier.classify([_thr("error_rate", 0.02)]) == Severity.LOW


def test_severity_worst_wins_across_signals():
    sev = severity_classifier.classify([_thr("error_rate", 0.02),
                                        _thr("p95_latency_ms", 900)])
    assert sev == Severity.HIGH


def test_drift_aggregate_severity():
    agg_high = DetectionResult(detector="drift_detector", anomaly_detected=True,
                               metric="data_drift_aggregate", score=0.5)
    assert severity_classifier.classify_detection(agg_high) == Severity.HIGH


def test_policy_high_with_backup_switches():
    action, _ = policy_rules.choose_action(Severity.HIGH, backup_healthy=True,
                                           action_gated=False)
    assert action == ActionType.SWITCH_BACKUP


def test_policy_high_gated_alerts_only():
    action, _ = policy_rules.choose_action(Severity.HIGH, backup_healthy=True,
                                           action_gated=True)
    assert action == ActionType.ALERT


def test_policy_high_no_backup_disables():
    action, _ = policy_rules.choose_action(Severity.HIGH, backup_healthy=False,
                                           action_gated=False)
    assert action == ActionType.DISABLE_PREDICTIONS


def test_policy_low_is_noop():
    action, _ = policy_rules.choose_action(Severity.LOW, backup_healthy=True,
                                           action_gated=False)
    assert action == ActionType.NO_OP


def test_decision_switch_targets_backup():
    d = decision_engine.make_decision(
        [_thr("error_rate", 0.6)], active_model="model_a", backup_model="model_b",
        backup_healthy=True, action_gated=False)
    assert d.action == ActionType.SWITCH_BACKUP and d.target_model == "model_b"
