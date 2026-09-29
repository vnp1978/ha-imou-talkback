## 🎙️ Imou Talkback — Home Assistant Add-on

Phát TTS qua loa camera **Imou LAN** trực tiếp từ Home Assistant.  
Không cần cloud account, không cần Imou app.

👉 **GitHub:** https://github.com/vnp1978/ha-imou-talkback

---

## Tính năng

- 🔊 Phát văn bản (TTS) qua loa camera Imou qua mạng LAN
- 🌐 Web UI để cấu hình camera và test TTS
- 🔑 API Key tự động sinh — không cần cấu hình thủ công
- 🤖 Tích hợp HA qua `notify` platform — dùng được trong automation
- 🌍 Hỗ trợ đa ngôn ngữ: Tiếng Việt, English, Japanese, Korean...

---

## Yêu cầu

- Home Assistant OS hoặc Home Assistant Container
- Camera Imou kết nối **cùng mạng LAN** với máy chạy HA
- Password của camera: là **Security Code** in ở mặt dưới đáy camera

---

## Cài đặt

### Bước 1 — Thêm repository

Trong Home Assistant:  
**Settings → Add-ons → Add-on Store → ⋮ → Repositories**

Thêm URL:
```
https://github.com/vnp1978/ha-imou-talkback
```

### Bước 2 — Cài Add-on

Tìm **Imou Talkback** trong danh sách → **Install** → **Start**.

### Bước 3 — Cấu hình camera

Vào **`http://homeassistant.local:8765`** (hoặc IP của HA + port 8765).

Trang web sẽ hiện:
- **API Key** đã được tạo sẵn
- **Đoạn YAML** để copy vào `configuration.yaml`
- Form để **thêm camera**
- Nút **Test TTS** để thử ngay

### Bước 4 — Thêm vào configuration.yaml

Copy đoạn YAML từ trang web, dán vào `configuration.yaml`:

```yaml
notify:
  - platform: imou_talkback
    name: imou_talkback
    host: homeassistant.local
    port: 8765
    api_key: "your-api-key-here"
```

Sau đó: **Developer Tools → YAML → Reload** (hoặc restart HA).

---

## Sử dụng trong Automation

```yaml
automation:
  - alias: "Thông báo có khách"
    trigger:
      - platform: state
        entity_id: binary_sensor.motion_gate
        to: "on"
    action:
      - service: notify.imou_talkback
        data:
          message: "Có người ở cổng"
          target: "cam_cong"   # tên camera, bỏ trống = dùng camera đầu tiên
```

---

## API trực tiếp

```bash
curl -X POST http://homeassistant.local:8765/speak \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"text": "Xin chào!", "camera": "cam_cong"}'
```

---

## Troubleshooting

| Lỗi | Nguyên nhân | Cách fix |
|---|---|---|
| Không vào được `:8765` | Add-on chưa start | Kiểm tra tab **Log** trong Add-on |
| `Handshake thất bại` | Sai password hoặc IP | Kiểm tra lại trong Web UI |
| Không có tiếng | gTTS cần internet | Kiểm tra kết nối mạng của HA |
| `Unauthorized` | Sai API key | Copy lại key từ Web UI |

---

## Camera được hỗ trợ

Tất cả camera **Imou** hỗ trợ talkback qua LAN (ONVIF port 8086):
- Imou Cruiser, Ranger, Bullet, Dome series
- Và các camera Dahua OEM tương thích

---

## License

MIT
