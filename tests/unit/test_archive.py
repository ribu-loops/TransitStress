import io
import json
import multiprocessing
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlencode, urlsplit

import pytest

import transitstress.archive as transport
from transitstress.archive import (
    DOWNLOAD,
    INVOKE,
    MAX_PRODUCT,
    MAX_TOTAL,
    AcquisitionBudget,
    MastArchive,
    product_uri,
    validate_endpoint,
)
from transitstress.errors import DataValidationError

URI = "mast:TESS/product/tess2018206045859-s0001-0000000000000001-0120-s_lc.fits"


class Response:
    def __init__(self, content=b"abc", status=200, headers=None):
        self.status = status
        self.headers = headers or {}
        self.content = content
        self.closed = False

    def close(self):
        self.closed = True

    def getheader(self, name):
        return self.headers.get(name)

    def read1(self, size):
        data, self.content = self.content[:size], self.content[size:]
        return data


@pytest.fixture
def connection_mock(monkeypatch):
    responses, requests = [], []

    class Connection:
        sock = None

        def __init__(self, address, timeout, *, host=transport.HOST):
            assert address == "130.167.201.60" and 0 < timeout <= 20
            assert host in transport.HOSTS

        def request(self, method, target, body, headers):
            requests.append((method, target, body, headers))

        def getresponse(self):
            return responses.pop(0)

        def close(self):
            pass

    monkeypatch.setattr("transitstress.archive._PinnedHTTPSConnection", Connection)
    monkeypatch.setattr(
        "transitstress.archive._public_address",
        lambda *a, **kw: "130.167.201.60",
    )
    return responses, requests


@pytest.mark.parametrize(
    "url",
    [
        "http://mast.stsci.edu/api/v0/invoke",
        "https://mast.stsci.edu.evil.test/api/v0/invoke",
        "https://127.0.0.1/api/v0/invoke",
        "https://169.254.169.254/latest/meta-data/",
        "https://[::1]/api/v0/invoke",
        "https://mast.stsci.edu:443/api/v0/invoke",
        "https://user@mast.stsci.edu/api/v0/invoke",
        "https://mast.stsci.edu/api/v0/invoke#fragment",
        "https://mast.stsci.edu/other",
        "https://mast.stsci.edu/api/v0/invoke?url=evil",
    ],
)
def test_only_fixed_endpoint(url):
    with pytest.raises(DataValidationError):
        validate_endpoint(url)


def test_uri_identity_and_query():
    assert product_uri(URI) == URI
    for mitigation in "xsab":
        assert product_uri(URI.replace("-s_lc.fits", f"-{mitigation}_lc.fits"))
    validate_endpoint(DOWNLOAD + "?" + urlencode({"uri": URI}), uri=URI)
    for value in (
        "https://evil.test/f.fits",
        "mast:TESS/product/../../a",
        URI.replace("s0001", "s0002"),
        URI + "?extra",
    ):
        with pytest.raises(DataValidationError):
            product_uri(value)
    with pytest.raises(DataValidationError):
        validate_endpoint(DOWNLOAD + "?" + urlencode({"uri": URI, "extra": "1"}), uri=URI)


def test_download_counts_actual_bytes_and_fixed_request(connection_mock):
    responses, requests = connection_mock
    responses.append(Response(headers={"Content-Length": "3"}))
    archive = MastArchive()
    assert archive.download(URI, 3) == b"abc"
    assert archive.budget.downloads == 1 and archive.budget.bytes_received == 3
    assert requests[0][0] == "GET"
    assert requests[0][3]["Accept-Encoding"] == "identity"


@pytest.mark.parametrize(
    "headers", [{"Content-Length": "999"}, {"Content-Length": "NaN"}, {"Content-Encoding": "gzip"}]
)
def test_declared_limits_before_read(connection_mock, headers):
    responses, _ = connection_mock
    response = Response(headers=headers)
    responses.append(response)
    with pytest.raises(DataValidationError):
        MastArchive()._request(INVOKE, b"request", limit=5)
    assert response.content == b"abc"


