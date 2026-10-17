FROM ghcr.io/home-assistant/base@sha256:8fde4170393bdf7a121ea4d6e34b417e1d0b90125948459f9f9f70cdf5d77c16
RUN apk add --no-cache python3 py3-pip tzdata
COPY requirements.txt /requirements.txt
RUN python3 -m venv /opt/bridge && /opt/bridge/bin/pip install --no-cache-dir -r /requirements.txt
ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL io.hass.version="${BUILD_VERSION}" io.hass.type="app" io.hass.arch="${BUILD_ARCH}"
COPY bridge.py camera_relay.py media_control.py radio_directory.py setup_server.py managed_settings.py health_import.py index.html setup.js health.js setup.css logo.png nunito.ttf inter.ttf OFL-Inter.txt OFL-Nunito.txt /
CMD ["/command/with-contenv", "/opt/bridge/bin/python", "-u", "/setup_server.py"]
