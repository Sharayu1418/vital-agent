"""Print VAPID keys in the form the browser and pywebpush actually want.

`vapid --gen` writes PEM files. The browser's applicationServerKey needs
the uncompressed public point as base64url, and pywebpush wants the raw
private scalar the same way — and py_vapid 1.9 exposes neither, it hands
back cryptography key objects.

    uv run python scripts/vapid_keys.py                 # generate a new pair
    uv run python scripts/vapid_keys.py private_key.pem # convert an existing one

Prints the public key to stdout and the private key ONLY with --private, so
the ordinary invocation is safe to run with someone looking over your
shoulder. Pipe the private one straight into Secret Manager:

    uv run python scripts/vapid_keys.py --private --quiet \\
      | gcloud secrets create VAPID_PRIVATE_KEY --data-file=- --project ...

Rotating the key invalidates every existing push subscription — browsers
bind a subscription to the key that created it — so everyone has to turn
the brief back on. Rotate only if the private key is exposed.
"""
import argparse
import base64
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (Encoding,
                                                          PublicFormat)


def b64(raw: bytes) -> str:
    """base64url, unpadded — what the Web Push spec uses throughout."""
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def keys(pem_path: str | None):
    if pem_path:
        with open(pem_path, "rb") as handle:
            key = serialization.load_pem_private_key(handle.read(), password=None)
    else:
        key = ec.generate_private_key(ec.SECP256R1())

    # 0x04 || X || Y, 65 bytes. The compressed form is NOT accepted by
    # PushManager.subscribe.
    public = key.public_key().public_bytes(Encoding.X962,
                                           PublicFormat.UncompressedPoint)
    private = key.private_numbers().private_value.to_bytes(32, "big")
    return b64(public), b64(private)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pem", nargs="?", help="existing private_key.pem")
    parser.add_argument("--private", action="store_true",
                        help="print the PRIVATE key instead of the public one")
    parser.add_argument("--quiet", action="store_true",
                        help="value only, no label and no trailing newline")
    args = parser.parse_args()

    public, private = keys(args.pem)
    value = private if args.private else public

    if args.quiet:
        # No newline: `gcloud secrets create --data-file=-` stores exactly
        # what it reads, and a trailing newline inside a key is the kind of
        # failure that reads as "the key is wrong".
        sys.stdout.write(value)
        return 0

    label = "PRIVATE" if args.private else "PUBLIC"
    print(f"{label}: {value}")
    if not args.private:
        print("\nVAPID_PUBLIC_KEY — a plain env var, it ships to the browser.")
        print("For the private key:  --private --quiet | gcloud secrets create ...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
