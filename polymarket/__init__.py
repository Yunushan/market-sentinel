from __future__ import annotations


def _install_platform_trust_store() -> None:
    try:
        import truststore
    except Exception:
        return
    try:
        truststore.inject_into_ssl()
    except Exception:
        pass


_install_platform_trust_store()
