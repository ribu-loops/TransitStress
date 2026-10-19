import json
from urllib.parse import urlencode

import pytest

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

        def __init__(self, address, timeout):
            assert address == "130.167.201.60" and 0 < timeout <= 20

        def request(self, method, target, body, headers):
            requests.append((method, target, body, headers))

        def getresponse(self):
            return responses.pop(0)

        def close(self):
            pass

    monkeypatch.setattr("transitstress.archive._PinnedHTTPSConnection", Connection)
    monkeypatch.setattr(
        "transitstress.archive.socket.getaddrinfo",
        lambda *a, **kw: [(2, 1, 6, "", ("130.167.201.60", 443))],
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
    monkeypatch.setattr(
        "transitstress.archive.socket.getaddrinfo",
        lambda *a, **kw: [(2, 1, 6, "", ("10.0.0.1", 443))],
    )
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
                    "paging": {"page": n, "pagesFiltered": 2},
                    "data": [{"value": n}],
                }
            ).encode()
        )
        for n in (1, 2)
    )
    assert MastArchive().query("Mast.Caom.Products", {"obsid": "1"}) == [{"value": 1}, {"value": 2}]
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
        {"status": "EXECUTING", "data": []},
        {"status": "COMPLETE", "data": [], "paging": {"page": 1, "pagesFiltered": 41}},
        {"status": "COMPLETE", "data": [None], "paging": {"page": 1, "pagesFiltered": 1}},
    ],
)
def test_incomplete_or_oversized_inventory(connection_mock, result):
    responses, _ = connection_mock
    responses.append(Response(json.dumps(result).encode()))
    with pytest.raises(DataValidationError, match="metadata"):
        MastArchive().query("Mast.Caom.Products", {"obsid": "1"})
