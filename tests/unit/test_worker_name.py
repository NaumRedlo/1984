from aiohttp.test_utils import make_mocked_request

from services.render_farm.http import _worker

def test_a_worker_name_in_any_language_comes_back_as_written():
    request = make_mocked_request("GET", "/", headers={"X-Render-Worker": "%D0%B4%D1%80%D0%B5%D0%B9%D0%BA 100%25"})
    assert _worker(request) == "дрейк 100%"

def test_a_plain_worker_name_is_left_alone():
    request = make_mocked_request("GET", "/", headers={"X-Render-Worker": " MacBook Pro "})
    assert _worker(request) == "MacBook Pro"
