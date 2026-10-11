FROM debian@sha256:12c396bd585df7ec21d5679bb6a83d4878bc4415ce926c9e5ea6426d23c60bdc
RUN rm /etc/apt/sources.list.d/debian.sources && printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20250301T000000Z bookworm main\n' > /etc/apt/sources.list && apt-get update && apt-get install -y --no-install-recommends gcc make libc6-dev libtalloc-dev pkg-config binutils ca-certificates git && rm -rf /var/lib/apt/lists/*
ENV SOURCE_DATE_EPOCH=1686519489 LC_ALL=C TZ=UTC
