import pytest

import agentguard
from agentguard._runtime import reset as reset_repository
from agentguard.human.broker import reset_broker

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
def fake_repository():
    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    reset_broker()
    yield repo
    reset_repository()
    reset_broker()
