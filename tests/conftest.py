import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


@pytest.fixture(scope="session")
def fixture_dir(tmp_path_factory):
    import make_fixture
    dest = tmp_path_factory.mktemp("md")
    os.environ.update(make_fixture.make(dest))
    return dest


@pytest.fixture(scope="session")
def client(fixture_dir):
    from fastapi.testclient import TestClient
    from memory_desk.app import create_app
    with TestClient(create_app(), headers={"x-memory-desk": "1"}) as c:
        for _ in range(200):
            if c.get("/api/health").json()["indexState"] == "ready":
                break
            time.sleep(0.1)
        yield c
        c.post("/api/jobs/cancel")


@pytest.fixture()
def items(client):
    return {m["title"]: m for m in client.get("/api/meetings").json()["items"]}
