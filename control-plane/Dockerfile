ARG DOCKER_HUB_MIRROR=m.daocloud.io/docker.io

FROM ${DOCKER_HUB_MIRROR}/library/rust:1.96-bookworm AS builder
WORKDIR /build
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo build --release --locked

FROM ${DOCKER_HUB_MIRROR}/library/debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --create-home app
WORKDIR /app
COPY --from=builder /build/target/release/agora-sensevoice-control-plane /usr/local/bin/control-plane
COPY --chown=10001:10001 static ./static
ENV BIND_ADDR=0.0.0.0:8080 \
    STATIC_DIR=/app/static \
    RUST_LOG=info
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD ["/usr/local/bin/control-plane", "--healthcheck"]
ENTRYPOINT ["/usr/local/bin/control-plane"]
