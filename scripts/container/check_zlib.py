"""Exercise the retained patched zlib, including repeated failed gzip writes."""

import ctypes
import gzip
import hashlib
import json
import platform
import sys
import zlib
from datetime import UTC, datetime
from pathlib import Path


def main():
    status = Path("/var/lib/dpkg/status").read_text()
    record = next(p for p in status.split("\n\n") if p.startswith("Package: zlib1g\n"))
    assert "\nVersion: 1:1.3.dfsg+really1.3.2-4\n" in record + "\n"
    assert zlib.ZLIB_RUNTIME_VERSION == "1.3.2"
    payload = bytes(range(256)) * 1024
    assert zlib.decompress(zlib.compress(payload)) == payload
    assert gzip.decompress(gzip.compress(payload)) == payload

    library = ctypes.CDLL("libz.so.1")
    library.gzopen.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    library.gzopen.restype = ctypes.c_void_p
    library.gzbuffer.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    library.gzwrite.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    library.gzwrite.restype = ctypes.c_int
    library.gzclearerr.argtypes = [ctypes.c_void_p]
    library.gzclearerr.restype = None
    library.gzclose.argtypes = [ctypes.c_void_p]
    library.gzclose.restype = ctypes.c_int
    # /dev/full reliably fails writes with ENOSPC. Repeated gzwrite calls after
    # clearing that error exercise the stale input-pointer path behind the CVE.
    handle = library.gzopen(b"/dev/full", b"wb0")
    assert handle
    try:
        assert library.gzbuffer(handle, 128) == 0
        for _ in range(8):
            buffer = ctypes.create_string_buffer(payload)
            assert library.gzwrite(handle, buffer, len(payload)) == 0
            library.gzclearerr(handle)
    finally:
        assert library.gzclose(handle) <= 0
    if "--vex" in sys.argv:
        # Emit only after version, actual native calls and error recovery pass.
        # Exact version/architecture scope cannot match the old production zlib.
        arch = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
        distro = Path("/etc/debian_version").read_text().strip()
        library_path = next(Path("/usr/lib").glob("*/libz.so.1"), None)
        if library_path is None:
            library_path = next(Path("/lib").glob("*/libz.so.1"))
        digest = hashlib.sha256(library_path.read_bytes()).hexdigest()
        purl = f"pkg:deb/debian/zlib1g@1.3.dfsg%2Breally1.3.2-4?arch={arch}&distro=debian-{distro}&epoch=1"
        document = {
            "@context": "https://openvex.dev/ns/v0.2.0",
            "@id": f"https://github.com/marcnester/glycofy-api/zlib-fixed/{digest}",
            "author": "Glycofy CI: verified Debian source rebuild",
            "timestamp": datetime.now(UTC).isoformat(),
            "version": 1,
            "statements": [
                {
                    "vulnerability": {"name": cve},
                    "products": [{"@id": purl}],
                    "status": "fixed",
                    "status_notes": "Debian source 1:1.3.dfsg+really1.3.2-4 includes upstream df84af25dc1942490e1d1c899a07619152a46148. Source archives are checksum-pinned in Dockerfile. Runtime version, compression and repeated failed-write checks passed. libz SHA256="
                    + digest,
                }
                for cve in ("CVE-2026-76844", "CVE-2026-85091")
            ],
        }
        print(json.dumps(document, indent=2))
    else:
        print("Patched zlib metadata, compression and failed-write checks passed")


if __name__ == "__main__":
    main()
