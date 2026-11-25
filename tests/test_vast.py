"""Test the Vast.ai HTTP client against a real local HTTP server."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from cloudrig import connections, vast, vault
from cloudrig.models import CrigError, RateLimited, Rental


class FakeVastServer:
    def __init__(self, handler_fn):
        self.requests: list[tuple[str, str, str | None, dict | None]] = []

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_PUT(self):
                self._handle("PUT")

            def do_DELETE(self):
                self._handle("DELETE")

            def _handle(self, method: str):
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else None
                auth = self.headers.get("Authorization")
                outer.requests.append((method, self.path, auth, body))
                status, payload, headers = handler_fn(method, self.path, auth, body)
                self.send_response(status)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if status != 204 and payload is not None:
                    self.wfile.write(json.dumps(payload).encode("utf-8"))

            def log_message(self, format, *args):
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


def test_mint_scoped_keys_creates_read_and_no_billing_action_keys():
    created = []

    def handle(method, path, auth, body):
        assert auth == "Bearer master-secret"
        assert method == "POST" and path == "/api/v0/auth/apikeys/"
        created.append(body)
        idx = len(created)
        return 200, {"id": idx, "key": f"minted-key-{idx}"}, None

    with FakeVastServer(handle) as server:
        read_key, action_key = vast.mint_scoped_keys("master-secret", base_url=server.base_url)

    assert (read_key, action_key) == ("minted-key-1", "minted-key-2")
    read_perms = created[0]["permissions"]["api"]
    action_perms = created[1]["permissions"]["api"]
    assert "instance_write" not in read_perms
    assert "billing_write" not in read_perms
    assert "user_write" not in read_perms
    assert "instance_write" in action_perms
    assert "billing_write" not in action_perms
    assert "user_write" not in action_perms


def test_mint_scoped_keys_cleans_up_first_key_on_second_failure():
    deleted = []

    def handle(method, path, auth, body):
        if method == "POST":
            if "read" in body["name"]:
                return 200, {"id": 99, "key": "read-key"}, None
            return 500, {"error": "boom"}, None
        if method == "DELETE":
            deleted.append(path)
            return 204, None, None
        return 404, {}, None

    with FakeVastServer(handle) as server:
        with pytest.raises(CrigError):
            vast.mint_scoped_keys("master", base_url=server.base_url)
    assert deleted == ["/api/v0/auth/apikeys/99/"]


def test_offers_instances_rent_stop_destroy_and_key_separation(monkeypatch):
    vault.save_keys("read-tok", "action-tok", "5678", scoped=True)
    summaries = []

    def fake_tty(prompt, *, header=""):
        summaries.append(header)
        return "5678"

    monkeypatch.setattr(vault, "read_tty_secret", fake_tty)

    offer_row = {
        "id": 1001,
        "gpu_name": "RTX_4090",
        "num_gpus": 1,
        "gpu_ram": 24576,
        "dph_base": 0.30,
        "storage_cost": 72.0,
        "disk_space": 500,
        "reliability2": 0.995,
        "geolocation": "US",
    }
    instance_row = {
        "id": 777,
        "label": "train-1",
        "gpu_name": "RTX_4090",
        "num_gpus": 1,
        "actual_status": "running",
        "dph_total": 0.305,
        "geolocation": "US",
        "public_ipaddr": "203.0.113.5",
        "ports": {"22/tcp": [{"HostPort": "22022"}]},
        "ssh_host": "ssh5.vast.ai",
        "ssh_port": 12345,
    }

    def handle(method, path, auth, body):
        if path == "/api/v0/gpu_names/unique/":
            assert auth == "Bearer read-tok"
            return 200, {"gpu_names": ["RTX_4090", "H100_SXM"]}, None
        if path == "/api/v0/bundles/":
            assert auth == "Bearer read-tok"
            return 200, {"offers": [offer_row]}, None
        if path.startswith("/api/v1/instances/"):
            assert auth == "Bearer read-tok"
            return 200, {"instances": [instance_row]}, None
        if method == "PUT" and path == "/api/v0/asks/1001/":
            assert auth == "Bearer action-tok"
            assert "ssh-ed25519 " in body["onstart"]
            assert "/root/.ssh/authorized_keys" in body["onstart"]
            return 200, {"success": True, "new_contract": 777}, None
        if method == "POST" and path == "/api/v0/instances/777/ssh/":
            assert auth == "Bearer action-tok"
            assert body["ssh_key"].startswith("ssh-ed25519 ")
            return 200, {"success": True}, None
        if method == "PUT" and path == "/api/v0/instances/777/":
            assert auth == "Bearer action-tok"
            assert body == {"state": "stopped"}
            return 200, {"success": True}, None
        if method == "DELETE" and path == "/api/v0/instances/777/":
            assert auth == "Bearer action-tok"
            return 200, {"success": True}, None
        return 404, {}, None

    with FakeVastServer(handle) as server:
        client = vast.VastClient(base_url=server.base_url)
        offers = client.offers("4090", disk=40, count=1)
        assert len(offers) == 1 and offers[0].id == "1001"
        assert summaries == []  # Reading offers never prompts for PIN.

        new_id = client.rent(Rental(offers[0], "train-1", 40, vast.DEFAULT_IMAGE))
        assert new_id == "777"
        assert connections.has_keypair("777")
        assert "RENT train-1" in summaries[-1]

        items = client.instances()
        assert len(items) == 1 and items[0].host == "203.0.113.5" and items[0].proxy_host == "ssh5.vast.ai"

        client.stop(items[0])
        assert "STOP train-1" in summaries[-1]

        client.destroy(items[0])
        assert "DESTROY train-1" in summaries[-1]
        assert not connections.has_keypair("777")


def test_rent_rejects_price_increase_before_pin_prompt(monkeypatch):
    vault.save_keys("read-tok", "action-tok", "5678", scoped=True)
    monkeypatch.setattr(vault, "read_tty_secret", lambda *a, **k: pytest.fail("Prompted after price spike"))

    live_row = {
        "id": 1001,
        "gpu_name": "RTX_4090",
        "num_gpus": 1,
        "gpu_ram": 24576,
        "dph_base": 0.90,
        "storage_cost": 72.0,
        "disk_space": 500,
        "geolocation": "US",
    }

    def handle(method, path, auth, body):
        return 200, {"offers": [live_row]}, None

    with FakeVastServer(handle) as server:
        client = vast.VastClient(base_url=server.base_url)
        stale_offer = vast._parse_offer({**live_row, "dph_base": 0.30}, 40)
        with pytest.raises(CrigError, match="price increased"):
            client.rent(Rental(stale_offer, "train-1", 40, vast.DEFAULT_IMAGE))


def test_rate_limit_header_raises_rate_limited():
    vault.save_keys("read-tok", "action-tok", "5678", scoped=True)

    def handle(method, path, auth, body):
        return 429, {"error": "slow down"}, {"Retry-After": "12"}

    with FakeVastServer(handle) as server:
        client = vast.VastClient(base_url=server.base_url)
        with pytest.raises(RateLimited) as exc:
            client.instances()
        assert exc.value.seconds == 12


def test_offers_pagination_and_short_429_auto_retry(monkeypatch):
    vault.save_keys("read-tok", "action-tok", "5678", scoped=True)
    monkeypatch.setattr(vast.time, "sleep", lambda s: None)
    cheap = {
        "id": 101,
        "gpu_name": "RTX_3090",
        "num_gpus": 1,
        "gpu_ram": 24576,
        "dph_base": 0.15,
        "storage_cost": 72.0,
        "disk_space": 500,
        "geolocation": "US",
    }
    page2 = {**cheap, "id": 303, "gpu_name": "H100_SXM", "gpu_ram": 81920, "dph_base": 2.50}
    attempts = {"bundles": 0}

    def handle(method, path, auth, body):
        if path == "/api/v0/gpu_names/unique/":
            return 200, {"gpu_names": ["RTX_3090", "H100_SXM"]}, None
        if path == "/api/v0/bundles/":
            attempts["bundles"] += 1
            if attempts["bundles"] == 1:
                return 429, {"error": "burst"}, {"Retry-After": "2"}
            if body.get("dph_total"):
                return 200, {"offers": [page2]}, None
            return 200, {"offers": [cheap]}, None
        return 404, {}, None

    with FakeVastServer(handle) as server:
        client = vast.VastClient(base_url=server.base_url)
        initial = client.offers()
        assert [row.id for row in initial] == ["101"]
        assert attempts["bundles"] == 2  # Retried the brief 2s 429 automatically.
        assert client.has_gpu_match("h100")
        assert not client.has_gpu_match("tokyo")
        extra = client.more_offers(after_price=0.20)
        assert [row.id for row in extra] == ["303"]
