import os
import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_root_serves_html():
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "MCPify" in response.text
    assert "Generate MCP links for any AI Agent" in response.text

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_api_info():
    response = client.get("/api")
    assert response.status_code == 200
    assert response.json()["service"] == "MCPify"

def test_generate_config():
    response = client.post("/generate", json={"url": "https://fastapi.tiangolo.com"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "detected_framework" in data["analysis"]
    assert "claude_desktop" in data["configs"]
    assert "cursor_vscode" in data["configs"]
