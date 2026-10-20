"""Restricted reader for the fixed, developer-generated T04 numeric evidence."""

import io
import zipfile
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from transitstress import acquisition as acq
from transitstress.errors import DataValidationError


def load_arrays(root: Path, name: str, checksum: str) -> dict[str, NDArray]:
    """Bound ZIP members before NumPy loading; never extract or deserialize objects."""
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
        with np.load(io.BytesIO(content), allow_pickle=False, max_header_size=512) as stored:
            arrays = {key: stored[key] for key in sizes}
        for key, array in arrays.items():
            integer = key.endswith(("neighbor_counts", "segment_ids"))
            if array.dtype.kind != ("i" if integer else "f") or array.dtype.itemsize != 8:
                raise ValueError("dtype")
            size = len(arrays["time_days"] if sizes[key] == "time" else arrays["period_days"])
            if array.ndim != 1 or not 5 <= size <= 50000 or array.shape != (size,):
                raise ValueError("shape")
    except (ValueError, KeyError, zipfile.BadZipFile, EOFError) as error:
        raise DataValidationError(
            "T04 arrays: invalid numeric schema; restore generated evidence"
        ) from error
    return arrays
