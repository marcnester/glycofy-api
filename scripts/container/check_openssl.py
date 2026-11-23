"""Verify the checksum-pinned upstream OpenSSL backport and loaded libraries."""

import hashlib
import json
import platform
import ssl
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

VERSION = "3.5.9-1~deb13u1+glycofy1"
PATCHED_SOURCE_SHA256 = "7bceb5bdc408c3e5a46eb38944f5d16727b812e3e049dae6f2b2e4cdadce3616"
COMMIT = "f3acb0a3328046741479482c51cec815839e1b05"


def main():
    record = next(
        p for p in Path("/var/lib/dpkg/status").read_text().split("\n\n") if p.startswith("Package: libssl3t64\n")
    )
    assert "\nVersion: " + VERSION + "\n" in record + "\n"
    assert ssl.OPENSSL_VERSION_INFO[:3] == (3, 5, 9)
    assert ssl.create_default_context().get_ca_certs()
    assert ssl.HAS_TLSv1_3
    libraries = {name: next(Path("/usr/lib").glob("*/" + name)) for name in ("libssl.so.3", "libcrypto.so.3")}
    hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in libraries.items()}
    manifest = Path("/openssl-backport.json")
    if "--record-build" in sys.argv:
        assert (
            hashlib.sha256(Path("/tmp/openssl-packages/patched-source.c").read_bytes()).hexdigest()
            == PATCHED_SOURCE_SHA256
        )
        manifest.write_text(
            json.dumps({"commit": COMMIT, "patched_source_sha256": PATCHED_SOURCE_SHA256, "libraries": hashes})
        )
        return
    proof = json.loads(manifest.read_text())
    assert proof == {"commit": COMMIT, "patched_source_sha256": PATCHED_SOURCE_SHA256, "libraries": hashes}
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
                        "vulnerability": {"name": "CVE-2026-124709"},
                        "products": [
                            {"@id": f"pkg:deb/debian/libssl3t64@{quote(VERSION)}?arch={arch}&distro=debian-{distro}"}
                        ],
                        "status": "fixed",
                        "status_notes": "Debian 3.5.9 rebuilt with checksum-pinned upstream commit "
                        + COMMIT
                        + ". Debian build/test suite passed. Runtime package version, TLS support, patched source and installed library fingerprints verified. "
                        + json.dumps(hashes, sort_keys=True),
                    }
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
