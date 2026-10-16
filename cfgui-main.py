"""Minecraft Java client for an existing Cloudflare TCP tunnel."""

import json
from ipaddress import IPv4Address
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from PyQt6.QtCore import QLocale, QObject, QProcess, QSettings, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow, QMenu,
    QPlainTextEdit, QPushButton, QSpinBox, QSystemTrayIcon,
    QVBoxLayout, QWidget,
)


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
            raise OSError("Could not save profiles to your Windows user settings.")


def binary_path():
    return Path(__file__).resolve().parent / "cloudflared" / "cloudflared-windows-386.exe"


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
        self.error.setStyleSheet("color: #ff9d9d;")
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


def app_icon(color="#56d99a"):
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#192332"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(0, 0, 64, 64, 16, 16)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(10, 22, 44, 22, 11, 11)
    painter.setBrush(QColor("#ffffff"))
    painter.drawEllipse(34, 26, 14, 14)
    painter.end()
    return QIcon(pixmap)


STYLE = """
QWidget { background: #131a25; color: #e8edf5; font-family: 'Segoe UI'; font-size: 10pt; }
QPushButton, QComboBox, QLineEdit, QSpinBox { background: #232f41; border: 1px solid #35445c; border-radius: 7px; padding: 8px; }
QPushButton:hover { background: #30425a; }
QPushButton:disabled, QComboBox:disabled { color: #728095; }
QListWidget, QPlainTextEdit { background: #0d131d; border: 1px solid #35445c; border-radius: 6px; padding: 6px; }
QListWidget::item:selected { background: #304b66; }
QPushButton#switch { font-size: 22pt; font-weight: 600; border-radius: 32px; padding: 16px; }
QPushButton#switch:checked { background: #56d99a; color: #102c22; border: 1px solid #56d99a; }
QLabel#title { font-size: 21pt; font-weight: 600; }
QLabel#subtitle { color: #a3b2c6; }
QMenu { background: #232f41; padding: 5px; }
QMenu::item:selected { background: #35445c; }
"""


class MainWindow(QMainWindow):
    def __init__(self, store=None, controller=None):
        super().__init__()
        self.store = store if store is not None else ProfileStore()
        self.profiles = self.store.load()
        self.controller = controller if controller is not None else TunnelController(self)
        self.quitting = False
        self.setWindowTitle("Minecraft Tunnel")
        self.setWindowIcon(app_icon())
        self.setMinimumWidth(410)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(16)
        top = QHBoxLayout()
        self.config = QPushButton("⚙")
        self.config.setAccessibleName("Configure tunnel profiles")
        self.config.setToolTip("Configure tunnel profiles")
        self.config.setFixedWidth(44)
        self.config.clicked.connect(self.configure)
        top.addWidget(self.config)
        top.addStretch()
        top.addWidget(QLabel("CLOUDFLARED  /  JAVA"))
        layout.addLayout(top)
        title = QLabel("Minecraft Tunnel")
        title.setObjectName("title")
        layout.addWidget(title)
        subtitle = QLabel("Your connection to the server")
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)
        self.selector = QComboBox()
        self.selector.setAccessibleName("Server profile")
        self.selector.currentIndexChanged.connect(self.profile_changed)
        layout.addWidget(self.selector)
        self.switch = QPushButton("OFF")
        self.switch.setObjectName("switch")
        self.switch.setCheckable(True)
        self.switch.setMinimumHeight(80)
        self.switch.clicked.connect(self.toggle)
        layout.addWidget(self.switch)
        self.status = QLabel("Off")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status)
        address_row = QHBoxLayout()
        self.address = QLabel()
        self.address.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        address_row.addWidget(self.address)
        self.copy = QPushButton("Copy")
        self.copy.clicked.connect(lambda: QApplication.clipboard().setText(self.join_address()))
        address_row.addWidget(self.copy)
        layout.addLayout(address_row)
        self.listener_hint = QLabel("Configured to listen on all IPv4 interfaces. Use this PC's LAN IP to join from another device.")
        self.listener_hint.setWordWrap(True)
        self.listener_hint.setObjectName("subtitle")
        self.listener_hint.hide()
        layout.addWidget(self.listener_hint)
        hint = QLabel("Join this address in Minecraft Java. Cloudflare may open your browser to sign in.\nOn means the local listener is ready; server access is checked when you join.")
        hint.setWordWrap(True)
        hint.setObjectName("subtitle")
        layout.addWidget(hint)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #ff9d9d;")
        self.error.hide()
        layout.addWidget(self.error)
        self.log_toggle = QPushButton("Show logs")
        self.log_toggle.setCheckable(True)
        self.log_toggle.toggled.connect(self.show_logs)
        layout.addWidget(self.log_toggle)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.document().setMaximumBlockCount(1000)
        self.logs.setMinimumHeight(150)
        self.logs.hide()
        layout.addWidget(self.logs)
        self.setCentralWidget(root)
        self.tray = QSystemTrayIcon(app_icon("#8895a8"), self)
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
        self.resize(430, self.sizeHint().height())

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
        self.listener_hint.setVisible(profile is not None and profile.local_ip == "0.0.0.0")
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
        self.switch.setText({"Starting": "STARTING…", "Stopping": "STOPPING…", "On": "ON"}.get(state, "OFF"))
        self.switch.setEnabled(bool(self.profiles) and state != "Stopping")
        self.status.setText("Local listener ready" if state == "On" else state)
        self.tray_toggle.setText("Disconnect" if state in ("Starting", "On") else "Connect")
        self.tray_toggle.setEnabled(bool(self.profiles) and state != "Stopping")
        self.tray.setIcon(app_icon("#56d99a" if state == "On" else "#8895a8"))
        self.tray.setToolTip(f"Minecraft Tunnel — {state}")

    def show_error(self, message):
        self.error.setText(message)
        self.error.show()
        if not self.isVisible() and self.tray.isVisible():
            self.tray.showMessage("Minecraft Tunnel", message, QSystemTrayIcon.MessageIcon.Warning)

    def show_logs(self, visible):
        self.logs.setVisible(visible)
        self.log_toggle.setText("Hide logs" if visible else "Show logs")
        if not visible:
            QTimer.singleShot(0, self.adjustSize)

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
