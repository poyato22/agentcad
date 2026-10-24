import pytest

from mises.store import Store
from mises.types import Caps, SafetyClass as C

from .fake_onshape import FakeOnshape


@pytest.fixture
def fake():
    return FakeOnshape()


@pytest.fixture
def store():
    return Store(":memory:")


@pytest.fixture
def run(store):
    r = store.create_run("designer", "fake", "m", "t")
    store.create_policy(r.id, ["*"], ["*"], Caps(),
                        [C.READ, C.LOCAL_MUTATE, C.ONSHAPE_MUTATE, C.IRREVERSIBLE])
    return r


@pytest.fixture
def client(fake, store):
    from mises.onshape.client import OnshapeClient
    return OnshapeClient("AK", "SK", store=store, user="me", transport=fake.transport)


@pytest.fixture
def adapter(client, store):
    from mises.onshape.adapter import OnshapeAdapter
    return OnshapeAdapter(client, store)
