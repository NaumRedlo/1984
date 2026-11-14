import io

from PIL import Image

from utils import memory_report
from utils.ttl_cache import TTLCache

_kept_for_the_test = TTLCache(maxsize=10, ttl=60.0)


def test_the_report_names_the_process_its_objects_and_a_holder_of_pixels():
    _kept_for_the_test["cover"] = Image.new("RGBA", (1024, 512))
    _kept_for_the_test["png"] = io.BytesIO(b"x" * 2048)
    lines = memory_report.report(roots=memory_report.ROOTS + (__name__.split(".")[0],))
    text = "\n".join(lines)
    assert lines[0].startswith("memory report of pid ")
    assert "objects the collector tracks" in text
    assert "1024x512 RGBA" in text
    held = next(line for line in lines if line.endswith("test_memory_report._kept_for_the_test"))
    assert held.split()[0] == "2.0" and held.split()[2] == "2"
    _kept_for_the_test.clear()


def test_the_resident_size_is_a_plausible_number_of_megabytes():
    assert 5 < memory_report.resident() < 4096
