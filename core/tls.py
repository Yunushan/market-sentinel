from __future__ import annotations

import os
import ssl
from typing import Any


# Keep the interpreter's context type before an optional dependency can replace
# ``ssl.SSLContext`` process-wide.  Explicit CA bundles must be evaluated by the
# context they were loaded into instead of being re-evaluated solely against an
# operating-system trust store.
_SSL_CONTEXT_MRO = ssl.SSLContext.__mro__
_ORIGINAL_SSL_CONTEXT = next(
    candidate
    for candidate in _SSL_CONTEXT_MRO
    if candidate.__module__ == "ssl" and candidate.__name__ == "SSLContext"
)
_NATIVE_SSL_CONTEXT = next(
    candidate
    for candidate in _SSL_CONTEXT_MRO
    if candidate.__module__ == "_ssl" and candidate.__name__ == "_SSLContext"
)


class _StdlibSSLContext(_ORIGINAL_SSL_CONTEXT):
    """Stdlib context whose mutable properties survive truststore injection.

    urllib3 reapplies ``verify_mode`` to caller-provided contexts.  Binding the
    native descriptors here prevents those later writes from entering the
    Python-level setters whose ``super`` call consults the replaced module
    global on CPython 3.14.
    """

    options = _NATIVE_SSL_CONTEXT.options
    verify_flags = _NATIVE_SSL_CONTEXT.verify_flags
    verify_mode = _NATIVE_SSL_CONTEXT.verify_mode
    if hasattr(_NATIVE_SSL_CONTEXT, "minimum_version"):
        minimum_version = _NATIVE_SSL_CONTEXT.minimum_version
        maximum_version = _NATIVE_SSL_CONTEXT.maximum_version


_STDLIB_SSL_CONTEXT = _StdlibSSLContext


def _set_context_property(context: Any, name: str, value: Any) -> None:
    """Set a context property without consulting a replaced module global.

    CPython's Python-level ``SSLContext`` setters use ``ssl.SSLContext`` in a
    two-argument ``super`` call.  After truststore injects its wrapper there,
    invoking a setter on an already-captured stdlib context recurses on Python
    3.14.  The immutable native descriptor is the same verified operation the
    stdlib setter delegates to and is safe for that original context type.
    """
    if isinstance(context, _StdlibSSLContext):
        getattr(_NATIVE_SSL_CONTEXT, name).__set__(context, value)
    else:
        setattr(context, name, value)


def _context_property(context: Any, name: str) -> Any:
    if isinstance(context, _StdlibSSLContext):
        return getattr(_NATIVE_SSL_CONTEXT, name).__get__(context, type(context))
    return getattr(context, name)


def install_platform_trust_store() -> bool:
    """Install the host trust-store adapter for libraries we do not own.

    The application has a small number of reviewed SDK transports whose
    sessions cannot be supplied an SSL context.  Keep their historical native
    trust-store behavior, while retaining ``_STDLIB_SSL_CONTEXT`` above for
    callers that deliberately provide a private CA bundle.
    """
    try:
        import truststore
    except ImportError:
        return False
    try:
        truststore.inject_into_ssl()
    except Exception:
        return False
    return True


def create_verified_client_context(
    *,
    cafile: str | bytes | os.PathLike[str] | os.PathLike[bytes] | None = None,
    capath: str | bytes | os.PathLike[str] | os.PathLike[bytes] | None = None,
) -> Any:
    """Return a hostname-checking client context with an explicit trust policy.

    With no custom CA location, ``truststore`` delegates verification to the
    platform trust service.  When a CA file or directory is supplied, the
    standard-library context is intentional: on Windows, truststore's native
    chain-policy pass can reject roots supplied through ``load_verify_locations``
    after OpenSSL has accepted them.  The standard context still requires a
    valid chain and matching hostname; it simply makes the caller's explicit
    trust anchor authoritative and behaves consistently on every platform.
    """
    explicit_trust = cafile is not None or capath is not None
    using_stdlib = explicit_trust
    if explicit_trust:
        context = _STDLIB_SSL_CONTEXT(ssl.PROTOCOL_TLS_CLIENT)
    else:
        try:
            import truststore
        except ImportError:
            using_stdlib = True
            context = _STDLIB_SSL_CONTEXT(ssl.PROTOCOL_TLS_CLIENT)
        else:
            context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    # Keep these assignments explicit so future dependency defaults cannot
    # silently turn a verified transport into an unauthenticated one.
    _set_context_property(context, "verify_mode", ssl.CERT_REQUIRED)
    _set_context_property(context, "check_hostname", True)
    for flag_name in ("VERIFY_X509_PARTIAL_CHAIN", "VERIFY_X509_STRICT"):
        flag = getattr(ssl, flag_name, 0)
        if flag:
            _set_context_property(
                context,
                "verify_flags",
                _context_property(context, "verify_flags") | flag,
            )

    if explicit_trust:
        context.load_verify_locations(cafile=cafile, capath=capath)
    elif using_stdlib:
        context.load_default_certs(ssl.Purpose.SERVER_AUTH)
    return context
