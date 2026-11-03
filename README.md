# Minecraft Tunnel

A compact PyQt6 client for Linux, Windows, and macOS that connects to an existing Minecraft Java server through Cloudflare Tunnel.

## Run from source

Use Python 3.10 or later. From the project directory:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python cfgui-main.py
```

On Windows activation is `.\.venv\Scripts\activate` and Python can be launched with `py`.

The app picks the bundled Cloudflared binary that matches your OS/CPU from the `cloudflared/` directory.

## Connect

1. Set up a server-side Cloudflare Tunnel with a published TCP hostname pointing to your Minecraft Java server, such as `tcp://localhost:25565`. The server connector must already be running. See [Cloudflare TCP setup](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/non-http/cloudflared-authentication/arbitrary-tcp/).
2. Open the gear button at the top left and add a profile with the published hostname (for example `mc.example.com`), a **Local IP** (default `127.0.0.1`), and an unused local port (default `25565`). Save your profiles.
3. Choose a profile and click the power icon to connect. The app runs `cloudflared access tcp --hostname <hostname> --url <local-ip>:<port>`.
4. In Minecraft Java, join the displayed address, or use **Copy**. If Cloudflare Access protects the hostname, cloudflared may open your browser to authenticate when you connect.

Each profile saves its own Local IP. Use `127.0.0.1` for connections on this PC, another loopback address such as `127.0.0.2`, or an IPv4 address assigned to this PC's LAN interface. Use `0.0.0.0` to listen on all IPv4 interfaces: the displayed/copied address is then `127.0.0.1:<port>` for this PC, while other devices use this PC's LAN IP and the same port. Hostnames and IPv6 are not accepted in Local IP. An IP unavailable on the PC produces a cloudflared startup error. Existing profiles automatically use `127.0.0.1` until edited.

On means cloudflared's local listener is ready. Authentication, remote tunnel availability, and Minecraft server availability are checked when Minecraft connects. Logs are always visible below the controls; check them for errors. If a port is occupied (for example by a local Minecraft server), choose another local port. Bedrock/UDP is not supported.

Turn the switch off before editing or changing profiles. Closing the window keeps the app and tunnel running in the system tray. Use the tray menu to reopen, connect/disconnect, or **Quit**. Quit stops the app's cloudflared process. Without a system tray, closing stops the tunnel and exits. The app always launches disconnected and does not automatically reconnect after failures.

Profiles and the last selection are stored per user via Qt settings, outside the executable. No tunnel token is needed for this player client. The app does not create or manage server-side tunnels.

## Build a single EXE

Build on your target platform using the virtual environment above:

```bash
python -m PyInstaller --clean --noconfirm MinecraftTunnel.spec
```

Distribute the generated app in `dist/`. Python, Qt, and all bundled Cloudflared binaries are included; recipients do not need to install them separately. The build extracts its runtime into a temporary directory on launch, so first startup can take a few seconds. Run as a regular user.

## Verification

```bash
python -m unittest discover -s tests -v
```

The tests use isolated temporary settings and local test processes; they do not authenticate to Cloudflare or require a live Minecraft server. A real Minecraft join must be checked with your configured hostname.
