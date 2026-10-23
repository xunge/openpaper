"""Password hashing for local accounts.

The hosted product signs in with Google or a emailed one-time code, both of
which need a public callback URL or an outbound mail provider — neither exists
on an air-gapped internal deployment. Local accounts keep the credential in the
same Postgres the rest of the app uses.

`hashlib.scrypt` instead of bcrypt/argon2 on purpose: it is memory-hard, ships
with CPython, and needs no new dependency (so no lockfile change and no image
rebuild). The stored format is self-describing so parameters can be raised later
without a migration:

    scrypt$<n>$<r>$<p>$<salt-b64>$<hash-b64>
"""

import base64
import hashlib
import hmac
import os

# 2**14 blocks * 8 * 128 bytes ≈ 16 MiB per hash: enough to make GPU grinding
# expensive while staying well under OpenSSL's default 32 MiB scrypt cap.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_KEY_BYTES = 32


def hash_password(password: str) -> str:
    """Hash a password into the storable `scrypt$...` form."""
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_KEY_BYTES,
    )
    return "$".join(
        [
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored hash, in constant time."""
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        # Malformed or truncated hash: treat as "does not match" rather than
        # raising into the request handler.
        return False

    return hmac.compare_digest(digest, expected)


def validate_new_password(password: str) -> str | None:
    """Return an error message when a proposed password is unusable."""
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if len(password) > 1024:
        return "Password is too long."
    return None
