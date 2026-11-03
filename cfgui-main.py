"""Minecraft Java client for an existing Cloudflare TCP tunnel."""

import json
from ipaddress import IPv4Address
import platform
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from PyQt6.QtCore import QLocale, QObject, QProcess, QSettings, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow, QMenu,
    QPlainTextEdit, QPushButton, QSpinBox, QSystemTrayIcon,
    QVBoxLayout, QWidget,
)


PALETTE = {
    "paper": "#B59785",
    "brown": "#55412F",
    "red": "#640606",
    "rooster": "#340C0C",
    "sheep": "#110E0D",
}
MUTED = QColor(PALETTE["paper"]).darker(120).name()
ERROR_TEXT = QColor(PALETTE["paper"]).lighter(130).name()


@dataclass
class Profile:
    name: str
    hostname: str
    port: int = 25565
    local_ip: str = "127.0.0.1"

    def validate(self):
        if not self.name.strip():
            raise ValueError("Enter a profile name.")
        host = self.hostname
        if len(host) > 253 or "." not in host or any(
            not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in host.split(".")
        ):
            raise ValueError("Enter a hostname such as mc.example.com, without https://, a path, or a port.")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("Local port must be between 1 and 65535.")
        try:
            if not isinstance(self.local_ip, str):
                raise ValueError
            IPv4Address(self.local_ip)
        except ValueError:
            raise ValueError("Enter a valid local IPv4 address, such as 127.0.0.1 or 0.0.0.0.") from None


class ProfileStore:
    def __init__(self, settings=None):
        self.settings = settings if settings is not None else QSettings("Pztmc", "MinecraftTunnel")

    def load(self):
        profiles = []
        try:
            rows = json.loads(self.settings.value("profiles", "[]"))
            if not isinstance(rows, list):
                return []
            for row in rows:
                try:
                    profile = Profile(**row)
                    profile.validate()
                    if profile.name.casefold() not in {p.name.casefold() for p in profiles}:
                        profiles.append(profile)
                except (TypeError, ValueError, AttributeError):
                    continue
        except (ValueError, TypeError):
            pass
        return profiles

    def save(self, profiles, selected):
        self.settings.setValue("profiles", json.dumps([asdict(p) for p in profiles]))
        self.settings.setValue("selected", selected)
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            raise OSError("Could not save profiles to your user settings.")


def cloudflared_candidates():
    machine = platform.machine().lower()
    if sys.platform.startswith("win"):
        if machine in {"arm64", "aarch64"}:
            return ["cloudflared-windows-arm64.exe", "cloudflared-windows-386.exe"]
        return ["cloudflared-windows-amd64.exe", "cloudflared-windows-386.exe"]
    if sys.platform == "darwin":
        if machine in {"arm64", "aarch64"}:
            return ["cloudflared-darwin-arm64", "cloudflared-darwin-amd64"]
        return ["cloudflared-darwin-amd64", "cloudflared-darwin-arm64"]
    if machine in {"arm64", "aarch64"}:
        return ["cloudflared-linux-arm64", "cloudflared-linux-amd64"]
    if machine in {"armv7l", "armv6l", "arm"}:
        return ["cloudflared-linux-arm", "cloudflared-linux-arm64"]
    if machine in {"i386", "i686", "x86"}:
        return ["cloudflared-linux-386", "cloudflared-linux-amd64"]
    return ["cloudflared-linux-amd64", "cloudflared-linux-386"]


def binary_path():
    binary_dir = Path(__file__).resolve().parent / "cloudflared"
    for name in cloudflared_candidates():
        path = binary_dir / name
        if path.is_file():
            return path
    return binary_dir / cloudflared_candidates()[0]


