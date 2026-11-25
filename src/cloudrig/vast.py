"""Minimal Vast.ai client with scoped keys and PIN-gated mutations."""

import email.utils
import gzip
import json
import math
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from cloudrig import connections, vault
from cloudrig.models import CrigError, Instance, Offer, RateLimited, Rental, number
from cloudrig.style import gb, gpu_label, per_day, per_hour

BASE_URL = "https://console.vast.ai"
DEFAULT_IMAGE = "pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime"
API_KEY_URL = "https://console.vast.ai/manage-keys/"

READ_PERMISSIONS = {
    "api": {
        "misc": {},
        "user_read": {},
        "instance_read": {},
        "billing_read": {},
    }
}

ACTION_PERMISSIONS = {
    "api": {
        "misc": {},
        "user_read": {},
        "instance_read": {},
        "instance_write": {},
    }
}

_MAX_RESPONSE_BYTES = 32 * 1024 * 1024


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _AuthDenied(CrigError):
    """Vast.ai rejected the request with HTTP 401 or 403."""


def _opener():
    return build_opener(HTTPSHandler(context=ssl.create_default_context()), _NoRedirect())


def _retry_after(header: str | None) -> int:
    if not header:
        return 30
    try:
        return min(86400, max(1, int(header.strip())))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(header)
        return min(86400, max(1, math.ceil(when.timestamp() - time.time())))
    except (TypeError, ValueError):
        return 30


def _num(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return math.nan
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def _text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
    return ""


def api_call(method: str, path: str, token: str, body: dict | None = None, *, base_url: str = BASE_URL):
    if not path.startswith("/api/"):
        raise CrigError("Invalid Vast.ai API path.")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(
        base_url + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token.strip()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        },
    )
    try:
        with _opener().open(request, timeout=30) as response:
            if response.status == 204:
                return {}
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            encoding = (response.headers.get("Content-Encoding") or "").lower()
    except HTTPError as error:
        if error.code == 429:
            raise RateLimited(_retry_after(error.headers.get("Retry-After"))) from None
        if error.code in (401, 403):
            raise _AuthDenied("Vast.ai rejected this API key. Check your key or run crig login.") from None
        raise CrigError(f"Vast.ai request failed with HTTP {error.code}.") from None
    except (URLError, OSError) as error:
        raise CrigError("Could not connect to Vast.ai.") from error

    if len(raw) > _MAX_RESPONSE_BYTES:
        raise CrigError("Vast.ai response was too large.")
    if encoding == "gzip":
        try:
            raw = gzip.decompress(raw)
        except OSError as error:
            raise CrigError("Vast.ai returned damaged compressed data.") from error
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise CrigError("Vast.ai response was too large.")
    try:
        parsed = json.loads(raw)
    except ValueError as error:
        raise CrigError("Vast.ai returned invalid JSON.") from error
    if isinstance(parsed, dict) and parsed.get("success") is False:
        raise CrigError("Vast.ai declined the operation. Check your account before retrying.")
    return parsed


def verify_key(token: str, *, base_url: str = BASE_URL) -> None:
    api_call("GET", "/api/v1/instances/?limit=1", token, base_url=base_url)


def _create_scoped_key(master_key: str, name: str, permissions: dict, *, base_url: str = BASE_URL):
    result = api_call(
        "POST",
        "/api/v0/auth/apikeys/",
        master_key,
        {"name": name, "permissions": permissions},
        base_url=base_url,
    )
    if not isinstance(result, dict):
        raise CrigError("Vast.ai returned an unexpected API key response.")
    secret = _text(result.get("key") or result.get("api_key"))
    key_id = _text(result.get("id"))
    if not secret:
        raise CrigError("Vast.ai did not return a new API key.")
    return key_id, secret


def _delete_key_best_effort(master_key: str, key_id: str, *, base_url: str = BASE_URL) -> None:
    if not key_id:
        return
    try:
        api_call("DELETE", f"/api/v0/auth/apikeys/{quote(key_id, safe='')}/", master_key, base_url=base_url)
    except CrigError:
        pass


