import os
import tempfile
from pathlib import Path

import pytest

# Point the app at a throwaway database *before* it is imported.
_tmp = Path(tempfile.mkdtemp()) / "test.db"
os.environ["PT_DATABASE_URL"] = f"sqlite:///{_tmp}"
os.environ["PT_LOAD_SAMPLE_DATA"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402

SAMPLE = Path(__file__).resolve().parent.parent / "sample_data"


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c


def upload(client, kind: str, content: str):
    return client.post(f"/api/import/{kind}", files={"file": (f"{kind}.csv", content, "text/csv")})


@pytest.fixture()
def seeded(client):
    for kind in ("instruments", "prices", "transactions"):
        r = upload(client, kind, (SAMPLE / f"{kind}.csv").read_text())
        assert r.status_code == 200, r.text
    return client
