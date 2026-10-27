# PhoneDrop

Transfer photos, screenshots and other files from your phone to your PC over Wi-Fi.
Run it on your computer, open the page on your phone, pick files or take a photo
with the camera — they are saved into a folder on your disk.

No cloud, no accounts, no internet required. Pure Python standard library.

## Features

- Receive files from phone via browser (drag & drop, file picker, camera capture).
- QR code and clickable links for quick connection.
- Files stored in date folders (`2026-10-27/`).
- Automatic rename on name collisions.
- List of uploaded files with sizes.
- Pure Python 3.8+, no `pip install`.
- QR code generated on the server (SVG), no external services.

## Quick start

```bash
python server.py
```

Open the URL printed by the program in your browser (or scan the QR code).
Phone and PC must be on the same Wi-Fi network.

Options:

```bash
python server.py --port 8800 --dir "D:\PhoneBackup"
```

- `--port` — port (default 8800).
- `--dir` — destination folder (default `~/Pictures/PhoneDrop`).

On Windows you can use `run.bat`.

## Requirements

- Python 3.8 or newer.
- Phone and PC on the same local network.

## Privacy

All data stays on your PC. No files or metadata are sent anywhere.
The QR code is generated locally.

## License

MIT. See [LICENSE](LICENSE).

---

Русская версия: [README.ru.md](README.ru.md)
