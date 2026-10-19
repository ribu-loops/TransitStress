"""Fixed public MAST API transport. This module is used only by explicit fetch."""

import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from transitstress.errors import DataValidationError
from transitstress.provenance import canonical_json

HOST = "mast.stsci.edu"
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


def _public_address(timeout: float) -> str:
    """Bound the caller's DNS wait, validate all answers, then pin one numeric address."""
    resolver = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mast-dns")
    try:
        addresses = resolver.submit(socket.getaddrinfo, HOST, 443, type=socket.SOCK_STREAM).result(
            timeout=timeout
        )
    except TimeoutError as error:
        raise DataValidationError(
            "archive DNS: timeout; retry explicit acquisition later"
        ) from error
    finally:
        resolver.shutdown(wait=False, cancel_futures=True)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise DataValidationError(
            "archive DNS: private/local address rejected; check trusted network configuration"
        )
    return addresses[0][4][0]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to the validated numeric IP, retaining certificate and hostname verification."""

    def __init__(self, address: str, timeout: float):
        super().__init__(HOST, timeout=timeout)
        self.address = address

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
                raw, server_hostname=HOST, do_handshake_on_connect=False
            )
            self.sock.do_handshake()
        except OSError:
            self.close()
            raise


def _expire_connection(connection: http.client.HTTPSConnection) -> None:
    """Interrupt a trickling response at the total deadline, including header reads."""
    sock = connection.sock
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
        or parsed.netloc != HOST
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or any(ord(c) < 32 for c in url)
    ):
        raise DataValidationError(
            "archive endpoint: forbidden host/scheme; use the fixed HTTPS MAST API"
        )
    if uri is None:
        valid = parsed.path == "/api/v0/invoke" and not parsed.query
        if parsed.path == "/api/v0/invoke" and parsed.query and len(url) <= 8192:
            values = parse_qs(parsed.query)
            try:
                request = json.loads(values["request"][0])
                valid = (
                    set(values) == {"request"}
                    and len(values["request"]) == 1
                    and set(request) == {"service", "params", "format", "pagesize", "page"}
                    and request["service"] in ("Mast.Caom.Filtered", "Mast.Caom.Products")
                    and type(request["params"]) is dict
                    and request["format"] == "json"
                    and request["pagesize"] == 1000
                    and type(request["page"]) is int
                    and 1 <= request["page"] <= 40
                )
            except (KeyError, ValueError, TypeError):
                valid = False
    else:
        valid = parsed.path == "/api/v0.1/Download/file" and parse_qs(parsed.query) == {
            "uri": [product_uri(uri)]
        }
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


class MastArchive:
    """One attempt per request (no automatic retries), at most two safe redirects.

    TLS uses a verified default context. Environment proxy/auth settings are not
    read. DNS/connect use 20 seconds, response reads use 60 seconds, and a timer
    interrupts active I/O at the 180-second total deadline (including redirects).
    """

    def __init__(self, budget: AcquisitionBudget | None = None, *, offline: bool = False):
        self.budget = budget if budget is not None else AcquisitionBudget()
        self.offline = offline

    def _request(
        self, url: str, body: bytes | None, *, limit: int, uri: str | None = None
    ) -> bytes:
        if self.offline:
            raise DataValidationError(
                "archive: offline with missing cache; rerun explicit fetch on"
                "line for pinned products"
            )
        deadline = time.monotonic() + REQUEST_SECONDS
        original_url = url
        stage = "DNS"
        try:
            for _ in range(3):
                validate_endpoint(url, uri=uri)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DataValidationError(
                        "archive time: deadline exceeded; retry explicit fetch later"
                    )
                address = _public_address(min(SOCKET_SECONDS, remaining))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DataValidationError(
                        "archive time: deadline exceeded; retry explicit fetch later"
                    )
                connection = _PinnedHTTPSConnection(address, timeout=min(SOCKET_SECONDS, remaining))
                timer = threading.Timer(remaining, _expire_connection, args=(connection,))
                timer.daemon = True
                timer.start()
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
                        connection.sock.settimeout(
                            min(RESPONSE_SECONDS, max(0.001, deadline - time.monotonic()))
                        )
                    stage = "response headers"
                    response = connection.getresponse()
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
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise DataValidationError(
                                "archive time: deadline exceeded; retry explicit fetch later"
                            )
                        if connection.sock is not None:
                            connection.sock.settimeout(min(RESPONSE_SECONDS, remaining))
                        remaining_bytes = MAX_TOTAL - self.budget.bytes_received
                        chunk = response.read1(
                            min(CHUNK, limit + 1 - len(output), remaining_bytes + 1)
                        )
                        self.budget.add(len(chunk))
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
                    connection.close()
        except (OSError, http.client.HTTPException) as error:
            raise DataValidationError(
                f"archive connection ({stage}): unavailable/timed out; retry pinned request later"
            ) from error
        raise DataValidationError(
            "archive redirects: limit exceeded; inspect official endpoint before retrying"
        )

    def query(self, service: str, params: dict[str, object]) -> list[dict]:
        """Bound pages and rows; never accept a partial/EXECUTING inventory as complete."""
        if service not in ("Mast.Caom.Filtered", "Mast.Caom.Products"):
            raise DataValidationError(
                "archive service: unsupported; use fixed observations/products services"
            )
        rows: list[dict] = []
        expected_pages = None
        for page in range(1, 41):
            request = {
                "service": service,
                "params": params,
                "format": "json",
                "pagesize": 1000,
                "page": page,
            }
            query = urlencode({"request": canonical_json(request).decode("ascii")})
            payload = self._request(INVOKE + "?" + query, None, limit=MAX_METADATA)
            try:
                result = json.loads(payload)
                if result["status"] != "COMPLETE" or type(result["data"]) is not list:
                    raise ValueError("incomplete")
                paging = result["paging"]
                pages = paging["pagesFiltered"]
                if type(pages) is not int or not 0 <= pages <= 40 or paging["page"] != page:
                    raise ValueError("paging")
                if expected_pages is not None and pages != expected_pages:
                    raise ValueError("changing inventory")
                expected_pages = pages
                if len(result["data"]) > 1000 or not all(type(r) is dict for r in result["data"]):
                    raise ValueError("rows")
                rows.extend(result["data"])
                if page >= pages:
                    return rows
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
