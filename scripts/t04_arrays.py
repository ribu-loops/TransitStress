"""Restricted reader for the fixed, developer-generated T04 numeric evidence."""

import io
import struct
import tokenize
import zipfile
from pathlib import Path
from typing import BinaryIO

import numpy as np
from numpy.typing import NDArray

from transitstress import acquisition as acq
from transitstress.errors import DataValidationError


def _header(stream: BinaryIO, member_size: int, integer: bool) -> tuple[tuple[int, ...], np.dtype]:
    """Parse only bounded metadata; never read a payload or allocate its declared shape."""
    version = np.lib.format.read_magic(stream)
    if version not in ((1, 0), (2, 0)):
        raise ValueError("unsupported NPY version")
    length_size = 2 if version == (1, 0) else 4
    raw_length = stream.read(length_size)
    if len(raw_length) != length_size:
        raise ValueError("truncated NPY header length")
    header_length = struct.unpack("<H" if length_size == 2 else "<I", raw_length)[0]
    if not 0 < header_length <= 512 or 8 + length_size + header_length > member_size:
        raise ValueError("oversized/truncated NPY header")
    # Public format helpers parse literal header metadata without reading an
    # array payload. The length is bounded before their own header read.
    stream.seek(8)
    reader = (
        np.lib.format.read_array_header_1_0
        if version == (1, 0)
        else np.lib.format.read_array_header_2_0
    )
    shape, _fortran_order, dtype = reader(stream, max_header_size=512)
    if (
        len(shape) != 1
        or type(shape[0]) is not int
        or not 5 <= shape[0] <= 50000
        or dtype.kind != ("i" if integer else "f")
        or dtype.itemsize != 8
        or dtype.hasobject
        or dtype.fields is not None
        or dtype.subdtype is not None
    ):
        raise ValueError("invalid NPY shape/dtype")
    # Python integer arithmetic is exact and occurs only after the count and
    # item size are bounded. Reject both short and surplus declared payloads.
    if member_size - stream.tell() != shape[0] * dtype.itemsize:
        raise ValueError("NPY declared payload does not match member length")
    return shape, dtype


def load_arrays(root: Path, name: str, checksum: str) -> dict[str, NDArray]:
    """Validate all ZIP/NPY metadata before loading any bounded numeric payload."""
    path = acq.safe_file(root, name)
    if not path.is_file() or path.stat().st_size > 8 * 1024**2:
        raise DataValidationError("T04 arrays: absent/oversized; restore generated evidence")
    content = path.read_bytes()
    if acq.source_sha256(content) != checksum:
        raise DataValidationError("T04 arrays: checksum mismatch; preserve corrupt evidence")
    sizes = {key: "time" for key in ("time_days", "flux", "flux_err")}
    sizes["period_days"] = "period"
    for pipeline in ("rm_0p5d", "rm_1p5d"):
        for key in ("epoch_days", "duration_days", "depth", "depth_err", "depth_snr", "power"):
            sizes[f"{pipeline}_{key}"] = "period"
        for key in ("flux", "flux_err", "trend", "neighbor_counts", "segment_ids"):
            sizes[f"{pipeline}_{key}"] = "time"
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if (
                len(members) != len(sizes)
                or {m.filename for m in members} != {f"{key}.npy" for key in sizes}
                or any(m.file_size > 50000 * 8 + 512 for m in members)
                or sum(m.file_size for m in members) > 16 * 1024**2
            ):
                raise ValueError("invalid members/sizes")
            headers = {}
            for key in sizes:
                member = archive.getinfo(f"{key}.npy")
                with archive.open(member) as stream:
                    headers[key] = _header(
                        stream, member.file_size, key.endswith(("neighbor_counts", "segment_ids"))
                    )
            for key, group in sizes.items():
                reference = "time_days" if group == "time" else "period_days"
                if headers[key][0] != headers[reference][0]:
                    raise ValueError("NPY cross-array length mismatch")
        with np.load(io.BytesIO(content), allow_pickle=False, max_header_size=512) as stored:
            arrays = {key: stored[key] for key in sizes}
        for key, array in arrays.items():
            if (array.shape, array.dtype) != headers[key]:
                raise ValueError("NPY decoded schema mismatch")
    except (
        ValueError,
        KeyError,
        TypeError,
        OverflowError,
        SyntaxError,
        tokenize.TokenError,
        zipfile.BadZipFile,
        EOFError,
        NotImplementedError,
        RuntimeError,
    ) as error:
        raise DataValidationError(
            "T04 arrays: invalid numeric schema; restore generated evidence"
        ) from error
    return arrays
