ARG BUILD_FROM=ghcr.io/home-assistant/base:latest
FROM $BUILD_FROM

# Cài system packages
RUN apk add --no-cache \
    python3 \
    py3-pip \
    ffmpeg \
    tzdata

# Cài Python packages
RUN pip3 install --break-system-packages \
    flask \
    gtts \
    requests \
    aiohttp

# Copy toàn bộ rootfs vào container
# CACHEBUST tự động thay đổi mỗi lần build để force copy mới
ARG CACHEBUST=1
COPY rootfs /

RUN chmod a+x /run.sh \
    && chmod +x /opt/imou_visualtalk.py \
    && chmod +x /opt/imou_dhav.py \
    && chmod +x /opt/imou_wsse.py

# Thêm /opt vào PYTHONPATH để imou_visualtalk.py tìm được imou_dhav, imou_wsse
ENV PYTHONPATH="/opt"

CMD [ "/run.sh" ]
