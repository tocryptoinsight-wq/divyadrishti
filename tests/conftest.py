import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.auth.deps import create_token


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def admin_client(client):
    token = create_token("admin", "admin")
    client.cookies.set("token", token, path="/")
    client.headers["Authorization"] = f"Bearer {token}"
    return client


@pytest.fixture(autouse=True)
def _override_settings():
    pass