class TunnelController(QObject):
    state_changed = pyqtSignal(str)
    log = pyqtSignal(str)
    failed = pyqtSignal(str)
    stopped = pyqtSignal()

    def __init__(self, parent=None, executable=None, prefix_args=()):
        super().__init__(parent)
        self.executable = str(executable or binary_path())
        self.prefix_args = list(prefix_args)
        self.state = "Off"
        self.pending_error = ""
        self.buffer = ""
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        self.start_timer = QTimer(self)
        self.start_timer.setSingleShot(True)
        self.start_timer.setInterval(15000)
        self.start_timer.timeout.connect(lambda: self._fail("Cloudflared did not open its listener within 15 seconds. See logs."))
        self.kill_timer = QTimer(self)
        self.kill_timer.setSingleShot(True)
        self.kill_timer.setInterval(3000)
        self.kill_timer.timeout.connect(self.process.kill)

    def _set_state(self, state):
        self.state = state
        self.state_changed.emit(state)

    def start(self, profile):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            return
        try:
            profile.validate()
        except ValueError as error:
            self._fail(str(error))
            return
        self.pending_error = ""
        self.buffer = ""
        if not Path(self.executable).is_file():
            self._fail("Bundled cloudflared executable is missing. Rebuild or reinstall the app.")
            return
        self._set_state("Starting")
        args = self.prefix_args + ["access", "tcp", "--hostname", profile.hostname,
                                   "--url", f"{profile.local_ip}:{profile.port}"]
        self.log.emit(f"Starting local listener at {profile.local_ip}:{profile.port} for {profile.hostname}")
        self.start_timer.start()
        self.process.start(self.executable, args)

    def stop(self):
        self.start_timer.stop()
        if self.process.state() == QProcess.ProcessState.NotRunning:
            if not self.pending_error:
                self._set_state("Off")
            self.stopped.emit()
            return
        self._set_state("Stopping")
        self.process.terminate()
        self.kill_timer.start()

    def _fail(self, message):
        self.pending_error = message
        self.log.emit(message)
        self.start_timer.stop()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.stop()
        else:
            self._set_state("Error")
            self.failed.emit(message)
            self.stopped.emit()

    def _read(self):
        self.buffer += bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            self._line(line.rstrip())

    def _line(self, line):
        self.log.emit(line)
        if self.state == "Starting" and "Start Websocket listener" in line:
            self.start_timer.stop()
            self._set_state("On")

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._fail(f"Could not start cloudflared: {self.process.errorString()}")

    def _finished(self, code, status):
        self._read()
        if self.buffer:
            self._line(self.buffer)
            self.buffer = ""
        self.start_timer.stop()
        self.kill_timer.stop()
        expected = self.state == "Stopping"
        if not expected and not self.pending_error:
            self.pending_error = f"Cloudflared exited unexpectedly (code {code}). See logs for details; the local port may already be in use."
        if self.pending_error:
            self._set_state("Error")
            self.failed.emit(self.pending_error)
        else:
            self._set_state("Off")
        self.stopped.emit()


