# Adaptive LAN File Transfer

A small Computer Networks project that shares files through a browser on your local network. The browser measures its link to the server, chooses an initial upload chunk size, then adjusts chunk size from observed upload throughput. Files remain on the computer running the server.

## Requirements

- Python 3.10 or later
- No third-party packages

## Run on Windows

Open a terminal in this project folder and run:

```powershell
python server.py
```

Then open [http://localhost:8000](http://localhost:8000) on the server computer. If the `python` command is unavailable, use the Python executable selected in VS Code:

```powershell
& "C:\Users\Samar\AppData\Local\Python\pythoncore-3.14-64\python.exe" server.py
```

To use a different port, run `python server.py --port 8080` and open `http://localhost:8080`.

## Connect another device

1. Keep the server running and connect both devices to the same Wi-Fi or wired LAN.
2. On the server computer, run `ipconfig` and find its IPv4 address under the active Wi-Fi or Ethernet adapter (for example, `192.168.1.24`).
3. On the other device, open `http://192.168.1.24:8000`, replacing the example IP with the server computer's address.
4. If Windows Firewall asks, allow Python on **Private networks**. Do not expose this server to public networks.

## Transfer behavior

- Select or drag files into the send area and choose **Start transfer**.
- Chunk sizes range from 256 KiB to 8 MiB and are tuned toward approximately 0.8 seconds per request.
- Completed files are stored in the `transfers` folder. Repeated filenames are saved with a number suffix rather than overwritten.
- The available-files list is shared by every device connected to this server. Anyone with LAN access to the page can download those files.

This is a classroom/demo server, not an internet-facing service: it has no accounts, encryption, or access control. Use it only on a trusted private network.
