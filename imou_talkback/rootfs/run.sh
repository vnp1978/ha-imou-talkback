#!/usr/bin/with-contenv bashio

# Tạo thư mục data
mkdir -p /data

# Sinh API key nếu chưa có
if [ ! -f /data/api_key.txt ]; then
    API_KEY=$(cat /proc/sys/kernel/random/uuid)
    echo "$API_KEY" > /data/api_key.txt
    bashio::log.info "============================================"
    bashio::log.info " Imou Talkback đã khởi động!"
    bashio::log.info " Vào http://homeassistant.local:8765 để cấu hình"
    bashio::log.info "============================================"
else
    bashio::log.info "Imou Talkback đang chạy tại http://homeassistant.local:8765"
fi

# Đọc config từ HA options
TTS_LANG=$(bashio::config 'tts_language')
export TTS_LANG

# Start Flask server
exec python3 /app/server.py
