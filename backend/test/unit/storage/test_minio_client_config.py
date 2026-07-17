from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


MODULE_NAME = "minio_client_for_config_test"


def _find_module_path() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "package" / "yuxi" / "storage" / "minio" / "client.py"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("yuxi/storage/minio/client.py not found from test path")


def _load_module(monkeypatch):
    existing = sys.modules.get(MODULE_NAME)
    if existing is not None:
        return existing

    minio_module = ModuleType("minio")
    minio_module.Minio = object
    minio_error_module = ModuleType("minio.error")
    minio_error_module.S3Error = type("S3Error", (Exception,), {})
    yuxi_utils_module = ModuleType("yuxi.utils")
    yuxi_utils_module.logger = SimpleNamespace(debug=lambda *_args, **_kwargs: None)
    monkeypatch.setitem(sys.modules, "minio", minio_module)
    monkeypatch.setitem(sys.modules, "minio.error", minio_error_module)
    monkeypatch.setitem(sys.modules, "yuxi.utils", yuxi_utils_module)

    spec = importlib.util.spec_from_file_location(MODULE_NAME, _find_module_path())
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def test_public_uri_controls_generated_upload_url(monkeypatch):
    module = _load_module(monkeypatch)
    monkeypatch.setenv("MINIO_URI", "http://10.0.0.8:9000")
    monkeypatch.setenv("MINIO_PUBLIC_URI", "https://files.example.test")
    monkeypatch.delenv("RUNNING_IN_DOCKER", raising=False)
    client = module.MinIOClient()
    client.ensure_bucket_exists = lambda _bucket_name=None, **_kwargs: True
    client._client = SimpleNamespace(put_object=lambda **_kwargs: object())

    result = client.upload_file("public", "folder/demo.txt", b"demo")

    assert client.public_endpoint == "files.example.test"
    assert result.url == "https://files.example.test/public/folder/demo.txt"


def test_host_runtime_uses_minio_uri_when_public_uri_is_unset(monkeypatch):
    module = _load_module(monkeypatch)
    monkeypatch.setenv("MINIO_URI", "http://10.0.0.8:9000")
    monkeypatch.delenv("MINIO_PUBLIC_URI", raising=False)
    monkeypatch.delenv("RUNNING_IN_DOCKER", raising=False)

    client = module.MinIOClient()

    assert client.public_base_url == "http://10.0.0.8:9000"


@pytest.mark.parametrize(
    "public_uri",
    [
        "https://files.example.test/minio",
        "https://files.example.test?download=1",
        "https://user:password@files.example.test",
    ],
)
def test_public_uri_rejects_non_origin_values(monkeypatch, public_uri):
    module = _load_module(monkeypatch)
    monkeypatch.setenv("MINIO_PUBLIC_URI", public_uri)

    with pytest.raises(ValueError, match="MINIO_PUBLIC_URI"):
        module.MinIOClient()
