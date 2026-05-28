from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, Signal, QRect, QPoint, QSize
from PySide6.QtGui import QPainter, QColor, QBrush, QPen, QFont, QMouseEvent
from PySide6 import QtCore, QtWidgets


class KeyframeTimeline(QWidget):
    seek_requested = Signal(int)
    mark_requested = Signal(int)

    COLORS = [
        QColor(255, 90, 90, 140),
        QColor(80, 170, 255, 140),
        QColor(80, 255, 100, 140),
        QColor(255, 200, 40, 140),
        QColor(190, 80, 255, 140),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(52)
        self.setFixedHeight(52)
        self.setMouseTracking(True)
        self._total_frames = 100
        self._current_frame = 1
        self._sections = []
        self._hover_frame = -1
        self._margin_left = 8
        self._margin_right = 8
        self._bar_top = 18
        self._bar_height = 16
        self._tick_height = 8
        self.setCursor(Qt.PointingHandCursor)

    def set_total_frames(self, total):
        self._total_frames = max(1, total)
        self.update()

    def set_current_frame(self, frame):
        self._current_frame = max(1, min(frame, self._total_frames))
        self.update()

    def set_sections(self, sections):
        self._sections = list(sections)
        self.update()

    def paintEvent(self, event):
        w = self.width()
        h = self.height()
        bar_x = self._margin_left
        bar_w = w - self._margin_left - self._margin_right
        bar_y = self._bar_top
        bar_h = self._bar_height
        total = self._total_frames
        if bar_w <= 0 or total <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # background
        painter.fillRect(bar_x, bar_y, bar_w, bar_h, QColor(50, 50, 55))

        # section blocks
        for i, sec in enumerate(self._sections):
            s = sec.start if hasattr(sec, 'start') else sec[0]
            e = (sec.stop - 1) if hasattr(sec, 'stop') else sec[1]
            s = max(1, s)
            e = max(1, min(e, total))
            if s >= e:
                continue
            x = bar_x + int((s - 1) / total * bar_w)
            bw = max(2, int((e - s + 1) / total * bar_w))
            color = self.COLORS[i % len(self.COLORS)]
            painter.fillRect(QRect(x, bar_y, bw, bar_h), QBrush(color))
            painter.setPen(QPen(color.darker(130), 1))
            painter.drawRect(QRect(x, bar_y, bw - 1, bar_h - 1))

        # playhead
        px = bar_x + int((self._current_frame - 1) / total * bar_w)
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.drawLine(px, bar_y - 3, px, bar_y + bar_h + 3)
        painter.setBrush(QBrush(QColor(255, 255, 255)))
        painter.drawEllipse(QPoint(px, bar_y + bar_h // 2), 4, 4)

        # hover indicator
        if self._hover_frame > 0:
            hx = bar_x + int((self._hover_frame - 1) / total * bar_w)
            painter.setPen(QPen(QColor(255, 255, 255, 100), 1, Qt.DashLine))
            painter.drawLine(hx, bar_y - 2, hx, bar_y + bar_h + 2)

        # time ruler
        painter.setPen(QPen(QColor(180, 180, 180)))
        font = QFont('Segoe UI', 8)
        painter.setFont(font)
        tick_count = min(10, total)
        for i in range(tick_count + 1):
            fn = 1 + int(i * (total - 1) / max(1, tick_count))
            tx = bar_x + int((fn - 1) / total * bar_w)
            painter.drawLine(tx, bar_y + bar_h + 1, tx, bar_y + bar_h + self._tick_height)
            label = str(fn)
            text_w = painter.fontMetrics().horizontalAdvance(label)
            painter.drawText(tx - text_w // 2, bar_y + bar_h + self._tick_height + 12, label)

        # hover frame label
        if self._hover_frame > 0:
            label = f'Frame {self._hover_frame}'
            painter.setPen(QColor(220, 220, 220))
            painter.drawText(bar_x, 12, label)

        # border
        painter.setPen(QPen(QColor(80, 80, 85), 1))
        painter.drawRect(QRect(bar_x, bar_y, bar_w, bar_h))

        painter.end()

    def _frame_at_x(self, x):
        bar_w = self.width() - self._margin_left - self._margin_right
        if bar_w <= 0:
            return -1
        ratio = max(0.0, min(1.0, (x - self._margin_left) / bar_w))
        return 1 + int(ratio * (self._total_frames - 1))

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            fn = self._frame_at_x(event.position().x())
            if fn > 0:
                self.seek_requested.emit(fn)

    def mouseMoveEvent(self, event: QMouseEvent):
        fn = self._frame_at_x(event.position().x())
        if fn != self._hover_frame:
            self._hover_frame = fn
            self.update()

    def leaveEvent(self, event):
        self._hover_frame = -1
        self.update()

    def sizeHint(self):
        return self.minimumSizeHint()

    def minimumSizeHint(self):
        return QtCore.QSize(200, 52)