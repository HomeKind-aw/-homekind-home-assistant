FROM ghcr.io/home-assistant/base@sha256:8fde4170393bdf7a121ea4d6e34b417e1d0b90125948459f9f9f70cdf5d77c16
RUN apk add --no-cache python3 tzdata
ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL io.hass.version="${BUILD_VERSION}" io.hass.type="app" io.hass.arch="${BUILD_ARCH}"
COPY maintenance.py windows.py window_sync.py window_runtime.py system_updates.py /opt/homekind/homekind_maintenance/
WORKDIR /opt/homekind
CMD ["/command/with-contenv", "python3", "-u", "-m", "homekind_maintenance.maintenance"]
