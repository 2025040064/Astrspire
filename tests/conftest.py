"""Use the actual Windows UI font for reproducible offscreen geometry checks."""
from pathlib import Path
import pytest


@pytest.fixture(scope='session', autouse=True)
def windows_ui_font(qapp):
    from PySide6.QtGui import QFont, QFontDatabase
    font = Path('C:/Windows/Fonts/msyh.ttc')
    if font.is_file():
        QFontDatabase.addApplicationFont(str(font))
        qapp.setFont(QFont('Microsoft YaHei UI', 10))
