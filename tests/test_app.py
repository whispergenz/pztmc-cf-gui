import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QProcess, QSettings
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication, QDialog

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cfgui", ROOT / "cfgui-main.py")
gui = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gui
spec.loader.exec_module(gui)
APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


def until(predicate, timeout=6):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        APP.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = gui.ProfileStore(QSettings(str(Path(self.temp.name) / "settings.ini"), QSettings.Format.IniFormat))
        self.children = []
        self.windows = []

    def tearDown(self):
        for controller in self.children:
            controller.process.kill()
            controller.process.waitForFinished(3000)
        for window in self.windows:
            window.tray.hide()
            window.hide()
            window.deleteLater()
        APP.processEvents()
        self.temp.cleanup()

    def controller(self, code):
        child = Path(self.temp.name) / f"child{len(self.children)}.py"
        child.write_text(code, encoding="utf-8")
        controller = gui.TunnelController(executable=sys.executable, prefix_args=[str(child)])
        self.children.append(controller)
        return controller

    def test_profile_validation(self):
        gui.Profile("Server", "mc.example.com").validate()
        for host in ("https://mc.example.com", "mc.example.com:443", "mc.example.com/path", "bad host", "-bad.example.com", "example..com"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                gui.Profile("Server", host).validate()
        for port in (0, 65536, "25565", True):
            with self.subTest(port=port), self.assertRaises(ValueError):
                gui.Profile("Server", "mc.example.com", port).validate()

    def test_local_ip_validation(self):
        for ip in ("127.0.0.1", "127.0.0.2", "192.168.1.10", "0.0.0.0"):
            with self.subTest(ip=ip):
                gui.Profile("Test", "mc.example.com", local_ip=ip).validate()
        for ip in ("", "localhost", "::1", "256.1.1.1", "127.1", "127.0.0.1:25565",
                   "http://127.0.0.1", "127.0.0.1/8", "127.0.0.1 ", None, 1234):
            with self.subTest(ip=ip), self.assertRaises(ValueError):
                gui.Profile("Test", "mc.example.com", local_ip=ip).validate()

    def test_local_ip_settings_and_legacy_profiles(self):
        self.store.settings.setValue("profiles", json.dumps([
            {"name": "Legacy", "hostname": "old.example.com", "port": 25566},
            {"name": "LAN", "hostname": "lan.example.com", "port": 25567, "local_ip": "192.168.1.10"},
        ]))
        profiles = self.store.load()
        self.assertEqual("127.0.0.1", profiles[0].local_ip)
        self.assertEqual(25566, profiles[0].port)
        self.assertEqual("192.168.1.10", profiles[1].local_ip)
        self.store.save(profiles, "LAN")
        self.assertEqual(profiles, self.store.load())

    def test_local_ip_editor_save_switch_and_cancel(self):
        original = [gui.Profile("One", "one.example.com"),
                    gui.Profile("Two", "two.example.com", local_ip="192.168.1.10")]
        editor = gui.ProfileEditor(original, "One")
        self.assertEqual("127.0.0.1", editor.local_ip.text())
        editor.local_ip.setText("invalid")
        editor.accept()
        self.assertIn("IPv4", editor.error.text())
        editor.local_ip.setText(" 127.0.0.2 ")
        editor.items.setCurrentRow(1)
        self.assertEqual("127.0.0.2", editor.profiles[0].local_ip)
        self.assertEqual("192.168.1.10", editor.local_ip.text())
        editor.local_ip.setText("0.0.0.0")
        editor.accept()
        self.assertEqual(QDialog.DialogCode.Accepted, editor.result())
        self.assertEqual("0.0.0.0", editor.profiles[1].local_ip)
        self.store.save(editor.profiles, editor.selected)
        self.assertEqual(editor.profiles, self.store.load())
        cancelled = gui.ProfileEditor(original, "One")
        cancelled.local_ip.setText("127.0.0.3")
        cancelled.items.setCurrentRow(1)
        cancelled.reject()
        self.assertEqual("127.0.0.1", original[0].local_ip)

    def test_local_ip_display_and_clipboard(self):
        self.store.save([
            gui.Profile("Loopback", "mc.example.com", local_ip="127.0.0.2"),
            gui.Profile("LAN", "mc.example.com", 25566, "192.168.1.10"),
            gui.Profile("All", "mc.example.com", 25567, "0.0.0.0"),
        ], "Loopback")
        window = gui.MainWindow(self.store)
        self.windows.append(window)
        for row, address in enumerate(("127.0.0.2:25565", "192.168.1.10:25566", "127.0.0.1:25567")):
            with self.subTest(row=row):
                window.selector.setCurrentIndex(row)
                self.assertEqual(f"Join: {address}", window.address.text())
                window.copy.click()
                self.assertEqual(address, APP.clipboard().text())
                self.assertEqual(row != 2, window.listener_hint.isHidden())

    def test_settings_roundtrip_and_malformed_entries(self):
        profiles = [gui.Profile("One", "one.example.com"), gui.Profile("Two", "two.example.com", 25566)]
        self.store.save(profiles, "Two")
        self.assertEqual(profiles, self.store.load())
        self.assertEqual("Two", self.store.settings.value("selected"))
        self.store.settings.setValue("profiles", json.dumps([{"bad": 1}, {"name": "ok", "hostname": "ok.example.com", "port": 1234}, None]))
        self.assertEqual([gui.Profile("ok", "ok.example.com", 1234)], self.store.load())
        self.store.settings.setValue("profiles", "not json")
        self.assertEqual([], self.store.load())

    def test_editor_cancel_validation_selection_and_deletion(self):
        original = [gui.Profile("One", "one.example.com"), gui.Profile("Two", "two.example.com")]
        editor = gui.ProfileEditor(original, "One")
        editor.name.setText("two")
        editor.accept()
        self.assertNotEqual(QDialog.DialogCode.Accepted, editor.result())
        self.assertIn("unique", editor.error.text())
        editor.name.setText("Renamed")
        editor.items.setCurrentRow(1)
        self.assertEqual("Renamed", editor.profiles[0].name)
        editor.reject()
        self.assertEqual("One", original[0].name)
        editor = gui.ProfileEditor(original, "Two")
        editor.host.setText("invalid")
        editor.items.setCurrentRow(0)
        self.assertEqual(1, editor.items.currentRow())
        editor.delete_profile()
        editor.delete_profile()
        editor.accept()
        self.assertEqual([], editor.profiles)
        self.assertEqual("", editor.selected)
        editor.add_profile()
        editor.host.setText("new.example.com")
        editor.accept()
        self.assertEqual("new.example.com", editor.profiles[0].hostname)

    def test_process_arguments_readiness_and_shutdown(self):
        controller = self.controller("import sys,time\nprint(repr(sys.argv[1:]), flush=True)\nprint('Start Websocket ', end='', flush=True)\ntime.sleep(.05)\nprint('listener host=127.0.0.1:25565', flush=True)\ntime.sleep(60)\n")
        logs = []
        controller.log.connect(logs.append)
        controller.start(gui.Profile("Test", "mc.example.com", local_ip="127.0.0.2"))
        self.assertEqual("Starting", controller.state)
        self.assertTrue(until(lambda: controller.state == "On"))
        self.assertIn("['access', 'tcp', '--hostname', 'mc.example.com', '--url', '127.0.0.2:25565']", logs)
        self.assertIn("Starting local listener at 127.0.0.2:25565 for mc.example.com", logs)
        controller.start(gui.Profile("Other", "other.example.com"))
        controller.stop()
        self.assertTrue(until(lambda: controller.state == "Off"))
        self.assertEqual(QProcess.ProcessState.NotRunning, controller.process.state())

    def test_unexpected_exit_and_missing_binary(self):
        controller = self.controller("import sys\nprint('bind: address already in use', flush=True)\nsys.exit(1)\n")
        errors = []
        controller.failed.connect(errors.append)
        controller.start(gui.Profile("Test", "mc.example.com"))
        self.assertTrue(until(lambda: controller.state == "Error"))
        self.assertIn("code 1", errors[0])
        controller.executable = str(Path(self.temp.name) / "missing.exe")
        controller.start(gui.Profile("Test", "mc.example.com"))
        self.assertIn("missing", errors[-1])

    def test_startup_timeout(self):
        controller = self.controller("import time\ntime.sleep(60)\n")
        controller.start_timer.setInterval(100)
        controller.start(gui.Profile("Test", "mc.example.com"))
        self.assertTrue(until(lambda: controller.state == "Error"))
        self.assertEqual(QProcess.ProcessState.NotRunning, controller.process.state())

    def test_unlaunchable_binary(self):
        invalid = Path(self.temp.name) / "invalid.exe"
        invalid.write_text("not an executable", encoding="utf-8")
        controller = gui.TunnelController(executable=invalid)
        self.children.append(controller)
        errors = []
        controller.failed.connect(errors.append)
        controller.start(gui.Profile("Test", "mc.example.com"))
        self.assertTrue(until(lambda: controller.state == "Error"))
        self.assertIn("Could not start", errors[0])

    def test_close_to_tray_and_no_tray_fallback(self):
        window = gui.MainWindow(self.store)
        self.windows.append(window)
        window.show()
        with patch.object(window.tray, "isVisible", return_value=True), patch.object(window.tray, "showMessage") as notice:
            event = QCloseEvent()
            window.closeEvent(event)
            self.assertFalse(event.isAccepted())
            self.assertFalse(window.isVisible())
            self.assertFalse(window.quitting)
            notice.assert_called_once()
            window.closeEvent(QCloseEvent())
            notice.assert_called_once()
        window.show_window()
        with patch.object(window.tray, "isVisible", return_value=False), patch.object(window, "quit_app") as quit_app:
            window.closeEvent(QCloseEvent())
            quit_app.assert_called_once()

    def test_window_states_clipboard_and_tray_actions(self):
        self.store.save([gui.Profile("Test", "mc.example.com", 25567)], "Test")
        controller = self.controller("import time\nprint('Start Websocket listener', flush=True)\ntime.sleep(60)\n")
        window = gui.MainWindow(self.store, controller)
        self.windows.append(window)
        window.show()
        window.copy.click()
        self.assertEqual("127.0.0.1:25567", APP.clipboard().text())
        window.switch.click()
        self.assertTrue(until(lambda: controller.state == "On"))
        self.assertFalse(window.config.isEnabled())
        self.assertFalse(window.selector.isEnabled())
        self.assertTrue(window.switch.isChecked())
        window.log_toggle.click()
        self.assertFalse(window.logs.isHidden())
        window.tray_toggle.trigger()
        self.assertTrue(until(lambda: controller.state == "Off"))
        self.assertTrue(window.config.isEnabled())
        window.show_window()
        self.assertTrue(window.isVisible())
        # Prevent QApplication.quit from ending the test runner's shared event loop.
        controller.stopped.disconnect(window.on_stopped)
        window.tray_toggle.trigger()
        self.assertTrue(until(lambda: controller.state == "On"))
        window.quit_app()
        self.assertTrue(until(lambda: controller.process.state() == QProcess.ProcessState.NotRunning))
        self.assertTrue(window.quitting)

    def test_real_bundled_binary_listener_and_occupied_port(self):
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
        controller = gui.TunnelController()
        self.children.append(controller)
        controller.start(gui.Profile("Smoke", "unused.example.com", port))
        self.assertTrue(until(lambda: controller.state in ("On", "Error")))
        self.assertEqual("On", controller.state)
        controller.stop()
        self.assertTrue(until(lambda: controller.state == "Off"))
        with socket.socket() as occupied:
            if os.name == "nt":
                occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            occupied.bind(("127.0.0.1", port))
            occupied.listen()
            controller.start(gui.Profile("Smoke", "unused.example.com", port))
            self.assertTrue(until(lambda: controller.state == "Error"))


if __name__ == "__main__":
    unittest.main()
