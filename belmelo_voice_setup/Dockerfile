FROM python:3.13-alpine
COPY custom_components /opt/belmelo/custom_components
COPY installer.py /opt/belmelo/installer.py
COPY wake_words /opt/belmelo/wake_words
CMD ["python3", "/opt/belmelo/installer.py"]
