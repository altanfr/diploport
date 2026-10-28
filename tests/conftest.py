import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from diploport import util  # noqa: E402


@pytest.fixture
def cfg():
    c = util.load_config()
    c = copy.deepcopy(c)
    c["gemini"]["seconds_between_calls"] = 0
    c["brand"]["site_url"] = "https://example.github.io/diploport"
    c["limits"]["publish_window_tr"] = [0, 24]
    return c


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    data, docs = tmp_path / "data", tmp_path / "docs"
    (docs / "images").mkdir(parents=True)
    (docs / "posts.json").write_text("[]")
    monkeypatch.setattr(util, "DATA_DIR", data)
    monkeypatch.setattr(util, "DOCS_DIR", docs)
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "MAKE_WEBHOOK_URL", "MAKE_SECRET", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return data, docs


FIXTURES = Path(__file__).parent / "fixtures"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")
