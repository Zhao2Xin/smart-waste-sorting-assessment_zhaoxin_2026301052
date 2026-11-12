"""PyQt dashboard for video recognition and lower-controller communication."""

from typing import Dict

from PyQt5 import QtCore, QtGui, QtWidgets

from .communication_view import CommunicationView
from .controller import SystemState
from .detectors import Detection
from .protocol import WASTE_NAMES, WasteType
from .worker import SortingWorker


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


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, worker: SortingWorker, auto_start: bool = False) -> None:
        super().__init__()
        self.worker = worker
        self._processing = False
        self._video_frames = 0
        self._video_fps = 0.0
        self._video_frame = 0
        self._seeking = False
        self.count_labels = {}  # type: Dict[str, QtWidgets.QLabel]
        self.bin_labels = {}  # type: Dict[str, QtWidgets.QLabel]
        self._build_ui()
        self._connect_signals()
        self._auto_start = auto_start
        QtCore.QTimer.singleShot(50, self._initialize_worker)

    @QtCore.pyqtSlot()
    def _initialize_worker(self) -> None:
        try:
            self.worker.prepare_model()
        except Exception as exc:
            self.model_label.setText("加载失败")
            self.state_label.setText("运行异常")
            self.append_log("YOLO 模型加载失败：{}".format(exc))
            self.start_button.setEnabled(False)
            return
        self.worker.start()
        if self._auto_start:
            QtCore.QTimer.singleShot(500, self.toggle_processing)

    def _build_ui(self) -> None:
        self.setWindowTitle("智能垃圾分拣系统 · 招新考核")
        self.resize(1180, 775)
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("智能垃圾分拣上位机")
        title.setObjectName("titleLabel")
        title_font = title.font()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)
        header.addWidget(title)
        header.addStretch(1)

        status_layout = QtWidgets.QVBoxLayout()
        status_layout.setSpacing(6)
        self.state_label = QtWidgets.QLabel("等待启动")
        self.connection_label = QtWidgets.QLabel("未连接")
        self.fps_label = QtWidgets.QLabel("0")
        self.source_label = QtWidgets.QLabel("正在打开默认考核视频…")
        self.source_label.setTextFormat(QtCore.Qt.PlainText)
        self.source_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.model_label = QtWidgets.QLabel("等待加载…")
        for fields in (
            (("系统状态", self.state_label), ("下位机", self.connection_label), ("FPS", self.fps_label)),
            (("当前输入", self.source_label), ("YOLO 模型", self.model_label)),
        ):
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(6)
            for index, (caption, value) in enumerate(fields):
                if index:
                    row.addSpacing(7)
                value.setObjectName("statusValue")
                row.addWidget(QtWidgets.QLabel(caption))
                row.addWidget(value)
            row.addStretch()
            status_layout.addLayout(row)
        header.addLayout(status_layout)
        root.addLayout(header)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        vision_group = QtWidgets.QGroupBox("目标检测画面")
        vision_layout = QtWidgets.QVBoxLayout(vision_group)
        self.camera_label = VideoPreview("导入视频或打开摄像头，然后点击“开始识别”")
        self.camera_label.setObjectName("videoPreview")
        self.camera_label.setAlignment(QtCore.Qt.AlignCenter)
        self.camera_label.setMinimumSize(360, 250)
        vision_layout.addWidget(self.camera_label, 1)
        self._build_timeline(vision_layout)
        splitter.addWidget(vision_group)

        information = QtWidgets.QWidget()
        info_layout = QtWidgets.QVBoxLayout(information)
        info_layout.setContentsMargins(0, 0, 0, 0)

        result_group = QtWidgets.QGroupBox("当前 YOLO 识别结果")
        result_layout = QtWidgets.QFormLayout(result_group)
        result_layout.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldsStayAtSizeHint)
        self.class_label = QtWidgets.QLabel("—")
        self.waste_label = QtWidgets.QLabel("—")
        self.confidence_label = QtWidgets.QLabel("—")
        for value in (self.class_label, self.waste_label, self.confidence_label):
            value.setObjectName("detectionValue")
        result_layout.addRow("模型类别", self.class_label)
        result_layout.addRow("垃圾分类", self.waste_label)
        result_layout.addRow("置信度", self.confidence_label)
        info_layout.addWidget(result_group)

        stats_group = QtWidgets.QGroupBox("下位机确认后的分类统计")
        stats_layout = QtWidgets.QGridLayout(stats_group)
        stats_layout.setHorizontalSpacing(18)
        stats_layout.setColumnStretch(3, 1)
        for column, title_text in enumerate(("垃圾类别", "数量", "桶状态")):
            column_label = QtWidgets.QLabel(title_text)
            if column:
                column_label.setAlignment(QtCore.Qt.AlignCenter)
            stats_layout.addWidget(column_label, 0, column)
        for row, waste_type in enumerate(
            (WasteType.RECYCLABLE, WasteType.KITCHEN, WasteType.HAZARDOUS, WasteType.OTHER),
            start=1,
        ):
            name = WASTE_NAMES[waste_type]
            stats_layout.addWidget(QtWidgets.QLabel(name), row, 0)
            count = QtWidgets.QLabel("0")
            count.setAlignment(QtCore.Qt.AlignCenter)
            status = QtWidgets.QLabel("正常")
            status.setAlignment(QtCore.Qt.AlignCenter)
            self.count_labels[name] = count
            self.bin_labels[name] = status
            stats_layout.addWidget(count, row, 1)
            stats_layout.addWidget(status, row, 2)
        info_layout.addWidget(stats_group)

        communication_group = QtWidgets.QGroupBox("下位机通信")
        communication_layout = QtWidgets.QVBoxLayout(communication_group)
        log_options = QtWidgets.QHBoxLayout()
        log_options.addWidget(QtWidgets.QLabel("最新轮次在上"))
        log_options.addStretch()
        self.clear_log_button = QtWidgets.QPushButton("清空日志")
        log_options.addWidget(self.clear_log_button)
        self.raw_frames = QtWidgets.QCheckBox("显示原始帧")
        log_options.addWidget(self.raw_frames)
        communication_layout.addLayout(log_options)
        self.log_browser = CommunicationView()
        communication_layout.addWidget(self.log_browser)
        info_layout.addWidget(communication_group, 1)
        splitter.addWidget(information)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([820, 320])
        root.addWidget(splitter, 1)

        controls = QtWidgets.QGroupBox("运行控制")
        self.controls_layout = controls_layout = QtWidgets.QHBoxLayout(controls)
        self.video_button = QtWidgets.QPushButton("导入视频")
        self.camera_button = QtWidgets.QPushButton("打开摄像头")
        self.start_button = QtWidgets.QPushButton("开始识别")
        self.start_button.setObjectName("primaryButton")
        self.reset_button = QtWidgets.QPushButton("重置统计")
        self.quit_button = QtWidgets.QPushButton("退出")
        controls_layout.addWidget(self.video_button)
        controls_layout.addWidget(self.camera_button)
        controls_layout.addStretch(1)
        controls_layout.addWidget(self.start_button)
        controls_layout.addWidget(self.reset_button)
        controls_layout.addWidget(self.quit_button)
        root.addWidget(controls)

        self.setStyleSheet(
            "QMainWindow { background: #edf2f6; }"
            "QLabel, QCheckBox { color: #243640; }"
            "QGroupBox { color: #263d49; background: white; border: 1px solid #d5e0e7;"
            " border-radius: 8px; font-weight: bold; margin-top: 12px; padding-top: 6px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 11px; padding: 0 4px; }"
            "QPushButton { color: #243640; background: white; border: 1px solid #c7d6de;"
            " border-radius: 6px; padding: 4px 11px; min-height: 24px; }"
            "QPushButton:hover { background: #edf5f7; }"
            "QPushButton:focus { border-color: #08796b; }"
            "QPushButton:disabled { color: #697d86; background: #edf2f4; }"
            "#primaryButton { background: #08685d; color: white; border-color: #08685d; }"
            "#primaryButton:hover, #primaryButton:focus { background: #0c8073; }"
            "#primaryButton:disabled { background: #e1eae8; color: #526b67; border-color: #d2dedb; }"
            "#titleLabel { color: #17323c; }"
            "#statusValue { background: #dce9ee; border-radius: 5px; padding: 3px 7px; }"
            "#detectionValue { background: #dce9ee; border-radius: 5px; padding: 2px 7px;"
            " font-weight: bold; }"
            "#videoPreview { background: #182630; color: #eff6fa; border-radius: 6px; }"
            "QTreeWidget { color: #253842; background: #fbfcfd; border: 1px solid #d5e0e7;"
            " border-radius: 5px; }"
            "QTreeWidget::item { padding: 3px 0; }"
            "QTreeWidget::item:selected { color: #17323c; background: #c9e4e0; }"
        )

    def _build_timeline(self, layout: QtWidgets.QVBoxLayout) -> None:
        self.timeline = QtWidgets.QWidget()
        timeline_layout = QtWidgets.QVBoxLayout(self.timeline)
        timeline_layout.setContentsMargins(0, 0, 0, 0)
        timeline_layout.setSpacing(4)
        self.timeline_label = QtWidgets.QLabel()
        timeline_layout.addWidget(self.timeline_label)
        self.video_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.video_slider.setAccessibleName("视频进度")
        self.video_slider.setTracking(False)
        timeline_layout.addWidget(self.video_slider)
        self.timeline_steps = steps = QtWidgets.QHBoxLayout()
        self.previous_frame = QtWidgets.QPushButton("上一帧")
        self.next_frame = QtWidgets.QPushButton("下一帧")
        self.first_frame = QtWidgets.QPushButton("回到开头")
        for button in (self.previous_frame, self.next_frame, self.first_frame):
            steps.addWidget(button)
        steps.addStretch()
        timeline_layout.addLayout(steps)
        layout.addWidget(self.timeline)
        self.timeline.hide()

    def _connect_signals(self) -> None:
        self.video_button.clicked.connect(self.import_video)
        self.camera_button.clicked.connect(self.open_camera)
        self.start_button.clicked.connect(self.toggle_processing)
        self.reset_button.clicked.connect(self.worker.request_reset)
        self.quit_button.clicked.connect(self.close)
        self.worker.frame_ready.connect(self.update_frame)
        self.worker.state_changed.connect(self.update_state)
        self.worker.counts_changed.connect(self.update_counts)
        self.worker.bins_changed.connect(self.update_bins)
        self.worker.detection_changed.connect(self.update_detection)
        self.worker.model_changed.connect(self.model_label.setText)
        self.worker.source_changed.connect(self.update_source_label)
        self.worker.source_finished.connect(self.on_source_finished)
        self.worker.log_message.connect(self.append_log)
        self.worker.connection_changed.connect(self.update_connection)
        self.worker.fps_changed.connect(lambda value: self.fps_label.setText("{:.0f}".format(value)))
        self.worker.video_opened.connect(self.set_video_metadata)
        self.worker.video_position_changed.connect(self.update_video_position)
        self.worker.video_seek_finished.connect(self.finish_video_seek)
        self.video_slider.sliderMoved.connect(self._show_video_position)
        self.video_slider.valueChanged.connect(self.seek_video)
        self.previous_frame.clicked.connect(lambda: self.seek_video(self._video_frame - 1))
        self.next_frame.clicked.connect(lambda: self.seek_video(self._video_frame + 1))
        self.first_frame.clicked.connect(lambda: self.seek_video(0))
        self.clear_log_button.clicked.connect(self.confirm_clear_log)
        self.raw_frames.toggled.connect(self.log_browser.show_raw)

    @QtCore.pyqtSlot()
    def import_video(self) -> None:
        """TODO 8：弹出视频选择框并调用 worker.request_source。"""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
        self,
        "选择视频文件",
        "",
        "视频文件 (*.mp4 *.avi *.mov *.mkv);;所有文件 (*)",
        )

        if not path:
            return

        self.worker.request_source("video", path)

    @QtCore.pyqtSlot()
    def open_camera(self) -> None:
        """TODO 9：选择/打开摄像头并调用 worker.request_source。"""
        camera_index, ok = QtWidgets.QInputDialog.getInt(
            self,
            "打开摄像头",
            "摄像头编号",
            0,  # 默认值
            0,  # 最小值
        )

        if not ok:
            return

        self.worker.request_source("camera", str(camera_index))

    def _set_processing(self, enabled: bool) -> None:
        self._processing = enabled
        if enabled:
            self.worker.start_processing()
            self.start_button.setText("暂停识别")
        else:
            self.worker.pause_processing()
            self.start_button.setText("开始识别")

    @QtCore.pyqtSlot()
    def toggle_processing(self) -> None:
        if self._seeking:
            return
        self._set_processing(not self._processing)
        self.append_log("视觉识别已{}".format("启动" if self._processing else "暂停"))

    @QtCore.pyqtSlot()
    def on_source_finished(self) -> None:
        if self._seeking:
            return
        self._processing = False
        self.start_button.setText("重新播放")

    @QtCore.pyqtSlot(int, float)
    def set_video_metadata(self, frames: int, fps: float) -> None:
        self._video_frames = frames
        self._video_fps = fps
        if frames > 0:
            self.timeline_steps.addWidget(self.start_button)
        else:
            self.controls_layout.insertWidget(3, self.start_button)
        self.start_button.show()
        self.timeline.setVisible(frames > 0)
        with QtCore.QSignalBlocker(self.video_slider):
            self.video_slider.setRange(0, max(0, frames - 1))
            self.video_slider.setValue(0)
        self._video_frame = 0
        self._show_video_position(0)

    @QtCore.pyqtSlot(str)
    def update_source_label(self, source: str) -> None:
        self.source_label.setToolTip(source)
        self.source_label.setText(self.source_label.fontMetrics().elidedText(
            source, QtCore.Qt.ElideMiddle, 265,
        ))

    @staticmethod
    def _format_time(seconds: float) -> str:
        minutes, tenths = divmod(int(round(seconds * 10)), 600)
        return "{:02d}:{:02d}.{}".format(minutes, tenths // 10, tenths % 10)

    def _show_video_position(self, frame: int) -> None:
        if not self._video_frames:
            return
        label = "第 {} / {} 帧".format(frame + 1, self._video_frames)
        if self._video_fps > 0:
            label += "   ·   {} / {}".format(
                self._format_time(frame / self._video_fps),
                self._format_time(self._video_frames / self._video_fps),
            )
        self.timeline_label.setText(label)
        self.previous_frame.setEnabled(frame > 0)
        self.next_frame.setEnabled(frame < self._video_frames - 1)

    @QtCore.pyqtSlot(int)
    def update_video_position(self, frame: int) -> None:
        if self._seeking or self.video_slider.isSliderDown() or not self._video_frames:
            return
        self._video_frame = min(max(0, frame), self._video_frames - 1)
        with QtCore.QSignalBlocker(self.video_slider):
            self.video_slider.setValue(self._video_frame)
        self._show_video_position(self._video_frame)

    @QtCore.pyqtSlot(int)
    def seek_video(self, frame: int) -> None:
        if self._seeking or not 0 <= frame < self._video_frames:
            return
        self._seeking = True
        self._processing = False
        self.start_button.setText("开始识别")
        self.start_button.setEnabled(False)
        self.timeline.setEnabled(False)
        with QtCore.QSignalBlocker(self.video_slider):
            self.video_slider.setValue(frame)
        self._show_video_position(frame)
        self.worker.request_video_seek(frame)

    @QtCore.pyqtSlot(bool)
    def finish_video_seek(self, succeeded: bool) -> None:
        self._seeking = False
        self.start_button.setEnabled(True)
        self.timeline.setEnabled(True)
        if not succeeded:
            self.update_video_position(self._video_frame)

    @QtCore.pyqtSlot(QtGui.QImage)
    def update_frame(self, image: QtGui.QImage) -> None:
        self.camera_label.show_image(image)

    @QtCore.pyqtSlot(object)
    def update_detection(self, detection: Detection) -> None:
        if detection is None:
            self.class_label.setText("—")
            self.waste_label.setText("—")
            self.confidence_label.setText("—")
            return
        self.class_label.setText(detection.class_name)
        self.waste_label.setText(WASTE_NAMES[detection.waste_type])
        self.confidence_label.setText("{:.1f}%".format(detection.confidence * 100))

    @QtCore.pyqtSlot(object)
    def update_counts(self, values: dict) -> None:
        for name, value in values.items():
            if name in self.count_labels:
                self.count_labels[name].setText(str(value))

    @QtCore.pyqtSlot(object)
    def update_bins(self, values: dict) -> None:
        for name, is_full in values.items():
            if name in self.bin_labels:
                label = self.bin_labels[name]
                label.setText("满载" if is_full else "正常")
                label.setStyleSheet("color: #c62828;" if is_full else "color: #2e7d32;")

    @QtCore.pyqtSlot(str)
    def append_log(self, message: str) -> None:
        self.log_browser.append_message(message)

    @QtCore.pyqtSlot()
    def confirm_clear_log(self) -> None:
        dialog = QtWidgets.QMessageBox(self)
        dialog.setWindowTitle("清空日志")
        dialog.setIcon(QtWidgets.QMessageBox.Question)
        dialog.setText("确定清空当前通信日志吗？")
        clear = dialog.addButton("清空日志", QtWidgets.QMessageBox.DestructiveRole)
        cancel = dialog.addButton("取消", QtWidgets.QMessageBox.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.setEscapeButton(cancel)
        dialog.exec_()
        if dialog.clickedButton() is clear:
            self.log_browser.clear_history()

    @QtCore.pyqtSlot(str)
    def update_state(self, state: str) -> None:
        self.state_label.setText(state)
        if state == SystemState.ERROR.value:
            self.log_browser.interrupt_round("通信异常")

    @QtCore.pyqtSlot(bool)
    def update_connection(self, connected: bool) -> None:
        if not connected:
            self.log_browser.interrupt_round("连接断开")
        self.connection_label.setText("已连接" if connected else "未连接")
        self.connection_label.setStyleSheet("color: #2e7d32;" if connected else "color: #c62828;")

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.worker.stop()
        if not self.worker.wait(5000):
            self.append_log("工作线程未在规定时间内退出")
        event.accept()
