"""Application entry-point"""

# Standard library
import os
import sys
import time
import json
import threading

# Dependencies
from PyQt5 import QtCore, QtGui, QtQuick, QtTest

# Local libraries
from . import util, compat, control, settings, ipc

MODULE_DIR = os.path.dirname(__file__)
QML_IMPORT_DIR = os.path.join(MODULE_DIR, "qml")
APP_PATH = os.path.join(MODULE_DIR, "qml", "main.qml")
ICON_PATH = os.path.join(MODULE_DIR, "icon.ico")


class Window(QtQuick.QQuickView):
    """Main application window"""

    def __init__(self, parent=None):
        super(Window, self).__init__(None)
        self.parent = parent

        self.setTitle(settings.WindowTitle)
        self.setResizeMode(self.SizeRootObjectToView)

        self.resize(*settings.WindowSize)
        self.setMinimumSize(QtCore.QSize(430, 300))

    def event(self, event):
        """Allow GUI to be closed upon holding Shift"""
        if event.type() == QtCore.QEvent.Close:

            states = self.parent.controller.states
            if any(state in states for state in ("ready", "finished")):
                event.accept()
            else:
                print("Not ready")
                event.ignore()

        return super(Window, self).event(event)


class Application(QtGui.QGuiApplication):
    """Pyblish QML wrapper around QGuiApplication

    Provides production and debug launchers along with controller
    initialisation and orchestration.

    """

    shown = QtCore.pyqtSignal()
    hidden = QtCore.pyqtSignal()
    quitted = QtCore.pyqtSignal()

    def __init__(self, source):
        super(Application, self).__init__(sys.argv)

        self.setWindowIcon(QtGui.QIcon(ICON_PATH))

        window = Window(self)
        window.statusChanged.connect(self.on_status_changed)

        engine = window.engine()
        engine.addImportPath(QML_IMPORT_DIR)

        controller = control.Controller()

        context = engine.rootContext()
        context.setContextProperty("app", controller)

        self.window = window
        self.engine = engine
        self.controller = controller
        self.clients = dict()
        self.current_client = None

        self.shown.connect(self.show)
        self.hidden.connect(self.hide)
        self.quitted.connect(self.quit)

        window.setSource(QtCore.QUrl.fromLocalFile(source))

    def on_status_changed(self, status):
        if status == QtQuick.QQuickView.Error:
            self.quit()

    def register_client(self, port):
        self.current_client = port
        self.clients[port] = {
            "lastSeen": time.time()
        }

    def deregister_client(self, port):
        self.clients.pop(port)

    def show(self, client_settings=None):
        """Display GUI

        Once the QML interface has been loaded, use this
        to display it.

        Arguments:
            port (int): Client asking to show GUI.
            client_settings (dict, optional): Visual settings, see settings.py

        """

        window = self.window

        if client_settings:
            # Apply client-side settings
            settings.from_dict(client_settings)
            window.setWidth(client_settings["WindowSize"][0])
            window.setHeight(client_settings["WindowSize"][1])
            window.setTitle(client_settings["WindowTitle"])

        print("Settings:")
        for key, value in settings.to_dict().items():
            print("  %s = %s" % (key, value))

        previously_hidden = not window.isVisible()

        window.requestActivate()
        window.showNormal()

        if os.name == "nt":
            # Work-around for window appearing behind
            # other windows upon being shown once hidden.
            previous_flags = window.flags()
            window.setFlags(previous_flags | QtCore.Qt.WindowStaysOnTopHint)
            window.setFlags(previous_flags)

        if previously_hidden:
            # Give statemachine enough time to boot up
            if not any(state in self.controller.states
                       for state in ["ready", "finished"]):
                util.timer("ready")

                ready = QtTest.QSignalSpy(self.controller.ready)

                count = len(ready)
                ready.wait(1000)
                if len(ready) != count + 1:
                    print("Warning: Could not enter ready state")

                util.timer_end("ready", "Awaited statemachine for %.2f ms")

            self.controller.show.emit()
            self.controller.reset()

    def hide(self):
        """Hide GUI

        Process remains active and may be shown
        via a call to `show()`

        """

        self.window.hide()

    def listen(self):
        """Listen on incoming messages from host

        TODO(marcus): We can't use this, as we are already listening on stdin
            through client.py. Do use this, we will have to find a way to
            receive multiple signals from the same stdin, and channel them
            to their corresponding source.

        """

        def _listen():
            for line in iter(sys.stdin.readline, b""):
                try:
                    data = json.loads(line)

                except:
                    # This must be a regular error message
                    sys.stdout.write(line)

                else:
                    if data["header"] == "pyblish-qml:popen.parent":
                        payload = data["payload"]

                        # We can't call methods directly, as we are running
                        # in a thread. Instead, we emit signals that do the
                        # job for us.
                        signal = {
                            "show": "shown",
                            "hide": "hidden",
                            "quit": "quitted"
                        }.get(payload["signal"])

                        if not signal:
                            print("'{name}' was unavailable.".format(
                                **payload))
                        else:
                            getattr(self, payload["name"]).emit()

                    else:
                        # If it is JSON, but not one of ours, just print it.
                        sys.stdout.write(line)

        t = threading.Thread(target=_listen)
        t.daemon = True
        t.start()


def main(demo=False, aschild=False):
    """Start the Qt-runtime and show the window

    Arguments:
        aschild (bool, optional): Run as child of parent process

    """

    if aschild:
        print("Starting pyblish-qml..")
        compat.main()
        app = Application(APP_PATH)
        app.show()
        return app.exec_()

    else:
        print("Starting pyblish-qml server..")
        service = ipc.service.MockService() if demo else ipc.service.Service()
        ipc.server.Server(service).wait()
