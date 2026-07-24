"""Isolate every test's config store into a temp dir, set BEFORE any test module imports the web
app (whose module-level `app = create_app()` would otherwise touch the real state dir)."""

import os
import tempfile

os.environ.setdefault("CODEXMILL_CONFIG_DIR", tempfile.mkdtemp(prefix="codexmill-test-"))

from fastapi.testclient import TestClient  # noqa: E402

TEST_USER = "tester"
TEST_PASSWORD = "test-password-123"


def sign_in(client: TestClient) -> TestClient:
    """Auth is mandatory (ADR 0024): create the local admin on a fresh instance (or sign in if it
    already exists) so the client carries an authenticated session. Returns the same client."""
    import os

    # setup requires the break-glass token when the operator set one (ADR 0024 audit)
    headers = {}
    token = os.environ.get("CODEXMILL_SETUP_TOKEN")
    if token:
        headers["X-Setup-Token"] = token
    r = client.post(
        "/api/auth/setup", json={"username": TEST_USER, "password": TEST_PASSWORD}, headers=headers
    )
    if r.status_code == 409:  # account already exists on this app -> just log in
        r = client.post("/api/auth/login", json={"username": TEST_USER, "password": TEST_PASSWORD})
    assert r.status_code == 200, r.text
    return client