def test_streamed_limit_without_content_length(connection_mock):
    responses, _ = connection_mock
    responses.append(Response(content=b"abcdef"))
    archive = MastArchive()
    with pytest.raises(DataValidationError, match="streamed bytes"):
        archive._request(INVOKE, b"request", limit=5)
    assert archive.budget.bytes_received == 6


def test_total_stream_budget_bounds_read_to_one_sentinel_byte(connection_mock):
    responses, _ = connection_mock
    response = Response(content=b"abcdef")
    responses.append(response)
    archive = MastArchive(AcquisitionBudget(bytes_received=MAX_TOTAL - 1))
    with pytest.raises(DataValidationError, match="1 GiB"):
        archive._request(INVOKE, b"request", limit=10)
    assert response.content == b"cdef"
    assert archive.budget.bytes_received == MAX_TOTAL + 1


@pytest.mark.parametrize(
    "destination",
    ["http://127.0.0.1/", "https://mast.stsci.edu.evil.test/", "https://169.254.169.254/"],
)
def test_redirect_rejected_before_destination_request(connection_mock, destination):
    responses, requests = connection_mock
    responses.append(Response(status=302, headers={"Location": destination}))
    with pytest.raises(DataValidationError, match="endpoint"):
        MastArchive().download(URI, 3)
    assert len(requests) == 1


def test_safe_same_identity_redirect_and_redirect_cap(connection_mock):
    responses, requests = connection_mock
    url = DOWNLOAD + "?" + urlencode({"uri": URI})
    responses.extend([Response(status=307, headers={"Location": url}), Response()])
    assert MastArchive().download(URI, 3) == b"abc"
    assert len(requests) == 2
    responses.extend([Response(status=307, headers={"Location": url}) for _ in range(3)])
    with pytest.raises(DataValidationError, match="redirects"):
        MastArchive().download(URI, 3)


def test_private_dns_and_offline_never_request(connection_mock, monkeypatch):
    _, requests = connection_mock

    def rejected(*args):
        raise DataValidationError("archive DNS: private answer rejected")

    monkeypatch.setattr("transitstress.archive._public_address", rejected)
    with pytest.raises(DataValidationError, match="DNS"):
        MastArchive().download(URI, 3)
    with pytest.raises(DataValidationError, match="offline"):
        MastArchive(offline=True).download(URI, 3)
    assert requests == []


def test_deadline_and_connection_failure(connection_mock, monkeypatch):
    responses, _ = connection_mock
    response = Response()
    responses.append(response)
    clock = [0.0]
    original = response.read1

    def delayed(size):
        clock[0] = 181.0
        return original(size)

    response.read1 = delayed
    monkeypatch.setattr("transitstress.archive.time.monotonic", lambda: clock[0])
    with pytest.raises(DataValidationError, match="deadline"):
        MastArchive().download(URI, 3)


def test_budget_no_network():
    for declared in (0, -1, MAX_PRODUCT + 1, True):
        with pytest.raises(DataValidationError, match="size"):
            AcquisitionBudget().begin_download(declared)
    with pytest.raises(DataValidationError, match="30"):
        AcquisitionBudget(downloaded_before=30).begin_download(1)
    with pytest.raises(DataValidationError, match="1 GiB"):
        AcquisitionBudget(bytes_received=MAX_TOTAL).begin_download(1)
    with pytest.raises(DataValidationError, match="1 GiB"):
        AcquisitionBudget(bytes_received=MAX_TOTAL).add(1)


def test_truncation_metadata_mismatch_and_timeout(connection_mock):
    responses, requests = connection_mock
    responses.append(Response(content=b"a", headers={"Content-Length": "3"}))
    with pytest.raises(DataValidationError, match="truncated"):
        MastArchive().download(URI, 3)
    responses.append(Response(content=b"abc"))
    with pytest.raises(DataValidationError, match="pinned metadata size"):
        MastArchive().download(URI, 4)
    response = Response()

    def timed_out(size):
        raise TimeoutError("mock timeout")

    response.read1 = timed_out
    responses.append(response)
    with pytest.raises(DataValidationError, match="connection"):
        MastArchive().download(URI, 3)
    assert len(requests) == 3  # No hidden automatic retry.