class ProfileEditor(QDialog):
    def __init__(self, profiles, selected, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tunnel profiles")
        self.resize(510, 460)
        self.profiles = [Profile(**asdict(p)) for p in profiles]
        self.selected = selected
        self.current = -1
        layout = QVBoxLayout(self)
        self.items = QListWidget()
        layout.addWidget(self.items)
        buttons = QHBoxLayout()
        add = QPushButton("Add profile")
        delete = QPushButton("Delete")
        buttons.addWidget(add)
        buttons.addWidget(delete)
        layout.addLayout(buttons)
        form = QFormLayout()
        self.name = QLineEdit()
        self.host = QLineEdit()
        self.host.setPlaceholderText("mc.example.com")
        self.local_ip = QLineEdit()
        self.local_ip.setPlaceholderText("127.0.0.1")
        self.port = QSpinBox()
        self.port.setLocale(QLocale.c())
        self.port.setRange(1, 65535)
        self.port.setValue(25565)
        form.addRow("Profile name", self.name)
        form.addRow("Tunnel hostname", self.host)
        form.addRow("Local IP", self.local_ip)
        form.addRow("Local port", self.port)
        layout.addLayout(form)
        hint = QLabel("Use an IP on this PC. 127.0.0.1 accepts local connections; 0.0.0.0 listens on all IPv4 interfaces. Minecraft on this PC can then join 127.0.0.1.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {ERROR_TEXT};")
        layout.addWidget(self.error)
        footer = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(footer)
        footer.accepted.connect(self.accept)
        footer.rejected.connect(self.reject)
        add.clicked.connect(self.add_profile)
        delete.clicked.connect(self.delete_profile)
        self.items.currentRowChanged.connect(self.select_row)
        self.refresh(next((i for i, p in enumerate(self.profiles) if p.name == selected), 0))

    def refresh(self, row):
        self.items.blockSignals(True)
        self.items.clear()
        self.items.addItems([p.name for p in self.profiles])
        self.items.setCurrentRow(row if self.profiles else -1)
        self.items.blockSignals(False)
        self.current = -1
        self.select_row(self.items.currentRow())

    def commit(self):
        if self.current < 0:
            return True
        profile = Profile(self.name.text().strip(), self.host.text().strip().lower(),
                          self.port.value(), self.local_ip.text().strip())
        try:
            profile.validate()
            if any(i != self.current and p.name.casefold() == profile.name.casefold()
                   for i, p in enumerate(self.profiles)):
                raise ValueError("Choose a unique profile name.")
        except ValueError as error:
            self.error.setText(str(error))
            return False
        self.profiles[self.current] = profile
        self.items.item(self.current).setText(profile.name)
        self.error.clear()
        return True

    def select_row(self, row):
        if not self.commit():
            self.items.blockSignals(True)
            self.items.setCurrentRow(self.current)
            self.items.blockSignals(False)
            return
        self.current = row
        enabled = row >= 0
        for field in (self.name, self.host, self.local_ip, self.port):
            field.setEnabled(enabled)
        self.name.setText(self.profiles[row].name if enabled else "")
        self.host.setText(self.profiles[row].hostname if enabled else "")
        self.local_ip.setText(self.profiles[row].local_ip if enabled else "127.0.0.1")
        self.port.setValue(self.profiles[row].port if enabled else 25565)

    def add_profile(self):
        if not self.commit():
            return
        n = 1
        while f"Server {n}".casefold() in {p.name.casefold() for p in self.profiles}:
            n += 1
        self.profiles.append(Profile(f"Server {n}", "mc.example.com"))
        self.refresh(len(self.profiles) - 1)
        self.host.setFocus()
        self.host.selectAll()

    def delete_profile(self):
        if self.current < 0:
            return
        row = self.current
        del self.profiles[row]
        self.error.clear()
        self.refresh(min(row, len(self.profiles) - 1))

    def accept(self):
        if self.commit():
            self.selected = self.profiles[self.current].name if self.current >= 0 else ""
            super().accept()


def app_icon(color=PALETTE["red"]):
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(PALETTE["sheep"]))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(0, 0, 64, 64, 16, 16)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(10, 22, 44, 22, 11, 11)
    painter.setBrush(QColor(PALETTE["paper"]))
    painter.drawEllipse(34, 26, 14, 14)
    painter.end()
    return QIcon(pixmap)


def power_icon(color):
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(color), 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawArc(12, 12, 40, 40, 130 * 16, 280 * 16)
    painter.drawLine(32, 7, 32, 30)
    painter.end()
    return QIcon(pixmap)


STYLE = """
QWidget { background: @sheep; color: @paper; font-family: 'Segoe UI'; font-size: 10pt; }
QPushButton, QComboBox, QLineEdit, QSpinBox { background: @rooster; border: 1px solid @brown; border-radius: 7px; padding: 8px; selection-background-color: @paper; selection-color: @sheep; }
QPushButton:hover { background: @brown; }
QPushButton:focus, QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QPlainTextEdit:focus, QListWidget:focus { border: 1px solid @paper; }
QPushButton:disabled, QComboBox:disabled, QLineEdit:disabled, QSpinBox:disabled { color: @muted; background: @sheep; }
QLineEdit, QSpinBox, QPlainTextEdit { placeholder-text-color: @muted; }
QListWidget, QPlainTextEdit { background: @sheep; border: 1px solid @brown; border-radius: 6px; padding: 6px; selection-background-color: @paper; selection-color: @sheep; }
QListWidget::item:selected { background: @paper; color: @sheep; }
QComboBox QAbstractItemView { background: @rooster; color: @paper; border: 1px solid @brown; selection-background-color: @paper; selection-color: @sheep; }
QPushButton#switch { border-radius: 32px; padding: 6px; }
QPushButton#switch:checked { background: @red; border: 1px solid @paper; }
QPushButton#switch:checked:hover { background: @rooster; }
QMenu { background: @rooster; border: 1px solid @brown; padding: 5px; }
QMenu::item:selected { background: @brown; color: @paper; }
QMenu::item:disabled { color: @muted; }
QMenu::separator { height: 1px; background: @brown; margin: 4px; }
QToolTip { background: @rooster; color: @paper; border: 1px solid @paper; padding: 4px; }
QScrollBar:vertical { background: @sheep; width: 10px; }
QScrollBar:horizontal { background: @sheep; height: 10px; }
QScrollBar::handle { background: @brown; border-radius: 4px; min-width: 10px; min-height: 10px; }
QScrollBar::handle:hover { background: @paper; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: @sheep; }
"""
for _name, _color in {**PALETTE, "muted": MUTED}.items():
    STYLE = STYLE.replace(f"@{_name}", _color)


