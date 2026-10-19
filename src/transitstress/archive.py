"""Fixed public MAST API transport. This module is used only by explicit fetch."""

import hashlib
import http.client
import ipaddress
import json
import multiprocessing
import re
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from multiprocessing.connection import Connection
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from transitstress.errors import DataValidationError
from transitstress.provenance import canonical_json

HOST = "mast.stsci.edu"
PRODUCT_HOST = "stpubdata.s3.us-east-1.amazonaws.com"
HOSTS = (HOST, PRODUCT_HOST)
INVOKE = f"https://{HOST}/api/v0/invoke"
DOWNLOAD = f"https://{HOST}/api/v0.1/Download/file"
FILENAME = re.compile(r"tess[0-9]{13}-s0001-[0-9]{16}-[0-9]{4}-[xsab]_lc\.fits\Z")
URI_PREFIX = "mast:TESS/product/"
MAX_PRODUCT = 50 * 1024**2
MAX_TOTAL = 1024**3
MAX_DOWNLOADS = 30
MAX_METADATA = 16 * 1024**2
REQUEST_SECONDS = 180.0
SOCKET_SECONDS = 20.0
RESPONSE_SECONDS = 60.0
CHUNK = 64 * 1024
SERVER_SECONDS = 10
DNS_BYTES = 8192


def _dns_worker(output: Connection, host: str = HOST) -> None:
    """Fixed-host child sends bounded JSON bytes, never a pickled result or raw exception."""
    try:
        if host not in HOSTS:
            output.send_bytes(b"[]")
            return
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not 0 < len(addresses) <= 64:
            output.send_bytes(b"[]")
        else:
            payload = json.dumps([a[4][0] for a in addresses]).encode("ascii")
            output.send_bytes(payload if len(payload) <= DNS_BYTES else b"[]")
    except OSError:
        try:
            output.send_bytes(b"[]")
        except OSError:
            pass  # Parent timeout may have already closed the pipe.
    finally:
        output.close()


