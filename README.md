# 🎙️ Imou Talkback — Home Assistant Integration

Phát TTS và nhạc qua loa camera **Imou / Dahua** trực tiếp từ Home Assistant.  
Không cần cloud account, không cần Imou app, không cần add-on trung gian.

👉 **GitHub:** https://github.com/vnp1978/ha-imou-talkback

---

## Tính năng

- 🔊 Phát TTS và nhạc qua loa camera Imou/Dahua trên mạng LAN
- 🎵 Phát nhạc dài không bị vấp, không méo tiếng
- 📱 Tích hợp đầy đủ vào HA — nút Cast, Browse Media, Stop
- ⚡ Kết nối thẳng từ HA vào camera — không qua add-on trung gian
- 🔄 Tự động chọn giao thức tốt nhất: cổng 8086 (AAC 16kHz) hoặc 37777 (PCM 8kHz)
- 🌍 Hỗ trợ mọi TTS engine trong HA: Piper, Google TTS, Wyoming...

---

## Yêu cầu

- Home Assistant 2024.1 trở lên
- Camera Imou hoặc Dahua kết nối **cùng mạng LAN** với HA
- HA đã có **ffmpeg** (thường có sẵn)
- Ít nhất một **TTS engine** trong HA (Piper, Google TTS...)

---

## Cài đặt

### Bước 1 — Copy custom component vào HA

Tải thư mục `custom_components/imou_talkback/` từ repo này vào:
```
/config/custom_components/imou_talkback/
```

Hoặc dùng terminal HA:
```bash
git clone --depth=1 https://github.com/vnp1978/ha-imou-talkback /tmp/imou-install
cp -r /tmp/imou-install/custom_components/imou_talkback /config/custom_components/
```

### Bước 2 — Restart Home Assistant

**Settings → System → Restart**

### Bước 3 — Thêm Integration

**Settings → Integrations → Add Integration → Imou Talkback**

Điền thông tin camera:

| Trường | Giá trị |
|---|---|
| Tên camera | Tên hiển thị (vd: Phòng khách) |
| Host (IP) | IP camera trong mạng LAN |
| Mật khẩu | **Security Code** in ở mặt dưới đáy camera |
| Tài khoản | admin (mặc định) |
| Cổng | 37777 (mặc định, dự phòng) |

Mỗi camera thêm một lần. Sau khi thêm, entity `media_player.imou_<tên>` xuất hiện tự động.

---

## Sử dụng

### Phát TTS trong Automation

```yaml
action:
  - action: tts.speak
    target:
      entity_id: tts.piper
    data:
      media_player_entity_id: media_player.imou_phong_khach
      message: "Có người ở cổng"
```

### Phát nhạc

```yaml
action:
  - action: media_player.play_media
    target:
      entity_id: media_player.imou_phong_khach
    data:
      media_content_id: "http://example.com/music.mp3"
      media_content_type: "music"
```

### Dừng phát

```yaml
action:
  - action: media_player.media_stop
    target:
      entity_id: media_player.imou_phong_khach
```

### Cast từ Dashboard

Bấm nút **Cast** trên entity card → chọn nguồn nhạc (My Media, Radio Browser, Text-to-speech...).

---

## Giao thức

Integration tự động chọn giao thức phù hợp:

| Cổng | Giao thức | Chất lượng | Ghi chú |
|---|---|---|---|
| 8086 | HTTP/DHAV | AAC 16kHz — rõ hơn | Ưu tiên |
| 37777 | Dahua NetSDK | PCM 8kHz | Dự phòng tự động |

Nếu cổng 8086 không kết nối được, integration tự chuyển sang 37777 và nhớ trong 1 giờ.

---

## Camera được hỗ trợ

Tất cả camera **Imou** và **Dahua** có loa talkback:
- Imou Cruiser, Ranger, Bullet, Dome series
- Dahua và các OEM tương thích (Amcrest, Lorex...)

---

## Troubleshooting

| Lỗi | Nguyên nhân | Cách fix |
|---|---|---|
| `cannot_connect` khi thêm | Sai IP hoặc camera không mở cổng 37777 | Kiểm tra IP, thử ping từ HA |
| `invalid_auth` khi thêm | Sai Security Code | Lật đáy camera đọc lại Security Code |
| Thêm được nhưng không có tiếng | Camera không mở cổng 8086 và 37777 | Kiểm tra firewall, thử từ cùng subnet |
| Tiếng bị méo | ffmpeg không có trong HA | Cài add-on FFmpeg trong HA |

---

## License

MIT — free to use, modify and distribute.
