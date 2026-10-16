# Minecraft Tunnel

A compact PyQt6 Windows client for accessing an existing Minecraft Java server through Cloudflare Tunnel.

## Run from source

Use 64-bit Python 3.10 or later on Windows. From the project directory:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe cfgui-main.py
```

The app expects the supplied `cloudflared/cloudflared-windows-386.exe`. This 32-bit executable works on 64-bit Windows; it is separate from the 64-bit Python/Qt runtime.

## Connect

1. Set up a server-side Cloudflare Tunnel with a published TCP hostname pointing to your Minecraft Java server, such as `tcp://localhost:25565`. The server connector must already be running. See [Cloudflare TCP setup](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/non-http/cloudflared-authentication/arbitrary-tcp/).
2. Open the gear button at the top left and add a profile with the published hostname (for example `mc.example.com`) and an unused local port (default `25565`). Save your profiles.
3. Choose a profile and turn the switch on. The app runs `cloudflared access tcp --hostname <hostname> --url 127.0.0.1:<port>`.
4. In Minecraft Java, join the displayed `127.0.0.1:<port>` address. If Cloudflare Access protects the hostname, cloudflared may open your browser to authenticate when you connect.

On means cloudflared's local listener is ready. Authentication, remote tunnel availability, and Minecraft server availability are checked when Minecraft connects. Use **Show logs** for errors. If a port is occupied (for example by a local Minecraft server), choose another local port. Bedrock/UDP is not supported.

Turn the switch off before editing or changing profiles. Closing the window keeps the app and tunnel running in the system tray. Use the tray menu to reopen, connect/disconnect, or **Quit**. Quit stops the app's cloudflared process. Without a system tray, closing stops the tunnel and exits. The app always launches disconnected and does not automatically reconnect after failures.

Profiles and the last selection are stored per Windows user in `HKEY_CURRENT_USER\Software\Pztmc\MinecraftTunnel`, outside the executable. No tunnel token is needed for this player client. The app does not create or manage server-side tunnels.

## Build a single EXE

Build on 64-bit Windows using the virtual environment above:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm MinecraftTunnel.spec
```

Distribute `dist/MinecraftTunnel.exe`. Python, Qt, and cloudflared are included; recipients do not need to install them separately. The EXE extracts its runtime into a temporary directory on launch, so first startup can take a few seconds. Run as a regular user. The spec uses the binary checked into the project and does not download updates.

## Verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests use isolated temporary settings and local test processes; they do not authenticate to Cloudflare or require a live Minecraft server. A real Minecraft join must be checked with your configured hostname.
