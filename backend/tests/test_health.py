import asyncio

import httpx

from aimctexturegen.main import create_app


def test_health_contract() -> None:
    async def get_health() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/api/health")

    response = asyncio.run(get_health())

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "schema_version": 1}