def _public_address(timeout: float, *, host: str = HOST) -> str:
    """One spawned fixed-target resolver; timeout terminates and joins the worker.

    Only send_bytes/recv_bytes carry a bounded JSON list. Process spawning uses
    trusted application code, not an input-supplied executable, shell or pickle.
    Cleanup has two additional one-second join bounds, including forced kill.
    """
    if host not in HOSTS:
        raise DataValidationError("archive DNS: unsupported host; retain the fixed host allowlist")
    context = multiprocessing.get_context("spawn")
    incoming, outgoing = context.Pipe(duplex=False)
    resolver = context.Process(target=_dns_worker, args=(outgoing, host), daemon=True)
    started = False
    try:
        deadline = time.monotonic() + timeout
        resolver.start()
        started = True
        outgoing.close()
        if not incoming.poll(max(0.0, deadline - time.monotonic())):
            raise TimeoutError()
        addresses = json.loads(incoming.recv_bytes(DNS_BYTES))
        if (
            type(addresses) is not list
            or not 0 < len(addresses) <= 64
            or any(type(a) is not str or not ipaddress.ip_address(a).is_global for a in addresses)
        ):
            raise ValueError()
    except (OSError, EOFError, ValueError) as error:
        raise DataValidationError(
            "archive DNS: timeout/invalid/private answer; check trusted network "
            "and retry explicitly"
        ) from error
    finally:
        outgoing.close()
        incoming.close()
        if started:
            if resolver.is_alive():
                resolver.terminate()
            resolver.join(timeout=1)
            if resolver.is_alive():
                resolver.kill()
                resolver.join(timeout=1)
            if resolver.is_alive():
                raise DataValidationError("archive DNS: worker cleanup failed; stop acquisition")
        resolver.close()
    return addresses[0]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to the validated numeric IP, retaining certificate and hostname verification."""

    def __init__(self, address: str, timeout: float, *, host: str = HOST):
        if host not in HOSTS:
            raise DataValidationError("TLS host: unsupported; retain the fixed host allowlist")
        super().__init__(host, timeout=timeout)
        self.address = address
        self.response_socket = None
        self._receiving_headers = False

    def getresponse(self) -> http.client.HTTPResponse:
        # HTTPConnection closes/detaches its socket for Connection: close. Keep
        # ownership until our response reader finishes so the watchdog can stop it.
        self._receiving_headers = True
        try:
            return super().getresponse()
        finally:
            self._receiving_headers = False

    def close(self) -> None:
        if self._receiving_headers and self.sock is not None:
            self.response_socket, self.sock = self.sock, None
        super().close()
        if not self._receiving_headers and self.response_socket is not None:
            self.response_socket.close()
            self.response_socket = None

    def connect(self) -> None:
        # Numeric addresses avoid a second DNS lookup/rebinding between validation and connect.
        raw = socket.socket(
            socket.AF_INET6 if ":" in self.address else socket.AF_INET, socket.SOCK_STREAM
        )
        raw.settimeout(self.timeout)
        self.sock = raw
        try:
            raw.connect((self.address, 443))
            self.sock = ssl.create_default_context().wrap_socket(
                raw, server_hostname=self.host, do_handshake_on_connect=False
            )
            self.sock.do_handshake()
        except OSError:
            self.close()
            raise


def _expire_connection(connection: http.client.HTTPSConnection) -> None:
    """Interrupt a trickling response at the total deadline, including header reads."""
    sock = connection.sock or getattr(connection, "response_socket", None)
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            return  # Normal request cleanup may already have closed this socket.


def product_uri(uri: str) -> str:
    """Only the documented TESS light-curve product namespace, no URL/path input."""
    if (
        type(uri) is not str
        or not uri.startswith(URI_PREFIX)
        or not FILENAME.fullmatch(uri[len(URI_PREFIX) :])
    ):
        raise DataValidationError(
            "product URI: unsupported identity; use pinned sector-1 TESS SPOC lc FITS"
        )
    return uri


def validate_endpoint(url: str, *, uri: str | None = None) -> None:
    """Validate the initial endpoint and every redirect before any destination request."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc not in HOSTS
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or any(ord(c) < 32 for c in url)
    ):
        raise DataValidationError(
            "archive endpoint: forbidden host/scheme; use the fixed HTTPS MAST API"
        )
    if uri is None:
        valid = parsed.netloc == HOST and parsed.path == "/api/v0/invoke" and not parsed.query
        if (
            parsed.netloc == HOST
            and parsed.path == "/api/v0/invoke"
            and parsed.query
            and len(url) <= 8192
        ):
            values = parse_qs(parsed.query)
            try:
                request = json.loads(values["request"][0])
                valid = (
                    set(values) == {"request"}
                    and len(values["request"]) == 1
                    and set(request)
                    == {"service", "params", "format", "pagesize", "page", "timeout"}
                    and request["service"] in ("Mast.Caom.Filtered", "Mast.Caom.Products")
                    and type(request["params"]) is dict
                    and request["format"] == "json"
                    and type(request["pagesize"]) is int
                    and 1 <= request["pagesize"] <= 1000
                    and type(request["timeout"]) is int
                    and request["timeout"] == SERVER_SECONDS
                    and type(request["page"]) is int
                    and 1 <= request["page"] <= 40
                )
            except (KeyError, ValueError, TypeError):
                valid = False
    else:
        valid = (
            parsed.netloc == HOST
            and parsed.path == "/api/v0.1/Download/file"
            and parse_qs(parsed.query) == {"uri": [product_uri(uri)]}
        )
        if parsed.netloc == PRODUCT_HOST:
            name = product_uri(uri)[len(URI_PREFIX) :]
            tic = name.split("-")[2]
            path = "/tess/public/tid/s0001/" + "/".join(tic[i : i + 4] for i in range(0, 16, 4))
            valid = parsed.path == path + "/" + name and not parsed.query
    if not valid:
        raise DataValidationError(
            "archive endpoint: forbidden path/query; retain the fixed API and product identity"
        )


@dataclass
class AcquisitionBudget:
    """Actual bytes include failed responses and metadata; downloads include failed attempts."""

    downloaded_before: int = 0
    downloads: int = 0
    bytes_received: int = 0

    def begin_download(self, declared: int) -> None:
        if type(declared) is not int or not 0 < declared <= MAX_PRODUCT:
            raise DataValidationError(
                "product size: missing/over 50 MiB; resolve bounded archive metadata"
            )
        if self.downloaded_before + self.downloads >= MAX_DOWNLOADS:
            raise DataValidationError(
                "download cap: 30 reached; preserve evidence and request a protocol amendment"
            )
        if self.bytes_received + declared > MAX_TOTAL:
            raise DataValidationError(
                "acquisition bytes: 1 GiB would be exceeded; stop this invocation"
            )
        self.downloads += 1

    def add(self, count: int) -> None:
        self.bytes_received += count
        if self.bytes_received > MAX_TOTAL:
            raise DataValidationError("acquisition bytes: 1 GiB exceeded; stop this invocation")


@dataclass
class _RequestWindow:
    deadline: float
    transmissions: int = 0

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise DataValidationError("archive time: deadline exceeded; retry explicitly later")
        return remaining

    def begin(self) -> None:
        self.remaining()
        if self.transmissions >= 3:
            raise DataValidationError("archive metadata/redirects: three transmissions exhausted")
        self.transmissions += 1


