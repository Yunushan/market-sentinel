from __future__ import annotations

from core.tls import install_platform_trust_store


# Some reviewed third-party SDKs own their HTTP session and do not accept an
# SSL context.  Preserve their platform-trust behavior; managed transports use
# ``core.tls.create_verified_client_context`` so explicit CA bundles remain
# scoped and authoritative.
install_platform_trust_store()
