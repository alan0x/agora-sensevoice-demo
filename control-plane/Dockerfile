FROM rust:1.96-bookworm AS builder
WORKDIR /build
COPY Cargo.toml Cargo.lock* ./
COPY src ./src
RUN cargo build --release --locked

FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home app
WORKDIR /app
COPY --from=builder /build/target/release/agora-sensevoice-control-plane /usr/local/bin/control-plane
COPY static ./static
ENV BIND_ADDR=0.0.0.0:8080 \
    STATIC_DIR=/app/static \
    RUST_LOG=info
USER app
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/control-plane"]
