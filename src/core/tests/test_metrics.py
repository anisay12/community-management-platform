def test_metrics_hidden_without_token(client):
    assert client.get("/metrics").status_code == 404


def test_metrics_hidden_with_wrong_token(client):
    response = client.get("/metrics", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 404


def test_metrics_exposed_with_token(client):
    client.get("/healthz")
    response = client.get("/metrics", headers={"Authorization": "Bearer test-metrics-token"})
    assert response.status_code == 200
    assert b"django_http_requests" in response.content


def test_metrics_hidden_when_token_not_configured(client, settings):
    settings.METRICS_TOKEN = ""
    response = client.get("/metrics", headers={"Authorization": "Bearer "})
    assert response.status_code == 404
