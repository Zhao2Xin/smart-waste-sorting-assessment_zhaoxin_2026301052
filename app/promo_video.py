"""Playback page for the bundled promotional video."""

from pathlib import Path

import cv2
from PyQt5 import QtCore, QtGui, QtWidgets

from vedio.video_module import VideoFileSource


PROMO_PATH = Path(__file__).resolve().parents[1] / "vedio" / "dep.mp4"


class VideoPreview(QtWidgets.QLabel):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self._image = QtGui.QImage()
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Ignored)

    def show_image(self, image: QtGui.QImage) -> None:
        self._image = image
        self._scale_image()

    def _scale_image(self) -> None:
        if not self._image.isNull():
            self.setPixmap(QtGui.QPixmap.fromImage(self._image).scaled(
                self.contentsRect().size(), QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            ))

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._scale_image()


class PromoVideoPage(QtWidgets.QWidget):
    back_requested = QtCore.pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._source = None
        self._frame_index = 0
        self._timer = QtCore.QTimer(self)
        self._timer.setTimerType(QtCore.Qt.PreciseTimer)
        self._timer.timeout.connect(self._advance)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("宣传视频")
        font = title.font()
        font.setPointSize(18)
        font.setBold(True)
        title.setFont(font)
        header.addWidget(title)
        header.addStretch()
        self.back_button = QtWidgets.QPushButton("返回主界面")
        self.back_button.clicked.connect(self.back_requested)
        header.addWidget(self.back_button)
        layout.addLayout(header)

        self.preview = VideoPreview("")
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        self.preview.setObjectName("videoPreview")
        layout.addWidget(self.preview, 1)

        controls = QtWidgets.QHBoxLayout()
        self.play_button = QtWidgets.QPushButton("播放")
        self.play_button.clicked.connect(self._toggle_playback)
        controls.addWidget(self.play_button)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setAccessibleName("宣传视频进度")
        self.slider.setTracking(False)
        self.slider.sliderMoved.connect(self._show_time)
        self.slider.valueChanged.connect(self._seek)
        controls.addWidget(self.slider, 1)
        self.time_label = QtWidgets.QLabel()
        controls.addWidget(self.time_label)
        layout.addLayout(controls)

    def start(self) -> None:
        self.stop()
        self._source = VideoFileSource(str(PROMO_PATH))
        if self._source.frame_count < 1 or self._source.source_fps <= 0 or not self._read_frame():
            self.stop()
            raise RuntimeError("无法读取宣传视频的画面或播放信息。")
        with QtCore.QSignalBlocker(self.slider):
            self.slider.setRange(0, self._source.frame_count - 1)
            self.slider.setValue(0)
        self._show_time(0)
        self._timer.setInterval(max(1, round(1000 / self._source.source_fps)))
        self._set_playing(self._source.frame_count > 1)

    def stop(self) -> None:
        self._set_playing(False)
        if self._source is not None:
            self._source.release()
            self._source = None

    def _set_playing(self, playing: bool) -> None:
        if playing:
            self._timer.start()
            self.play_button.setText("暂停")
        else:
            self._timer.stop()
            self.play_button.setText("重播" if self._at_end() else "播放")

    def _at_end(self) -> bool:
        return self._source is not None and self._frame_index >= self._source.frame_count - 1

    def _toggle_playback(self) -> None:
        if self._timer.isActive():
            self._set_playing(False)
        elif self._source is not None:
            if self._at_end() and not self._seek(0):
                return
            self._set_playing(not self._at_end())

    def _advance(self) -> None:
        if self.slider.isSliderDown():
            return
        if self._read_frame():
            self._sync_progress()
            if self._at_end():
                self._set_playing(False)
        else:
            self._set_playing(False)
            self.time_label.setText("无法读取视频帧")

    def _seek(self, frame_index: int) -> bool:
        if self._source is None:
            return False
        if not self._source.seek_frame(frame_index) or not self._read_frame():
            self._set_playing(False)
            self._sync_progress()
            self.time_label.setText("无法跳转到该位置")
            return False
        self._sync_progress()
        self._set_playing(self._timer.isActive() and not self._at_end())
        return True

    def _read_frame(self) -> bool:
        ok, frame = self._source.read()
        if not ok or frame is None:
            return False
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        height, width = rgb.shape[:2]
        self.preview.show_image(QtGui.QImage(
            rgb.data, width, height, rgb.strides[0], QtGui.QImage.Format_RGB888,
        ).copy())
        self._frame_index = self._source.current_frame - 1
        return True

    def _sync_progress(self) -> None:
        with QtCore.QSignalBlocker(self.slider):
            self.slider.setValue(self._frame_index)
        self._show_time(self._frame_index)

    def _show_time(self, frame_index: int) -> None:
        fps = self._source.source_fps
        elapsed = self._source.duration_seconds if frame_index == self.slider.maximum() else frame_index / fps
        self.time_label.setText("{} / {}".format(
            self._format_time(elapsed), self._format_time(self._source.duration_seconds),
        ))

    @staticmethod
    def _format_time(seconds: float) -> str:
        minutes, seconds = divmod(int(seconds), 60)
        return "{:02d}:{:02d}".format(minutes, seconds)
