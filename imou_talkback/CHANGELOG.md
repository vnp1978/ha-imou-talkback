# Changelog

## 1.2.0

- Thêm `media_player` platform — hỗ trợ Cast, TTS, Browse Media
- Auto-detect camera mới thêm từ Web UI (poll mỗi 30 giây)
- Camera bị xóa chuyển sang `unavailable` thay vì mất hẳn
- Fix Dockerfile — rebuild tự động nhận file mới
- Fix câu test TTS ngắn gọn: "Hi, I am Imou Talkback"

## 1.0.0 — Initial Release

- Phát TTS qua loa camera Imou LAN (không cần cloud)
- Web UI tại `:8765` để cấu hình camera và test
- API Key tự động sinh khi khởi động lần đầu
- Hỗ trợ `notify` platform của Home Assistant
- Hỗ trợ đa ngôn ngữ TTS: vi, en, ja, ko, zh, fr, de, es