def mint_scoped_keys(master_key: str, *, base_url: str = BASE_URL) -> tuple[str, str]:
    """Mint a read-only key and a no-billing action key, then discard the master key."""
    host = socket.gethostname().split(".")[0][:24] or "local"
    read_id, read_secret = _create_scoped_key(
        master_key, f"cloudrig-read-{host}", READ_PERMISSIONS, base_url=base_url
    )
    try:
        _, action_secret = _create_scoped_key(
            master_key, f"cloudrig-action-{host}", ACTION_PERMISSIONS, base_url=base_url
        )
    except Exception:
        _delete_key_best_effort(master_key, read_id, base_url=base_url)
        raise
    return read_secret, action_secret


def _parse_offer(row: dict, disk: int) -> Offer:
    offer_id = _text(row.get("id"))
    gpu = _text(row.get("gpu_name")).replace("_", " ")
    count = int(_num(row.get("num_gpus")))
    vram = _num(row.get("gpu_ram")) / 1024.0
    disk_rate = _num(row["storage_cost"]) / 720.0 if row.get("storage_cost") is not None else None
    if row.get("dph_base") is not None:
        hourly = _num(row.get("dph_base"))
    elif row.get("dph_total") is not None:
        hourly = _num(row.get("dph_total")) - (disk * disk_rate if disk_rate is not None else 0.0)
    else:
        raise CrigError("Vast.ai offer is missing a price.")
    disk_max = _num(row["disk_space"]) if row.get("disk_space") is not None else None
    reliability = _num(row["reliability2"]) if row.get("reliability2") is not None else None
    return Offer(
        id=offer_id,
        provider="vastai",
        gpu=gpu,
        count=count,
        vram=vram,
        hourly=hourly,
        region=_text(row.get("geolocation")) or "Unknown",
        disk_hourly_gb=disk_rate,
        disk_max=disk_max,
        reliability=reliability,
    )


def _parse_instance(row: dict) -> Instance:
    ident = _text(row.get("id"))
    ports = (row.get("ports") or {}).get("22/tcp") or []
    proxy_host = _text(row.get("ssh_host")) or None
    proxy_port = int(_num(row.get("ssh_port"))) or None
    if ports and isinstance(ports, list) and isinstance(ports[0], dict):
        host = _text(row.get("public_ipaddr")) or proxy_host
        port = int(_num(ports[0].get("HostPort"))) or proxy_port
        direct = bool(host and host != proxy_host)
    else:
        host = proxy_host
        port = proxy_port
        direct = False
    count = max(1, int(_num(row.get("num_gpus"))))
    return Instance(
        id=ident,
        provider="vastai",
        name=_text(row.get("label")) or ident,
        gpu=_text(row.get("gpu_name")).replace("_", " "),
        count=count,
        status=_text(row.get("actual_status")).lower() or "unknown",
        hourly=_num(row.get("dph_total")),
        region=_text(row.get("geolocation")) or "Unknown",
        host=host,
        port=port,
        user="root",
        direct=direct,
        proxy_host=proxy_host,
        proxy_port=proxy_port,
    )


