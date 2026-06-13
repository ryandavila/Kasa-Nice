from api.device_store import HostStore


def test_load_missing_file_returns_empty(tmp_path):
    store = HostStore(tmp_path / "nope.json")
    assert store.load() == set()


def test_save_then_load_roundtrip(tmp_path):
    store = HostStore(tmp_path / "hosts.json")
    store.save({"10.0.0.5", "10.0.0.6"})
    assert store.load() == {"10.0.0.5", "10.0.0.6"}


def test_save_creates_parent_directory(tmp_path):
    store = HostStore(tmp_path / "nested" / "dir" / "hosts.json")
    store.save({"10.0.0.5"})
    assert (tmp_path / "nested" / "dir" / "hosts.json").is_file()
    assert store.load() == {"10.0.0.5"}


def test_load_ignores_malformed_content(tmp_path):
    path = tmp_path / "hosts.json"
    path.write_text("not json")
    assert HostStore(path).load() == set()


def test_load_ignores_non_list_json(tmp_path):
    path = tmp_path / "hosts.json"
    path.write_text('{"host": "10.0.0.5"}')
    assert HostStore(path).load() == set()
