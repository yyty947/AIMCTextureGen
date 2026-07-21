from fastapi.testclient import TestClient

from aimctexturegen.main import create_app


def test_health_contract() -> None:
    client = TestClient(create_app())

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "schema_version": 1}
