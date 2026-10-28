"""Self-drawn navigation symbols; no external assets or font glyph dependency."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap


def _pen(color: QColor, width: float = 2.0) -> QPen:
    pen = QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    return pen


def navigation_icon(kind: str, color: str) -> QIcon:
    """统一的 2px 线性图标：芯片、波形、终端、滑杆，共 28×28 画布。"""

    pixmap = QPixmap(28, 28)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    stroke = QColor(color)
    painter.setPen(_pen(stroke))

    if kind == 'isp':
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(QRectF(8, 8, 12, 12), 2, 2)
        for offset in (11.5, 16.5):
            painter.drawLine(QPointF(offset, 4), QPointF(offset, 8))
            painter.drawLine(QPointF(offset, 20), QPointF(offset, 24))
            painter.drawLine(QPointF(4, offset), QPointF(8, offset))
            painter.drawLine(QPointF(20, offset), QPointF(24, offset))
    elif kind == 'connection':
        painter.setBrush(Qt.NoBrush)
        painter.drawPolyline([QPointF(3, 17), QPointF(8, 17), QPointF(11, 9),
                              QPointF(15, 20), QPointF(18, 12), QPointF(21, 12), QPointF(25, 12)])
        painter.setBrush(stroke)
        painter.drawEllipse(QPointF(25, 12), 1.6, 1.6)
    elif kind == 'commands':
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(QRectF(4, 6, 20, 16), 3, 3)
        painter.drawPolyline([QPointF(9, 11), QPointF(12.5, 14), QPointF(9, 17)])
        painter.drawLine(QPointF(15, 17), QPointF(19.5, 17))
    elif kind == 'controls':
        painter.setBrush(Qt.NoBrush)
        for x, knob in ((10.0, 12.0), (18.0, 17.0)):
            painter.drawLine(QPointF(x, 6), QPointF(x, 22))
            painter.setBrush(stroke)
            painter.drawEllipse(QPointF(x, knob), 2.4, 2.4)
            painter.setBrush(Qt.NoBrush)
    elif kind == 'menu':
        painter.setBrush(stroke)
        painter.setPen(Qt.NoPen)
        for y in (8, 14, 20):
            painter.drawEllipse(QRectF(12, y - 2, 4, 4))

    painter.end()
    return QIcon(pixmap)