class MainWindow(QMainWindow):
    def __init__(self, store=None, controller=None):
        super().__init__()
        self.store = store if store is not None else ProfileStore()
        self.profiles = self.store.load()
        self.controller = controller if controller is not None else TunnelController(self)
        self.quitting = False
        self.setWindowTitle("Minecraft Tunnel")
        self.setWindowIcon(app_icon())
        self.setMinimumWidth(340)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.config = QPushButton("⚙")
        self.config.setAccessibleName("Configure tunnel profiles")
        self.config.setToolTip("Configure tunnel profiles")
        self.config.setFixedSize(36, 36)
        self.config.clicked.connect(self.configure)
        top.addWidget(self.config)
        self.selector = QComboBox()
        self.selector.setAccessibleName("Server profile")
        self.selector.currentIndexChanged.connect(self.profile_changed)
        top.addWidget(self.selector, 1)
        layout.addLayout(top)
        self.switch = QPushButton()
        self.switch.setObjectName("switch")
        self.switch.setCheckable(True)
        self.switch.setFixedSize(64, 64)
        self.switch.setIconSize(QSize(32, 32))
        self.switch.clicked.connect(self.toggle)
        layout.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignHCenter)
        self.status = QLabel("Off")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status)
        address_row = QHBoxLayout()
        self.address = QLabel()
        self.address.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        address_row.addWidget(self.address, 1)
        self.copy = QPushButton("Copy")
        self.copy.clicked.connect(lambda: QApplication.clipboard().setText(self.join_address()))
        address_row.addWidget(self.copy)
        layout.addLayout(address_row)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {ERROR_TEXT};")
        self.error.hide()
        layout.addWidget(self.error)
        self.logs = QPlainTextEdit()
        self.logs.setAccessibleName("Cloudflared logs")
        self.logs.setPlaceholderText("Cloudflared logs will appear here.")
        self.logs.setReadOnly(True)
        self.logs.document().setMaximumBlockCount(1000)
        self.logs.setMinimumHeight(100)
        layout.addWidget(self.logs, 1)
        self.setCentralWidget(root)
        self.tray = QSystemTrayIcon(app_icon(PALETTE["brown"]), self)
        menu = QMenu(self)
        show_action = menu.addAction("Show")
        show_action.triggered.connect(self.show_window)
        self.tray_toggle = QAction("Connect", self)
        self.tray_toggle.triggered.connect(self.toggle)
        menu.addAction(self.tray_toggle)
        menu.addSeparator()
        menu.addAction("Quit").triggered.connect(self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.controller.state_changed.connect(self.update_state)
        self.controller.log.connect(self.logs.appendPlainText)
        self.controller.failed.connect(self.show_error)
        self.controller.stopped.connect(self.on_stopped)
        self.refresh_profiles(self.store.settings.value("selected", ""))
        self.resize(380, 360)

    def refresh_profiles(self, selected):
        self.selector.blockSignals(True)
        self.selector.clear()
        self.selector.addItems([p.name for p in self.profiles])
        self.selector.setCurrentIndex(next((i for i, p in enumerate(self.profiles) if p.name == selected), 0) if self.profiles else -1)
        self.selector.setPlaceholderText("Add a server using ⚙")
        self.selector.blockSignals(False)
        self.profile_changed()

    def current_profile(self):
        row = self.selector.currentIndex()
        return self.profiles[row] if 0 <= row < len(self.profiles) else None

    def join_address(self):
        profile = self.current_profile()
        if not profile:
            return ""
        ip = "127.0.0.1" if profile.local_ip == "0.0.0.0" else profile.local_ip
        return f"{ip}:{profile.port}"

    def profile_changed(self):
        self.address.setText(f"Join: {self.join_address()}" if self.profiles else "Add a tunnel profile to get started")
        self.copy.setEnabled(bool(self.profiles))
        profile = self.current_profile()
        self.address.setToolTip(
            "Configured to listen on all IPv4 interfaces. Join 127.0.0.1 on this PC; use this PC's LAN IP from another device."
            if profile is not None and profile.local_ip == "0.0.0.0" else ""
        )
        self.store.settings.setValue("selected", self.selector.currentText())
        self.update_state(self.controller.state)

    def configure(self):
        if self.controller.state not in ("Off", "Error"):
            return
        editor = ProfileEditor(self.profiles, self.selector.currentText(), self)
        if editor.exec() == QDialog.DialogCode.Accepted:
            try:
                self.store.save(editor.profiles, editor.selected)
            except OSError as error:
                self.show_error(str(error))
                return
            self.profiles = editor.profiles
            self.refresh_profiles(editor.selected)

    def toggle(self):
        if self.controller.state in ("Starting", "On"):
            self.controller.stop()
        elif self.controller.state in ("Off", "Error"):
            profile = self.current_profile()
            if profile:
                self.error.hide()
                self.controller.start(profile)

    def update_state(self, state):
        busy = state in ("Starting", "On", "Stopping")
        self.config.setEnabled(not busy)
        self.selector.setEnabled(not busy and bool(self.profiles))
        self.switch.setChecked(state in ("Starting", "On"))
        color = PALETTE["paper"] if state in ("Starting", "On", "Stopping") else MUTED
        self.switch.setIcon(power_icon(color))
        action = {"Starting": "Cancel connection", "On": "Disconnect", "Stopping": "Disconnecting…"}.get(state, "Connect")
        self.switch.setToolTip(action)
        self.switch.setAccessibleName(action)
        self.switch.setEnabled(bool(self.profiles) and state != "Stopping")
        self.status.setText("Local listener ready" if state == "On" else state)
        self.tray_toggle.setText("Disconnect" if state in ("Starting", "On") else "Connect")
        self.tray_toggle.setEnabled(bool(self.profiles) and state != "Stopping")
        self.tray.setIcon(app_icon(PALETTE["red"] if state == "On" else PALETTE["brown"]))
        self.tray.setToolTip(f"Minecraft Tunnel — {state}")

    def show_error(self, message):
        self.error.setText(message)
        self.error.show()
        if not self.isVisible() and self.tray.isVisible():
            self.tray.showMessage("Minecraft Tunnel", message, QSystemTrayIcon.MessageIcon.Warning)

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

    def closeEvent(self, event):
        event.ignore()
        if self.tray.isVisible() and not self.quitting:
            self.hide()
            if not self.store.settings.value("tray_notice", False, type=bool):
                self.tray.showMessage("Minecraft Tunnel is still running", "Use the tray menu to reopen the window or Quit.")
                self.store.settings.setValue("tray_notice", True)
        else:
            self.quit_app()

    def quit_app(self):
        if self.quitting:
            return
        self.quitting = True
        self.hide()
        self.tray.hide()
        self.controller.stop()

    def on_stopped(self):
        if self.quitting:
            self.store.settings.sync()
            QApplication.instance().quit()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Minecraft Tunnel")
    app.setOrganizationName("Pztmc")
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    # Also clean up on a normal application exit initiated outside the tray.
    def cleanup():
        process = window.controller.process
        if process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
            process.waitForFinished(3000)
    app.aboutToQuit.connect(cleanup)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
