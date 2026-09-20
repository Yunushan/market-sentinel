from __future__ import annotations

import gzip
import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.normalize_python_sdist import normalize_sdist, source_date_epoch
from scripts.verify_reproducible_python_dist import verify_reproducible_distributions


def _write_sdist(path: Path, *, gzip_mtime: int, member_mtime: int, reverse: bool) -> None:
    members = [
        ("market_sentinel-1.2.3", None),
        ("market_sentinel-1.2.3/PKG-INFO", b"Name: market-sentinel\nVersion: 1.2.3\n"),
        ("market_sentinel-1.2.3/module.py", b"VALUE = 1\n"),
    ]
    if reverse:
        members.reverse()
    with path.open("wb") as raw:
        with gzip.GzipFile(filename=path.name, mode="wb", fileobj=raw, mtime=gzip_mtime) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for name, content in members:
                    info = tarfile.TarInfo(name)
                    info.mtime = member_mtime
                    if content is None:
                        info.type = tarfile.DIRTYPE
                        info.mode = 0o700
                        archive.addfile(info)
                    else:
                        info.size = len(content)
                        info.mode = 0o600
                        archive.addfile(info, io.BytesIO(content))


class PythonDistributionReproducibilityTests(unittest.TestCase):
    def test_sdist_normalization_removes_order_and_timestamp_variance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.tar.gz"
            second = root / "second.tar.gz"
            _write_sdist(first, gzip_mtime=100, member_mtime=200, reverse=False)
            _write_sdist(second, gzip_mtime=300, member_mtime=400, reverse=True)

            normalize_sdist(first, epoch=1_700_000_000)
            normalize_sdist(second, epoch=1_700_000_000)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            with tarfile.open(first, "r:gz") as archive:
                self.assertEqual(
                    [member.name for member in archive.getmembers()],
                    [
                        "market_sentinel-1.2.3",
                        "market_sentinel-1.2.3/PKG-INFO",
                        "market_sentinel-1.2.3/module.py",
                    ],
                )
                self.assertEqual(
                    {member.mtime for member in archive.getmembers()},
                    {1_700_000_000},
                )
                payload = archive.extractfile("market_sentinel-1.2.3/module.py")
                self.assertIsNotNone(payload)
                assert payload is not None
                self.assertEqual(payload.read(), b"VALUE = 1\n")

    def test_distribution_comparison_requires_exact_names_and_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            names = ("market_sentinel-1.2.3-py3-none-any.whl", "market_sentinel-1.2.3.tar.gz")
            for name in names:
                (first / name).write_bytes(f"bytes:{name}".encode())
                (second / name).write_bytes(f"bytes:{name}".encode())

            verify_reproducible_distributions(first, second)
            (second / names[0]).write_bytes(b"different wheel")
            with self.assertRaisesRegex(ValueError, "not byte-reproducible"):
                verify_reproducible_distributions(first, second)

    def test_epoch_is_required_and_bounded(self) -> None:
        with self.assertRaisesRegex(ValueError, "is required"):
            with patch.dict("os.environ", {}, clear=True):
                source_date_epoch()
        with self.assertRaisesRegex(ValueError, "gzip timestamp"):
            source_date_epoch(-1)


if __name__ == "__main__":
    unittest.main()