def test_paged_query(connection_mock):
    responses, requests = connection_mock
    responses.extend(
        Response(
            json.dumps(
                {
                    "status": "COMPLETE",
                    "paging": {
                        "page": n,
                        "pageSize": 1,
                        "pagesFiltered": 2,
                        "rows": 1,
                        "rowsFiltered": 2,
                        "rowsTotal": 2,
                    },
                    "data": [{"value": n}],
                }
            ).encode()
        )
        for n in (1, 2)
    )
    assert MastArchive().query("Mast.Caom.Products", {"obsid": "1"}, pagesize=1) == [
        {"value": 1},
        {"value": 2},
    ]
    assert len(requests) == 2
    assert all(r[0] == "GET" and r[1].startswith("/api/v0/invoke?request=") for r in requests)


def test_metadata_get_query_boundary_and_redirect_identity(connection_mock):
    responses, requests = connection_mock
    url = (
        INVOKE
        + "?"
        + urlencode(
            {
                "request": json.dumps(
                    {
                        "service": "Mast.Caom.Products",
                        "params": {"obsid": "1"},
                        "format": "json",
                        "pagesize": 1000,
                        "page": 1,
                        "timeout": 10,
                    }
                )
            }
        )
    )
    validate_endpoint(url)
    for suffix in ("?request=bad", "?request={}", "?request=1", "?request=[]"):
        with pytest.raises(DataValidationError):
            validate_endpoint(INVOKE + suffix)
    changed = url.replace("%221%22", "%222%22")
    assert changed != url
    responses.append(Response(status=302, headers={"Location": changed}))
    with pytest.raises(DataValidationError, match="query changed"):
        MastArchive()._request(url, None, limit=16)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "result",
    [
        {"status": "ERROR", "data": []},
        {"status": "COMPLETE", "data": [], "paging": {"page": 1, "pagesFiltered": 41}},
        {"status": "COMPLETE", "data": [None], "paging": {"page": 1, "pagesFiltered": 1}},
    ],
)
def test_incomplete_or_oversized_inventory(connection_mock, result):
    responses, _ = connection_mock
    responses.append(Response(json.dumps(result).encode()))
    with pytest.raises(DataValidationError, match="metadata"):
        MastArchive().query("Mast.Caom.Products", {"obsid": "1"})


def complete(data=None, **paging):
    data = [{"obsid": 1}] if data is None else data
    return {
        "status": "COMPLETE",
        "data": data,
        "paging": {
            "page": 1,
            "pageSize": 1000,
            "pagesFiltered": 1,
            "rows": len(data),
            "rowsFiltered": len(data),
            "rowsTotal": len(data),
            **paging,
        },
    }


def test_poll_identical_bytes_discards_executing_data(connection_mock):
    responses, requests = connection_mock
    responses.extend(
        Response(json.dumps(r).encode())
        for r in [{"status": "EXECUTING", "data": [{"not": "complete"}]}, complete()]
    )
    a = MastArchive()
    assert a.query("Mast.Caom.Filtered", {}) == [{"obsid": 1}]
    assert len(requests) == 2 and requests[0] == requests[1]
    assert [d["transmission"] for d in a.diagnostics] == [1, 2]
    assert all(d["body_bytes"] > 0 for d in a.diagnostics)


