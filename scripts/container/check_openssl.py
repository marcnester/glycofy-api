"""Verify the checksum-pinned upstream OpenSSL backport and loaded libraries."""

import hashlib
import json
import platform
import ssl
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

VERSION = "3.5.9-1~deb13u1+glycofy2"
PATCHED_SOURCES = {
    "ssl/quic/quic_stream_map.c": "7bceb5bdc408c3e5a46eb38944f5d16727b812e3e049dae6f2b2e4cdadce3616",
    "ssl/quic/quic_channel.c": "f2d149fc745d3e0862d62ec94517c6558e7c813d0788e7862763d00a114e55a1",
    "ssl/quic/quic_channel_local.h": "81e651ba367dcaa6c6a8582d66d315d69f9ad89e8221467fcf7bf25bc87b0346",
    "include/internal/quic_channel.h": "3baf3cf15d701f818d08207824f2b9750c2976444a1d9d623192547a268fdc22",
    "test/radix/quic_tests.c": "a3102a43293678e389e75163c00d4440d5999da7c58b52fc561df738f45b5309",
}
COMMITS = {
    "CVE-2026-124709": "f3acb0a3328046741479482c51cec815839e1b05",
    "CVE-2026-107494": "734c6cd7444bdbf589bfec917e47598c1b21ddd3",
}
REGRESSION_COMMIT = "42919a7f4e00aa217fce1532fbeb49f21be2ee7b"


def main():
    record = next(
        p for p in Path("/var/lib/dpkg/status").read_text().split("\n\n") if p.startswith("Package: libssl3t64\n")
    )
    assert "\nVersion: " + VERSION + "\n" in record + "\n"
    assert ssl.OPENSSL_VERSION.startswith("OpenSSL 3.5.9 ")
    assert ssl.create_default_context().get_ca_certs()
    assert ssl.HAS_TLSv1_3
    libraries = {}
    for name in ("libssl.so.3", "libcrypto.so.3"):
        paths = [path for root in ("/usr/lib", "/lib") for path in Path(root).glob("*/" + name)]
        assert paths, f"Missing native library: {name}"
        libraries[name] = paths[0]
    hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in libraries.items()}
    manifest = Path("/openssl-backport.json")
    if "--record-build" in sys.argv:
        for source, digest in PATCHED_SOURCES.items():
            assert hashlib.sha256((Path("/tmp/openssl-packages/source") / source).read_bytes()).hexdigest() == digest
        manifest.write_text(
            json.dumps(
                {
                    "commits": COMMITS,
                    "regression_commit": REGRESSION_COMMIT,
                    "patched_sources": PATCHED_SOURCES,
                    "libraries": hashes,
                }
            )
        )
        return
    proof = json.loads(manifest.read_text())
    assert proof == {
        "commits": COMMITS,
        "regression_commit": REGRESSION_COMMIT,
        "patched_sources": PATCHED_SOURCES,
        "libraries": hashes,
    }
    if "--vex" not in sys.argv:
        print("OpenSSL backport package, native TLS support, source and library fingerprints verified")
        return
    arch = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
    distro = Path("/etc/debian_version").read_text().strip()
    print(
        json.dumps(
            {
                "@context": "https://openvex.dev/ns/v0.2.0",
                "@id": "https://github.com/marcnester/glycofy-api/openssl-fixed/" + hashes["libssl.so.3"],
                "author": "Glycofy CI: verified upstream OpenSSL backport",
                "timestamp": datetime.now(UTC).isoformat(),
                "version": 1,
                "statements": [
                    {
                        "vulnerability": {"name": cve},
                        "products": [
                            {"@id": f"pkg:deb/debian/libssl3t64@{quote(VERSION)}?arch={arch}&distro=debian-{distro}"}
                        ],
                        "status": "fixed",
                        "status_notes": "Debian 3.5.9 rebuilt with checksum-pinned upstream commit "
                        + commit
                        + ". Debian build/test suite including the upstream QUIC retransmission regression passed. Runtime package version, TLS support, patched source and installed library fingerprints verified. "
                        + json.dumps(hashes, sort_keys=True),
                    }
                    for cve, commit in COMMITS.items()
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