class VastClient:
    name = "vastai"
    default_image = DEFAULT_IMAGE

    def __init__(self, *, base_url: str = BASE_URL):
        self.base_url = base_url
        self._gpu_names: list[str] | None = None

    def _read_token(self) -> str:
        if vault.has_read_key():
            return vault.get_read_key()
        return vault.unlock_action_key("UNLOCK VAST.AI\nNo separate read key saved; enter PIN to read.")

    def _ensure_gpu_names(self, token: str) -> list[str]:
        if self._gpu_names is None:
            names_resp = api_call("GET", "/api/v0/gpu_names/unique/", token, base_url=self.base_url)
            self._gpu_names = [
                name for name in ((names_resp or {}).get("gpu_names") or []) if isinstance(name, str)
            ]
        return self._gpu_names

    def has_gpu_match(self, query: str) -> bool:
        needle = query.strip().lower()
        if len(needle) < 2:
            return False
        names = self._gpu_names
        if names is None:
            try:
                names = self._ensure_gpu_names(self._read_token())
            except CrigError:
                return False
        return any(needle in name.lower().replace("_", " ") or needle in name.lower() for name in names)

    def _query_bundles(self, token: str, payload: dict, disk: int) -> list[Offer]:
        result = api_call("POST", "/api/v0/bundles/", token, payload, base_url=self.base_url)
        rows = (result or {}).get("offers") or []
        return [_parse_offer(row, disk) for row in rows if isinstance(row, dict)]

    def offers(
        self,
        query: str = "",
        disk: int = 40,
        count: int = 1,
        *,
        offer_id: str | None = None,
        min_price: float | None = None,
        limit: int = 250,
    ) -> list[Offer]:
        token = self._read_token()
        payload: dict = {
            "verified": {"eq": True},
            "rentable": {"eq": True},
            "rented": {"eq": False},
            "external": {"eq": False},
            "num_gpus": {"gte": count},
            "disk_space": {"gte": disk},
            "direct_port_count": {"gte": 1},
            "type": "on-demand",
            "order": [["dph_total", "asc"]],
            "limit": limit,
            "allocated_storage": disk,
        }
        if min_price is not None:
            payload["dph_total"] = {"gt": min_price}
        if offer_id is not None:
            try:
                numeric = int(offer_id)
                if numeric <= 0:
                    raise ValueError
            except ValueError as error:
                raise CrigError("Invalid Vast.ai offer ID.") from error
            payload["ask_contract_id"] = {"eq": numeric}
            return self._query_bundles(token, payload, disk)

        if query:
            all_names = self._ensure_gpu_names(token)
            needle = query.lower()
            matched = [
                name
                for name in all_names
                if needle in name.lower() or needle in name.lower().replace("_", " ")
            ]
            if not matched:
                return []
            payload["gpu_name"] = {"in": matched}
            return self._query_bundles(token, payload, disk)

        if min_price is not None:
            return self._query_bundles(token, payload, disk)

        # Initial load: fetch cheapest offers + high-VRAM GPUs + GPU name list in parallel (~2s total).
        cheap_payload = {**payload, "limit": 200}
        vram_payload = {**payload, "gpu_ram": {"gte": 40000}, "limit": 150}
        with ThreadPoolExecutor(max_workers=3) as pool:
            cheap_fut = pool.submit(self._query_bundles, token, cheap_payload, disk)
            vram_fut = pool.submit(self._query_bundles, token, vram_payload, disk)
            names_fut = pool.submit(self._ensure_gpu_names, token)
            cheap_rows = cheap_fut.result()
            try:
                vram_rows = vram_fut.result()
            except CrigError:
                vram_rows = []
            try:
                names_fut.result()
            except CrigError:
                pass
        seen: dict[str, Offer] = {}
        for row in (*cheap_rows, *vram_rows):
            seen.setdefault(row.id, row)
        return list(seen.values())

    def more_offers(self, after_price: float, query: str = "", disk: int = 40, count: int = 1) -> list[Offer]:
        return self.offers(query, disk=disk, count=count, min_price=after_price, limit=250)

    def instances(self) -> list[Instance]:
        token = self._read_token()
        items: list[Instance] = []
        cursor = ""
        seen: set[str] = set()
        while True:
            path = f"/api/v1/instances/?limit=100&after_token={quote(cursor, safe='')}"
            result = api_call("GET", path, token, base_url=self.base_url)
            raw_list = (result or {}).get("instances")
            if not isinstance(raw_list, list):
                raise CrigError("Vast.ai returned an invalid instance list.")
            items.extend(_parse_instance(row) for row in raw_list if isinstance(row, dict))
            next_token = (result or {}).get("next_token") or ""
            if not next_token:
                return items
            if not isinstance(next_token, str) or next_token in seen:
                raise CrigError("Vast.ai returned an invalid pagination cursor.")
            seen.add(next_token)
            cursor = next_token

    def instance(self, identifier: str) -> Instance:
        for row in self.instances():
            if row.id == identifier:
                return row
        raise CrigError(f"Instance {identifier} was not found.")

    def rent(self, rental: Rental) -> str:
        live_offers = self.offers(disk=rental.disk, count=rental.offer.count, offer_id=rental.offer.id)
        live = next((row for row in live_offers if row.id == rental.offer.id), None)
        if live is None:
            raise CrigError("This offer is no longer available. Refresh and pick another GPU.")

        reviewed = rental.offer.total(rental.disk)
        if reviewed is None:
            reviewed = rental.offer.hourly
        current = live.total(rental.disk)
        if current is None:
            current = live.hourly
        number(current)
        if current > reviewed + 0.000001:
            raise CrigError("The offer price increased since you reviewed it. Refresh before renting.")

        storage_note = "incl. disk" if live.disk_hourly_gb is not None else "plus storage"
        summary = "\n".join(
            [
                f"RENT {rental.name}",
                f"  GPU:    {gpu_label(live.gpu, live.count)} ({gb(live.vram * live.count)} VRAM)",
                f"  Region: {live.region}",
                f"  Rate:   {per_hour(current)} ({per_day(current * 24)})"
                f"  [{gb(rental.disk)}, {storage_note}]",
                f"  Image:  {rental.image}",
            ]
        )
        action_key = vault.unlock_action_key(summary)

        # Generate the throwaway SSH key before creation so onstart injects it immediately at boot,
        # avoiding Vast.ai's 60-120s async POST /ssh/ control-plane sync delay.
        temp_key, pub_key = connections.create_temp_keypair()
        onstart = (
            "mkdir -p /root/.ssh && chmod 700 /root/.ssh && "
            f"printf '%s\\n' '{pub_key}' >> /root/.ssh/authorized_keys && "
            "chmod 600 /root/.ssh/authorized_keys && chown -R root:root /root/.ssh"
        )
        try:
            result = api_call(
                "PUT",
                f"/api/v0/asks/{quote(rental.offer.id, safe='')}/",
                action_key,
                {
                    "client_id": "me",
                    "image": rental.image,
                    "disk": rental.disk,
                    "label": rental.name,
                    "runtype": "ssh_direc ssh_proxy",
                    "onstart": onstart,
                    "cancel_unavail": True,
                },
                base_url=self.base_url,
            )
        except Exception:
            connections.discard_temp_keypair(temp_key)
            raise

        new_id = _text((result or {}).get("new_contract"))
        if not new_id:
            connections.discard_temp_keypair(temp_key)
            raise CrigError("Rental outcome is unclear. Check crig --rented before trying again.")

        connections.bind_temp_keypair(temp_key, new_id)

        # Also register the key with Vast.ai's API so proxy SSH works if direct SSH is blocked.
        try:
            api_call(
                "POST",
                f"/api/v0/instances/{quote(new_id, safe='')}/ssh/",
                action_key,
                {"ssh_key": pub_key},
                base_url=self.base_url,
            )
        except CrigError:
            pass
        return new_id

    def attach_ssh_key(self, identifier: str, public_key: str) -> None:
        summary = f"SSH ACCESS {identifier}\n  Attach a local SSH key so VS Code can connect."
        action_key = vault.unlock_action_key(summary)
        api_call(
            "POST",
            f"/api/v0/instances/{quote(identifier, safe='')}/ssh/",
            action_key,
            {"ssh_key": public_key.strip()},
            base_url=self.base_url,
        )

    def stop(self, target: Instance | str) -> None:
        item = target if isinstance(target, Instance) else self.instance(target)
        summary = (
            f"STOP {item.name} ({item.id})   {gpu_label(item.gpu, item.count)}\n"
            "  Compute billing stops; storage charges may continue until deleted."
        )
        action_key = vault.unlock_action_key(summary)
        api_call(
            "PUT",
            f"/api/v0/instances/{quote(item.id, safe='')}/",
            action_key,
            {"state": "stopped"},
            base_url=self.base_url,
        )

    def destroy(self, target: Instance | str) -> None:
        item = target if isinstance(target, Instance) else self.instance(target)
        summary = (
            f"DESTROY {item.name} ({item.id})   {gpu_label(item.gpu, item.count)}\n"
            "  All files on this GPU will be permanently deleted."
        )
        action_key = vault.unlock_action_key(summary)
        api_call(
            "DELETE",
            f"/api/v0/instances/{quote(item.id, safe='')}/",
            action_key,
            base_url=self.base_url,
        )
        connections.cleanup_instance(item.id)
