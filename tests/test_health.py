from backend.health import live_status, ready_status


def test_given_running_process_when_liveness_is_requested_then_live_status_is_returned():
    assert live_status() == {"status": "live"}


def test_given_healthy_dependencies_when_readiness_is_checked_then_ready_is_returned():
    payload, status = ready_status({"database": lambda: None})
    assert status == 200
    assert payload["status"] == "ready"


def test_given_failed_dependency_when_readiness_is_checked_then_not_ready_is_returned():
    def fail():
        raise RuntimeError("unavailable")

    payload, status = ready_status({"database": fail})
    assert status == 503
    assert payload["status"] == "not_ready"
    assert payload["failed_dependencies"]["database"] == "RuntimeError"
