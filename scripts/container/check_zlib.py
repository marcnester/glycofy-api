"""Exercise the retained patched zlib, including repeated failed gzip writes."""

import ctypes
import gzip
import zlib
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
    print("Patched zlib metadata, compression and failed-write checks passed")


if __name__ == "__main__":
    main()