class MastArchive:
    """No transport retries; at most three transmissions including redirects/polls.

    TLS uses a verified default context. Environment proxy/auth settings are not
    read. DNS/connect use 20 seconds, response reads use 60 seconds, and a timer
    interrupts active I/O at the 180-second total deadline (including redirects).
    """

    def __init__(self, budget: AcquisitionBudget | None = None, *, offline: bool = False):
        self.budget = budget if budget is not None else AcquisitionBudget()
        self.offline = offline
        self.diagnostics: list[dict] = []

    def _request(
        self,
        url: str,
        body: bytes | None,
        *,
        limit: int,
        uri: str | None = None,
        _window: _RequestWindow | None = None,
    ) -> bytes:
        if self.offline:
            raise DataValidationError(
                "archive: offline with missing cache; rerun explicit fetch on"
                "line for pinned products"
            )
        window = _window or _RequestWindow(time.monotonic() + REQUEST_SECONDS)
        original_url = url
        stage = "DNS"
        try:
            while window.transmissions < 3:
                validate_endpoint(url, uri=uri)
                window.begin()
                stage = "DNS"
                started = time.monotonic()
                event = {
                    "method": "POST" if body is not None else "GET",
                    "transmission": window.transmissions,
                    "http_status": None,
                    "body_bytes": 0,
                }
                self.diagnostics.append(event)
                try:
                    hostname = urlsplit(url).hostname
                    dns_timeout = min(SOCKET_SECONDS, window.remaining())
                    address = (
                        _public_address(dns_timeout)
                        if hostname == HOST
                        else _public_address(dns_timeout, host=hostname)
                    )
                except DataValidationError:
                    event.update(stage=stage, elapsed_seconds=time.monotonic() - started)
                    raise
                remaining = window.remaining()
                connection = (
                    _PinnedHTTPSConnection(address, timeout=min(SOCKET_SECONDS, remaining))
                    if hostname == HOST
                    else _PinnedHTTPSConnection(
                        address, timeout=min(SOCKET_SECONDS, remaining), host=hostname
                    )
                )
                timer = threading.Timer(remaining, _expire_connection, args=(connection,))
                timer.daemon = True
                timer.start()
                response = None
                try:
                    stage = "connect/TLS/request"
                    parsed = urlsplit(url)
                    target = parsed.path + ("?" + parsed.query if parsed.query else "")
                    headers = {
                        "Accept-Encoding": "identity",
                        "User-Agent": "TransitStress/0.1 (public TESS acquisition)",
                    }
                    if body is not None:
                        headers["Content-Type"] = "application/x-www-form-urlencoded"
                    connection.request(
                        "POST" if body is not None else "GET", target, body=body, headers=headers
                    )
                    if connection.sock is not None:
                        connection.sock.settimeout(min(RESPONSE_SECONDS, window.remaining()))
                    stage = "response headers"
                    response = connection.getresponse()
                    window.remaining()
                    event["http_status"] = response.status
                    if response.status in (301, 302, 303, 307, 308):
                        location = response.getheader("Location")
                        if not location or (body is not None and response.status not in (307, 308)):
                            raise DataValidationError(
                                "archive redirect: missing/unsafe method; retain fixed endpoint"
                            )
                        url = urljoin(url, location)
                        validate_endpoint(url, uri=uri)
                        if uri is None and url != original_url:
                            raise DataValidationError(
                                "archive redirect: metadata query changed; retain exact identity"
                            )
                        continue
                    if response.status != 200:
                        raise DataValidationError(
                            f"archive HTTP: status {response.status}; retry pinned identity later"
                        )
                    encoding = response.getheader("Content-Encoding")
                    if encoding not in (None, "identity"):
                        raise DataValidationError(
                            "archive encoding: compressed response rejected; require boun"
                            "ded identity bytes"
                        )
                    length = response.getheader("Content-Length")
                    if length is not None and (not length.isdigit() or int(length) > limit):
                        raise DataValidationError(
                            "archive Content-Length: invalid/oversized; reject product be"
                            "fore reading"
                        )
                    output = bytearray()
                    stage = "response bytes"
                    while True:
                        remaining = window.remaining()
                        active = connection.sock or getattr(connection, "response_socket", None)
                        if active is not None:
                            active.settimeout(min(RESPONSE_SECONDS, remaining))
                        remaining_bytes = MAX_TOTAL - self.budget.bytes_received
                        chunk = response.read1(
                            min(CHUNK, limit + 1 - len(output), remaining_bytes + 1)
                        )
                        self.budget.add(len(chunk))
                        event["body_bytes"] += len(chunk)
                        window.remaining()  # Includes slow EOF, not just nonempty chunks.
                        if not chunk:
                            break
                        output.extend(chunk)
                        if len(output) > limit:
                            raise DataValidationError(
                                "archive streamed bytes: limit exceeded; reject oversized response"
                            )
                    if length is not None and len(output) != int(length):
                        raise DataValidationError(
                            "archive bytes: truncated response; retry the pinned product later"
                        )
                    return bytes(output)
                finally:
                    timer.cancel()
                    if response is not None:
                        response.close()
                    connection.close()
                    event.update(stage=stage, elapsed_seconds=time.monotonic() - started)
        except (OSError, http.client.HTTPException) as error:
            raise DataValidationError(
                f"archive connection ({stage}): unavailable/timed out; retry pinned request later"
            ) from error
        raise DataValidationError(
            "archive redirects: limit exceeded; inspect official endpoint before retrying"
        )

    def query(self, service: str, params: dict[str, object], *, pagesize: int = 1000) -> list[dict]:
        """Poll identical requests within a shared page deadline; verify complete row totals."""
        if service not in ("Mast.Caom.Filtered", "Mast.Caom.Products"):
            raise DataValidationError(
                "archive service: unsupported; use fixed observations/products services"
            )
        if type(pagesize) is not int or not 1 <= pagesize <= 1000:
            raise DataValidationError("metadata pagesize: require an integer from 1 to 1000")
        rows: list[dict] = []
        expected_totals = None
        for page in range(1, 41):
            request = {
                "service": service,
                "params": params,
                "format": "json",
                "pagesize": pagesize,
                "page": page,
                "timeout": SERVER_SECONDS,
            }
            query = urlencode({"request": canonical_json(request).decode("ascii")})
            window = _RequestWindow(time.monotonic() + REQUEST_SECONDS)
            try:
                while True:
                    if window.transmissions >= 3:
                        raise ValueError("poll exhaustion")
                    payload = self._request(
                        INVOKE + "?" + query, None, limit=MAX_METADATA, _window=window
                    )
                    result = json.loads(payload)
                    if type(result) is not dict:
                        raise ValueError("response object")
                    if result.get("status") == "EXECUTING":
                        continue
                    break
                if result["status"] != "COMPLETE" or type(result["data"]) is not list:
                    raise ValueError("incomplete")
                paging = result["paging"]
                names = ("page", "pageSize", "pagesFiltered", "rows", "rowsFiltered", "rowsTotal")
                if any(type(paging[n]) is not int or paging[n] < 0 for n in names):
                    raise ValueError("paging")
                total = paging["rowsFiltered"]
                pages = paging["pagesFiltered"]
                expected_pages = (total + pagesize - 1) // pagesize
                if (
                    total > 40000
                    or pages > 40
                    or paging["page"] != page
                    or paging["pageSize"] != pagesize
                    or paging["rowsTotal"] < total
                    or pages not in ({0, 1} if total == 0 else {expected_pages})
                    or paging["rows"] != len(result["data"])
                    or len(result["data"]) != min(pagesize, max(0, total - len(rows)))
                ):
                    raise ValueError("partial paging")
                totals = (pages, total, paging["rowsTotal"])
                if expected_totals is not None and totals != expected_totals:
                    raise ValueError("changing inventory")
                expected_totals = totals
                if not all(type(r) is dict for r in result["data"]):
                    raise ValueError("rows")
                rows.extend(result["data"])
                if page >= pages:
                    if len(rows) != total:
                        raise ValueError("incomplete total")
                    return rows
            except DataValidationError:
                raise
            except (ValueError, KeyError, TypeError) as error:
                raise DataValidationError(
                    "archive metadata: invalid/incomplete response; preserve evid"
                    "ence and retry query"
                ) from error
        raise DataValidationError(
            "archive inventory: page cap exceeded; inspect query without widening protocol"
        )

    def download(self, uri: str, declared_size: int) -> bytes:
        product_uri(uri)
        if self.offline:
            raise DataValidationError(
                "archive: offline with missing cache; restore pinned source or fetch online"
            )
        self.budget.begin_download(declared_size)
        result = self._request(
            DOWNLOAD + "?" + urlencode({"uri": uri}), None, limit=MAX_PRODUCT, uri=uri
        )
        if len(result) != declared_size:
            raise DataValidationError(
                "product bytes: differ from pinned metadata size; preserve fa"
                "ilure without refreshing identity"
            )
        return result


def source_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
