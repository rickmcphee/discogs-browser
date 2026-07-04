import screenshots


def test_get_screenshot_path_serves_contained_file(tmp_path, monkeypatch):
    monkeypatch.setattr(screenshots, "SCREENSHOTS_DIR", tmp_path)
    shot = tmp_path / "session" / "01.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b"x")
    assert screenshots.get_screenshot_path("session/01.png") == shot.resolve()


def test_get_screenshot_path_rejects_traversal(tmp_path, monkeypatch):
    base = tmp_path / "screenshots"
    base.mkdir()
    monkeypatch.setattr(screenshots, "SCREENSHOTS_DIR", base)
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"x")
    assert screenshots.get_screenshot_path("../secret.pdf") is None
    assert screenshots.get_screenshot_path("../../etc/anything.png") is None


def test_get_screenshot_path_rejects_absolute(tmp_path, monkeypatch):
    base = tmp_path / "screenshots"
    base.mkdir()
    monkeypatch.setattr(screenshots, "SCREENSHOTS_DIR", base)
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"x")
    assert screenshots.get_screenshot_path(str(secret)) is None
