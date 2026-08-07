from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Add src to sys.path so kronika and application modules can be found
src_dir = str(Path(__file__).parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

console_dir = str(Path(__file__).parent)
if console_dir not in sys.path:
    sys.path.insert(0, console_dir)

# PyQt6 must be imported before qasync so that qasync's Qt-binding detection succeeds.
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtQml import QQmlApplicationEngine, qmlRegisterSingletonType
from PyQt6.QtWidgets import QApplication

import qasync
from bridge import ConsoleBridge
from kronika.logging import setup_logging

log = logging.getLogger("kronika-console")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    setup_logging()

    app = QApplication(sys.argv)
    app.setOrganizationName("iohaus")
    app.setApplicationName("kronika-console")
    app.setApplicationVersion("2.0.0")

    # Install qasync event loop — must happen before any asyncio calls.
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    font_root = Path(__file__).parent / "fonts" / "Titillium_Web"
    if font_root.exists():
        for font_path in font_root.glob("*.ttf"):
            QFontDatabase.addApplicationFont(str(font_path))

    bridge = ConsoleBridge()

    # Register KrTheme as a QML singleton (same pattern as HatchGUI's DkTheme)
    theme_url = QUrl.fromLocalFile(str(Path(__file__).parent / "qml" / "KrTheme.qml"))
    qmlRegisterSingletonType(theme_url, "Kr", 1, 0, "KrTheme")

    engine = QQmlApplicationEngine()
    # Add qml/ as the import root so directory imports (import "qml") work in main.qml
    engine.addImportPath(str(Path(__file__).parent / "qml"))
    engine.rootContext().setContextProperty("bridge", bridge)

    qml_file = Path(__file__).parent / "main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_file)))

    if not engine.rootObjects():
        log.error("QML engine failed to load root object — check QML syntax.")
        return 1

    log.info("Kronika Operator Console running.")
    with loop:
        loop.run_forever()

    return 0


if __name__ == "__main__":
    sys.exit(main())
