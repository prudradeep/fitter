from fastapi.testclient import TestClient

from app.main import app


def test_api_response_exposes_backend_duration():
    response = TestClient(app).get("/health/live")

    assert response.status_code == 200
    assert response.headers["Server-Timing"].startswith("app;dur=")