def test_poll_exhaustion_and_redirect_share_transmissions(connection_mock):
    responses, requests = connection_mock
    responses.extend(Response(b'{"status":"EXECUTING"}') for _ in range(3))
    with pytest.raises(DataValidationError, match="metadata"):
        MastArchive().query("Mast.Caom.Products", {"obsid": "1"})
    assert len(requests) == 3
    requests.clear()
    url = (
        INVOKE
        + "?"
        + urlencode(
            {
                "request": json.dumps(
                    {
                        "service": "Mast.Caom.Products",
                        "params": {"obsid": "1"},
                        "format": "json",
                        "page": 1,
                        "pagesize": 1000,
                        "timeout": 10,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            }
        )
    )
    responses.extend(
        [
            Response(status=307, headers={"Location": url}),
            Response(b'{"status":"EXECUTING"}'),
            Response(json.dumps(complete()).encode()),
        ]
    )
    assert MastArchive().query("Mast.Caom.Products", {"obsid": "1"}) == [{"obsid": 1}]
    assert len(requests) == 3


@pytest.mark.parametrize(
    "result",
    [
        [],
        {},
        {"status": "ERROR"},
        {"status": "unknown"},
        complete(rowsFiltered=2, rowsTotal=2),
        complete(rows=0),
        complete(pageSize=1),
        complete(rowsFiltered=True),
        complete(rowsTotal=0),
        complete(page=2),
        complete(pagesFiltered=2),
        complete(rowsFiltered=40001, pagesFiltered=41),
    ],
)
def test_complete_paging_counterexamples_rejected(connection_mock, result):
    responses, _ = connection_mock
    responses.append(Response(json.dumps(result).encode()))
    with pytest.raises(DataValidationError, match="metadata"):
        MastArchive().query("Mast.Caom.Filtered", {})


def test_missing_and_changing_paging_totals(connection_mock):
    responses, _ = connection_mock
    result = complete()
    del result["paging"]["rowsFiltered"]
    responses.append(Response(json.dumps(result).encode()))
    with pytest.raises(DataValidationError, match="metadata"):
        MastArchive().query("Mast.Caom.Filtered", {})
    responses.extend(
        Response(json.dumps(r).encode())
        for r in [
            complete(pageSize=1, pagesFiltered=2, rowsFiltered=2, rowsTotal=2),
            complete(page=2, pageSize=1, pagesFiltered=2, rowsFiltered=2, rowsTotal=3),
        ]
    )
    with pytest.raises(DataValidationError, match="metadata"):
        MastArchive().query("Mast.Caom.Filtered", {}, pagesize=1)


def test_polls_share_deadline_and_empty_inventory(connection_mock, monkeypatch):
    responses, requests = connection_mock
    clock = [0.0]

    def executing():
        clock[0] = 179.0
        return b'{"status":"EXECUTING"}'

    first = Response()
    first.read1 = lambda size: executing() if clock[0] == 0 else b""
    second = Response(json.dumps(complete()).encode())
    original = second.read1

    def late(size):
        clock[0] = 181.0
        return original(size)

    second.read1 = late
    responses.extend([first, second])
    monkeypatch.setattr(transport.time, "monotonic", lambda: clock[0])
    with pytest.raises(DataValidationError, match="deadline"):
        MastArchive().query("Mast.Caom.Filtered", {})
    assert len(requests) == 2


@pytest.mark.parametrize("pages", [0, 1])
def test_empty_inventory(connection_mock, pages):
    responses, _ = connection_mock
    responses.append(Response(json.dumps(complete([], pagesFiltered=pages)).encode()))
    assert MastArchive().query("Mast.Caom.Filtered", {}) == []


@pytest.mark.parametrize("will_close", [False, True])
@pytest.mark.parametrize("stage", ["headers", "body", "eof"])
def test_real_http_parser_watchdog_and_cleanup_without_network(monkeypatch, will_close, stage):
    released = threading.Event()
    real_monotonic = time.monotonic
    clock = [0.0]
    # The protocol clock advances inside I/O. Host scheduling before I/O must
    # not turn this watchdog test into a test of the pre-I/O deadline check.
    monkeypatch.setattr(transport.time, "monotonic", lambda: clock[0])

    class Reader(io.BytesIO):
        def readline(self, *args):
            if stage == "headers":
                assert released.wait(2), "watchdog failed to interrupt headers"
                clock[0] = 1.0
                raise OSError("controlled header interruption")
            return super().readline(*args)

        def read1(self, *args):
            assert released.wait(2), "watchdog failed to interrupt body"
            clock[0] = 1.0
            if stage == "body":
                raise OSError("controlled body interruption")
            return b""

    class Socket:
        def __init__(self):
            self.closed = False
            self.reader = Reader(
                b"HTTP/1.1 200 OK\r\n"
                + (b"Connection: close\r\n" if will_close else b"")
                + b"Content-Length: 3\r\n\r\nabc"
            )

        def sendall(self, data):
            pass

        def settimeout(self, value):
            pass

        def makefile(self, *args):
            return self.reader

        def shutdown(self, *args):
            released.set()

        def close(self):
            self.closed = True

    sock = Socket()
    monkeypatch.setattr(transport, "_public_address", lambda *args: "130.167.220.32")
    monkeypatch.setattr(
        transport._PinnedHTTPSConnection, "connect", lambda self: setattr(self, "sock", sock)
    )
    monkeypatch.setattr(transport, "REQUEST_SECONDS", 0.08)
    start = real_monotonic()
    with pytest.raises(DataValidationError, match="connection|deadline"):
        MastArchive()._request(INVOKE, None, limit=3)
    assert real_monotonic() - start < 1.0
    assert released.is_set() and sock.closed
    # For failed header parsing the parser itself closes the file object.
    assert sock.reader.closed


def test_dns_stalled_worker_terminated_and_joined(monkeypatch):
    context = multiprocessing.get_context("spawn")
    workers = []

    def process(**kwargs):
        assert kwargs["target"] is transport._dns_worker
        worker = context.Process(target=time.sleep, args=(30,), daemon=True)
        workers.append(worker)
        return worker

    monkeypatch.setattr(
        transport.multiprocessing,
        "get_context",
        lambda method: SimpleNamespace(Pipe=context.Pipe, Process=process),
    )
    start = time.monotonic()
    with pytest.raises(DataValidationError, match="DNS"):
        transport._public_address(0.1)
    assert time.monotonic() - start < 3
    assert len(workers) == 1 and workers[0]._closed


@pytest.mark.parametrize(
    "payload",
    [
        b"[]",
        b"{}",
        b'["10.0.0.1"]',
        b'["not-an-ip"]',
        b'["130.167.220.32","127.0.0.1"]',
        b'["130.167.220.32"]',
    ],
)
def test_dns_bounded_json_and_all_addresses_validated(monkeypatch, payload):
    class Pipe:
        def close(self):
            pass

        def poll(self, timeout):
            return True

        def recv_bytes(self, size):
            assert size == 8192
            return payload

    class Worker:
        def __init__(self, **kwargs):
            assert kwargs["target"] is transport._dns_worker

        def start(self):
            pass

        def is_alive(self):
            return False

        def join(self, timeout):
            assert timeout == 1

        def close(self):
            pass

    monkeypatch.setattr(
        transport.multiprocessing,
        "get_context",
        lambda method: SimpleNamespace(Pipe=lambda **kwargs: (Pipe(), Pipe()), Process=Worker),
    )
    if payload == b'["130.167.220.32"]':
        assert transport._public_address(1) == "130.167.220.32"
    else:
        with pytest.raises(DataValidationError, match="DNS"):
            transport._public_address(1)


def test_documented_cloud_redirect_is_exact_identity_and_counts_transmissions(connection_mock):
    responses, requests = connection_mock
    name = URI.split("/")[-1]
    cloud = f"https://{transport.PRODUCT_HOST}/tess/public/tid/s0001/0000/0000/0000/0001/{name}"
    validate_endpoint(cloud, uri=URI)
    responses.extend([Response(status=302, headers={"Location": cloud}), Response(b"abc")])
    archive = MastArchive()
    assert archive.download(URI, 3) == b"abc"
    assert len(requests) == 2 and archive.budget.downloads == 1
    assert requests[1][1] == urlsplit(cloud).path
    for url in (
        cloud.replace("https:", "http:"),
        cloud + "?other=1",
        cloud.replace(transport.PRODUCT_HOST, transport.PRODUCT_HOST + ".evil.test"),
        cloud.replace("/0001/", "/0002/"),
        cloud.replace("/tess/", "/hst/"),
        cloud.replace("/tid/", "/tid/../"),
        cloud.replace("/s0001/", "/s0002/"),
    ):
        with pytest.raises(DataValidationError):
            validate_endpoint(url, uri=URI)
    with pytest.raises(DataValidationError):
        validate_endpoint(f"https://{transport.PRODUCT_HOST}/api/v0/invoke")


def test_unknown_dns_and_tls_host_rejected_without_io():
    with pytest.raises(DataValidationError, match="host"):
        transport._public_address(1, host="arbitrary.test")
    with pytest.raises(DataValidationError, match="host"):
        transport._PinnedHTTPSConnection("130.167.201.60", timeout=1, host="arbitrary.test")
