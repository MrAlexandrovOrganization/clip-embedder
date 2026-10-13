"""TLS by default; plaintext is allowed only for the private fallback service."""

import grpc


def worker_channel(endpoint, transport="tls", options=()):
    if transport == "tls":
        return grpc.secure_channel(endpoint, grpc.ssl_channel_credentials(), options=options)
    if transport == "internal" and endpoint == "clip-embedder:50057":
        return grpc.insecure_channel(endpoint, options=options)
    raise ValueError("invalid worker transport or private endpoint")
