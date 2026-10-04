import pytest


@pytest.fixture(autouse=True)
def fresh_render_memories():
    from services.render_farm import http

    http.forget_memories()
    yield
    http.forget_memories()
