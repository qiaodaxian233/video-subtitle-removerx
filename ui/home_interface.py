import os
import json
import cv2
import threading
import multiprocessing
import time
import traceback
import numpy as np
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout
from PySide6.QtCore import Slot, QRect, Signal, Qt, QTimer
from PySide6 import QtWidgets
from datetime import datetime
from qfluentwidgets import (PushButton, CardWidget, TextEdit, FluentIcon, ToggleButton)
from ui.setting_interface import SettingInterface
from ui.component.video_display_component import VideoDisplayComponent
from ui.component.task_list_component import TaskListComponent, TaskStatus, TaskOptions
from ui.component.keyframe_timeline import KeyframeTimeline
from ui.icon.my_fluent_icon import MyFluentIcon
from backend.config import config, tr
from backend.tools.constant import InpaintMode
from backend.tools.subtitle_remover_remote_call import SubtitleRemoverRemoteCall
from backend.tools.process_manager import ProcessManager
from backend.tools.common_tools import get_readable_path, is_image_file, read_image

class HomeInterface(QWidget):
    progress_signal = Signal(int, bool)
    append_log_signal = Signal(list)
    update_preview_with_comp_signal = Signal(list)
    task_error_signal = Signal(object)
    toggle_buttons_signal = Signal(bool)  # True=显示运行按钮, False=显示停止按钮
    task_status_signal = Signal(int, object)  # (task_index, TaskStatus)
    select_task_signal = Signal(int)  # task_index
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("HomeInterface")
        # 初始化一些变量
        self.video_path = None
        self.video_cap = None
        self.fps = None
        self.frame_count = None
        self.frame_width = None
        self.frame_height = None
        self.se = None  # 后台字幕提取器

        # 字幕区域参数
        self.xmin = None
        self.xmax = None
        self.ymin = None
        self.ymax = None

        # 添加自动滚动控制标志
        self.auto_scroll = True
        self._stop_event = threading.Event()  # 线程安全的停止信号
        self._worker_thread = None
        self.running_process = None
        self._saved_inpaint_mode = None  # 保存图片锁定前的 inpaint 模式
        self._video_cap_lock = threading.Lock()  # 保护 video_cap 的线程锁
        self._is_manual_mask_processing = False  # 是否正在处理手动蒙版

        # 当前正在处理的任务索引
        self.current_processing_task_index = -1

        self.__init_widgets()
        self.progress_signal.connect(self.update_progress)
        self.append_log_signal.connect(self.append_log)
        self.update_preview_with_comp_signal.connect(self.update_preview_with_comp)
        self.task_error_signal.connect(self.on_task_error)
        self.toggle_buttons_signal.connect(self._toggle_buttons)
        self.task_status_signal.connect(lambda idx, status: self.task_list_component.update_task_status(idx, status))
        self.select_task_signal.connect(self.task_list_component.select_task)

    def __init_widgets(self):
        """创建主页面"""
        main_layout = QHBoxLayout(self)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(16, 16, 16, 16)

        # 左侧视频区域
        left_layout = QVBoxLayout()
        left_layout.setSpacing(8)
        
        # 创建视频显示组件
        self.video_display_component = VideoDisplayComponent(self)
        self.video_display_component.ab_sections_changed.connect(self.ab_sections_changed)
        self.video_display_component.selections_changed.connect(self.selections_changed)
        self.video_display_component.ab_sections_changed.connect(self._save_ab_sections)
        left_layout.addWidget(self.video_display_component)
        
        # 获取视频显示和滑块的引用
        self.video_display = self.video_display_component.video_display
        self.video_slider = self.video_display_component.video_slider
        self.video_slider.valueChanged.connect(self.slider_changed)
        self.video_slider.valueChanged.connect(self._on_slider_for_timeline)

        self.mask_controls_container = CardWidget(self)
        mask_layout = QHBoxLayout()
        mask_layout.setContentsMargins(12, 8, 12, 8)
        mask_layout.setSpacing(12)

        self.paint_mask_toggle = ToggleButton('Paint Mask', self)
        self.paint_mask_toggle.setToolTip("Left click to paint, right click to erase. Toggle to show/hide mask layer.")
        self.paint_mask_toggle.toggled.connect(self._on_paint_mask_toggled)
        mask_layout.addWidget(self.paint_mask_toggle)

        from PySide6.QtWidgets import QLabel, QSlider as QHSlider
        brush_label = QLabel('Brush: 15')
        self.brush_size_label = brush_label
        mask_layout.addWidget(brush_label)

        self.brush_size_slider = QHSlider(Qt.Horizontal)
        self.brush_size_slider.setMinimum(1)
        self.brush_size_slider.setMaximum(80)
        self.brush_size_slider.setValue(15)
        self.brush_size_slider.setFixedWidth(120)
        self.brush_size_slider.valueChanged.connect(self._on_brush_size_changed)
        mask_layout.addWidget(self.brush_size_slider)

        self.clear_mask_btn = PushButton('Clear Mask', self)
        self.clear_mask_btn.setIcon(FluentIcon.DELETE)
        self.clear_mask_btn.clicked.connect(self._on_clear_mask)
        mask_layout.addWidget(self.clear_mask_btn)

        self.auto_segment_btn = PushButton('Auto Segment', self)
        self.auto_segment_btn.setIcon(FluentIcon.ROBOT)
        self.auto_segment_btn.setToolTip('Auto-detect scene changes and create AB sections')
        self.auto_segment_btn.clicked.connect(self._on_auto_segment)
        mask_layout.addWidget(self.auto_segment_btn)

        mask_layout.addStretch()
        self.mask_controls_container.setLayout(mask_layout)
        self.mask_controls_container.setVisible(False)
        left_layout.addWidget(self.mask_controls_container)

        self.mask_progress_bar = QtWidgets.QProgressBar(self)
        self.mask_progress_bar.setRange(0, 100)
        self.mask_progress_bar.setValue(0)
        self.mask_progress_bar.setFixedHeight(8)
        self.mask_progress_bar.setTextVisible(False)
        self.mask_progress_bar.setVisible(False)
        left_layout.addWidget(self.mask_progress_bar)

        # 每次设置变更后重新根据 ProPainter 模式显隐蒙版控件
        config.inpaintMode.valueChanged.connect(lambda v: self._update_mask_controls_visibility())
        self._update_mask_controls_visibility()

        self.keyframe_container = CardWidget(self)
        kf_layout = QVBoxLayout()
        kf_layout.setContentsMargins(12, 8, 12, 6)
        kf_layout.setSpacing(4)

        self.keyframe_timeline = KeyframeTimeline(self)
        self.keyframe_timeline.seek_requested.connect(self._on_timeline_seek)
        kf_layout.addWidget(self.keyframe_timeline)

        kf_ctrl = QHBoxLayout()
        kf_ctrl.setSpacing(6)
        self.preset_combo = QtWidgets.QComboBox(self)
        self.preset_combo.setMinimumWidth(100)
        self.preset_combo.setToolTip('Select a saved preset')
        kf_ctrl.addWidget(self.preset_combo, 1)

        self.preset_save_btn = PushButton('Save', self)
        self.preset_save_btn.clicked.connect(self._on_preset_save)
        kf_ctrl.addWidget(self.preset_save_btn)

        self.preset_load_btn = PushButton('Load', self)
        self.preset_load_btn.clicked.connect(self._on_preset_load)
        kf_ctrl.addWidget(self.preset_load_btn)

        self.preset_delete_btn = PushButton('Del', self)
        self.preset_delete_btn.clicked.connect(self._on_preset_delete)
        kf_ctrl.addWidget(self.preset_delete_btn)

        kf_layout.addLayout(kf_ctrl)
        self.keyframe_container.setLayout(kf_layout)
        is_propainter = config.inpaintMode.value == InpaintMode.PROPAINTER
        self.keyframe_container.setVisible(is_propainter)
        left_layout.addWidget(self.keyframe_container)

        # 输出文本区域
        self.output_text = TextEdit()
        self.output_text.setMinimumHeight(150)
        self.output_text.setReadOnly(True)
        self.output_text.document().setDocumentMargin(10)        
        # 连接滚动条值变化信号
        self.output_text.verticalScrollBar().valueChanged.connect(self.on_scroll_change)
        
        output_container = CardWidget(self)
        output_layout = QVBoxLayout()
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.addWidget(self.output_text)
        output_container.setLayout(output_layout)
        left_layout.addWidget(output_container)

        main_layout.addLayout(left_layout, 2)

        # 右侧设置区域
        right_layout = QVBoxLayout()
        right_layout.setSpacing(10)

        # 设置容器
        settings_container = CardWidget(self)
        self.setting_interface = SettingInterface(settings_container)
        settings_container.setLayout(self.setting_interface)
        right_layout.addWidget(settings_container)
        
        # 添加任务列表容器
        task_list_container = CardWidget(self)
        task_list_layout = QHBoxLayout()
        task_list_layout.setContentsMargins(0, 0, 0, 0)
        task_list_layout.setSpacing(0)
        self.task_list_component = TaskListComponent(self)
        self.task_list_component.task_selected.connect(self.on_task_selected)
        self.task_list_component.task_deleted.connect(self.on_task_deleted)
        task_list_layout.addWidget(self.task_list_component)
        task_list_container.setLayout(task_list_layout)
        right_layout.addWidget(task_list_container, 1)  # 占满剩余空间
        
        # 操作按钮容器
        button_container = CardWidget(self)
        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(16, 16, 16, 16)
        button_layout.setSpacing(8)
        
        self.file_button = PushButton(tr['SubtitleExtractorGUI']['Open'], self)
        self.file_button.setIcon(FluentIcon.FOLDER)
        self.file_button.clicked.connect(self.open_file)
        button_layout.addWidget(self.file_button)
        
        self.run_button = PushButton(tr['SubtitleExtractorGUI']['Run'], self)
        self.run_button.setIcon(FluentIcon.PLAY)
        self.run_button.clicked.connect(self.run_button_clicked)
        button_layout.addWidget(self.run_button)
        
        self.stop_button = PushButton(tr['SubtitleExtractorGUI']['Stop'], self)
        self.stop_button.setIcon(MyFluentIcon.Stop)
        self.stop_button.setVisible(False)
        self.stop_button.clicked.connect(self.stop_button_clicked)
        
        button_layout.addWidget(self.stop_button)
        
        button_container.setLayout(button_layout)
        right_layout.addWidget(button_container)

        main_layout.addLayout(right_layout, 1)
    
    def on_scroll_change(self, value):
        """监控滚动条位置变化"""
        scrollbar = self.output_text.verticalScrollBar()
        # 如果滚动到底部，启用自动滚动
        if value == scrollbar.maximum():
            self.auto_scroll = True
        # 如果用户向上滚动，禁用自动滚动
        elif self.auto_scroll and value < scrollbar.maximum():
            self.auto_scroll = False

    
    def slider_changed(self, value):
        self.video_display_component._sync_section_mask()
        frame = None
        with self._video_cap_lock:
            if self.video_cap is not None and self.video_cap.isOpened():
                frame_no = self.video_slider.value()
                self.video_cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
                ret, frame = self.video_cap.read()
                if not ret:
                    frame = None
        if frame is not None:
            self.update_preview(frame)

    def ab_sections_changed(self, ab_sections):
        if hasattr(self, 'keyframe_timeline'):
            self.keyframe_timeline.set_sections(ab_sections)
        if not hasattr(self, 'task_list_component'):
            return
        get_current_task_index = self.task_list_component.get_current_task_index()
        if get_current_task_index == -1:
            return
        self.task_list_component.update_task_option(get_current_task_index, TaskOptions.AB_SECTIONS, ab_sections)

    def selections_changed(self, selections):
        if not hasattr(self, 'task_list_component'):
            return
        get_current_task_index = self.task_list_component.get_current_task_index()
        if get_current_task_index == -1:
            return
        self.task_list_component.update_task_option(get_current_task_index, TaskOptions.SUB_AREAS, selections)

    def _update_mask_controls_visibility(self):
        is_propainter = config.inpaintMode.value == InpaintMode.PROPAINTER
        self.mask_controls_container.setVisible(is_propainter)
        self.video_display_component.set_propainter_mode(is_propainter)
        if hasattr(self, 'keyframe_container'):
            self.keyframe_container.setVisible(is_propainter)
        if not is_propainter:
            self.paint_mask_toggle.setChecked(False)
            self.video_display_component.set_paint_mask_mode(False)
            self.mask_progress_bar.setVisible(False)

    def _on_paint_mask_toggled(self, checked):
        self.video_display_component.set_paint_mask_mode(checked)

    def _on_brush_size_changed(self, value):
        self.video_display_component.set_brush_size(value)
        self.brush_size_label.setText(f"Brush: {value}")

    def _on_clear_mask(self):
        self.video_display_component.clear_mask()

    def _on_auto_segment(self):
        if not self.video_path or not self.video_cap or self.frame_count <= 0:
            return
        self.auto_segment_btn.setEnabled(False)
        self.auto_segment_btn.setText('Scanning...')
        threading.Thread(target=self._auto_segment_worker, daemon=True).start()

    def _auto_segment_worker(self):
        try:
            sample_interval = max(1, self.fps)
            prev_hist = None
            boundaries = []
            with self._video_cap_lock:
                cap = self.video_cap
                if cap is None:
                    return
                total = self.frame_count
                for fn in range(0, total, int(sample_interval)):
                    if fn >= total:
                        break
                    cap.set(cv2.CAP_PROP_POS_FRAMES, fn)
                    ret, frame = cap.read()
                    if not ret:
                        break
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    hist = cv2.calcHist([gray], [0], None, [64], [0, 256])
                    hist = cv2.normalize(hist, hist).flatten()
                    if prev_hist is not None:
                        diff = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CHISQR)
                        if diff > 0.5:
                            boundaries.append(fn)
                    prev_hist = hist
            sections = []
            if boundaries:
                for i in range(len(boundaries) - 1):
                    sections.append(range(boundaries[i], boundaries[i + 1]))
            if sections:
                self.video_display_component.set_ab_sections(sections)
                self.append_log_signal.emit([f'Auto segment: {len(sections)} sections created'])
            else:
                self.append_log_signal.emit(['Auto segment: no scene changes detected'])
        except Exception as e:
            self.append_log_signal.emit([f'Auto segment error: {e}'])
        finally:
            self.auto_segment_btn.setEnabled(True)
            self.auto_segment_btn.setText('Auto Segment')

    def _ab_sections_file(self):
        if not self.video_path:
            return None
        return os.path.splitext(self.video_path)[0] + '_ab_sections.json'

    def _save_ab_sections(self, ab_sections):
        filepath = self._ab_sections_file()
        if not filepath:
            return
        try:
            data = {'sections': [[s.start, s.stop - 1] for s in ab_sections]}
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass

    def _load_ab_sections(self):
        filepath = self._ab_sections_file()
        if not filepath or not os.path.exists(filepath):
            return
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            sections = [range(s[0], s[1] + 1) for s in data.get('sections', [])]
            if sections:
                self.video_display_component.set_ab_sections(sections)
        except Exception:
            pass

    def _on_timeline_seek(self, frame):
        self.video_slider.setValue(frame)

    def _on_slider_for_timeline(self, frame):
        if hasattr(self, 'keyframe_timeline'):
            self.keyframe_timeline.set_current_frame(frame)

    def _presets_file(self):
        if not self.video_path:
            return None
        return os.path.splitext(self.video_path)[0] + '_presets.json'

    def _load_presets(self):
        filepath = self._presets_file()
        if not filepath or not os.path.exists(filepath):
            return {}
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_presets(self, presets):
        filepath = self._presets_file()
        if not filepath:
            return
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(presets, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _refresh_preset_combo(self):
        self.preset_combo.clear()
        presets = self._load_presets()
        if presets:
            self.preset_combo.addItems(sorted(presets.keys()))
            self.preset_combo.setCurrentIndex(0)

    def _on_preset_save(self):
        name, ok = QtWidgets.QInputDialog.getText(self, 'Save Preset', 'Preset name:')
        if not ok or not name.strip():
            return
        name = name.strip()
        sections = self.video_display_component.get_ab_sections()
        data = [[s.start, s.stop - 1] for s in sections]
        presets = self._load_presets()
        presets[name] = data
        self._save_presets(presets)
        self._refresh_preset_combo()
        self.preset_combo.setCurrentText(name)

    def _on_preset_load(self):
        name = self.preset_combo.currentText()
        if not name:
            return
        presets = self._load_presets()
        if name not in presets:
            return
        sections = [range(s[0], s[1] + 1) for s in presets[name]]
        self.video_display_component.set_ab_sections(sections)

    def _on_preset_delete(self):
        name = self.preset_combo.currentText()
        if not name:
            return
        presets = self._load_presets()
        if name in presets:
            del presets[name]
            self._save_presets(presets)
            self._refresh_preset_combo()

    def on_task_selected(self, index, file_path):
        try:
            mask_data = self.task_list_component.get_task_option(index, TaskOptions.MASK_DATA, None)
            if mask_data is not None:
                self.video_display_component.mask_data = mask_data
                self.video_display_component.update_preview_with_rect()
        except Exception:
            pass
        """处理任务被选中事件
        
        Args:
            index: 任务索引
            file_path: 文件路径
        """
        # 加载选中的视频进行预览
        self.load_video(file_path)
        ab_sections = self.task_list_component.get_task_option(index, TaskOptions.AB_SECTIONS, [])
        self.video_display_component.set_ab_sections(ab_sections)
        selections = self.task_list_component.get_task_option(index, TaskOptions.SUB_AREAS, [])
        if len(selections) <= 0:
            self.video_display_component.load_selections_from_config()
        else:
            self.video_display_component.set_selection_rects(selections)
    
    def on_task_deleted(self, index):
        """处理任务被删除事件
        
        Args:
            index: 任务索引
        """
        # 如果删除的是正在处理的任务，则需要更新状态
        if index == self.current_processing_task_index:
            self.current_processing_task_index = -1
        
        task = self.task_list_component.get_task(0)
        if task:
            # 如果还有任务，选中第一个
            self.task_list_component.select_task(0)

    def update_preview(self, frame):
        # 先缩放图像
        resized_frame = self._img_resize(frame)

        # 设置视频参数
        self.video_display_component.set_video_parameters(
            self.frame_width, self.frame_height, 
            self.scaled_width if hasattr(self, 'scaled_width') else None,
            self.scaled_height if hasattr(self, 'scaled_height') else None,
            self.border_left if hasattr(self, 'border_left') else 0,
            self.border_top if hasattr(self, 'border_top') else 0,
            self.fps if self.fps is not None else 30,
        )
        
        # 更新视频显示（这会同时保存current_pixmap）
        self.video_display_component.update_video_display(resized_frame)

    def _img_resize(self, image):
        height, width = image.shape[:2]
        
        video_preview_width = self.video_display_component.video_preview_width
        video_preview_height = self.video_display_component.video_preview_height
        # 计算等比缩放后的尺寸
        target_ratio = video_preview_width / video_preview_height
        image_ratio = width / height
        
        if image_ratio > target_ratio:
            # 宽度适配，高度按比例缩放
            new_width = video_preview_width
            new_height = int(new_width / image_ratio)
            top_border = (video_preview_height - new_height) // 2
            bottom_border = video_preview_height - new_height - top_border
            left_border = 0
            right_border = 0
        else:
            # 高度适配，宽度按比例缩放
            new_height = video_preview_height
            new_width = int(new_height * image_ratio)
            left_border = (video_preview_width - new_width) // 2
            right_border = video_preview_width - new_width - left_border
            top_border = 0
            bottom_border = 0
        
        # 先缩放图像
        resized = cv2.resize(image, (new_width, new_height))
        
        # 添加黑边以填充到目标尺寸
        padded = cv2.copyMakeBorder(
            resized, 
            top_border, bottom_border, 
            left_border, right_border, 
            cv2.BORDER_CONSTANT, 
            value=[0, 0, 0]
        )
        
        # 保存边框信息，用于坐标转换
        self.border_left = left_border / video_preview_width
        self.border_right = right_border / video_preview_width
        self.border_top = top_border / video_preview_height
        self.border_bottom = bottom_border / video_preview_height
        self.original_width = width
        self.original_height = height
        self.is_vertical = width < height
        self.scaled_width = new_width / video_preview_width
        self.scaled_height = new_height / video_preview_height
        
        return padded

    def stop_button_clicked(self):
        try:
            self._stop_event.set()
            running_process = self.running_process
            if running_process:
                ProcessManager.instance().terminate_by_process(running_process)
            # 更新任务状态为待处理
            if self.current_processing_task_index >= 0:
                self.task_list_component.update_task_status(self.current_processing_task_index, TaskStatus.PENDING)
        finally:
            self.running_process = None
            self.run_button.setVisible(True)
            self.stop_button.setVisible(False)

    @Slot(bool)
    def _toggle_buttons(self, show_run):
        """线程安全地切换按钮可见性"""
        self.run_button.setVisible(show_run)
        self.stop_button.setVisible(not show_run)

    def run_button_clicked(self):
        if not self.task_list_component.get_pending_tasks():
            self.append_output(tr['SubtitleExtractorGUI']['OpenVideoFirst'])
            return

        try:
            # 获取所有待执行的任务
            pending_tasks = self.task_list_component.get_pending_tasks()
            if not pending_tasks:
                return

            self._stop_event.clear()
            self.toggle_buttons_signal.emit(False)
            # 开启后台线程处理视频
            def task():
                try:
                    while not self._stop_event.is_set():
                        try:
                            pending_tasks = self.task_list_component.get_pending_tasks()
                            if not pending_tasks:
                                break
                            pending_task = pending_tasks[0]
                            # 更新当前处理的任务索引
                            self.current_processing_task_index, task_item = pending_task
                            if not self.load_video(task_item.path):
                                self.append_log_signal.emit([tr['SubtitleExtractorGUI']['OpenVideoFailed'].format(task_item.path)])
                                self.task_status_signal.emit(self.current_processing_task_index, TaskStatus.FAILED)
                                continue

                            # 获取字幕区域坐标，未选择则使用全屏
                            subtitle_areas = self.task_list_component.get_task_option(self.current_processing_task_index, TaskOptions.SUB_AREAS, [])
                            if not subtitle_areas or len(subtitle_areas) <= 0:
                                subtitle_areas = [(0, self.frame_height, 0, self.frame_width)]
                                self.task_list_component.update_task_option(self.current_processing_task_index, TaskOptions.SUB_AREAS, subtitle_areas)

                            self.video_display_component.save_selections_to_config()

                            # 更新任务状态为运行中
                            self.task_list_component.update_task_progress(self.current_processing_task_index, 1)

                            # 选中当前任务
                            self.select_task_signal.emit(self.current_processing_task_index)

                            with self._video_cap_lock:
                                if self.video_cap:
                                    self.video_cap.release()
                                    self.video_cap = None

                            self.task_status_signal.emit(self.current_processing_task_index, TaskStatus.PROCESSING)
                            options = {}
                            for key in task_item.options:
                                value = task_item.options[key]
                                if key == TaskOptions.SUB_AREAS.value:
                                    value = self.video_display_component.preview_coordinates_to_video_coordinates(value)
                                options[key] = value
                            if self.paint_mask_toggle.isChecked() and self.video_display_component.mask_data is not None and np.any(self.video_display_component.mask_data):
                                section_masks = self.video_display_component.get_section_masks()
                                if section_masks:
                                    options[TaskOptions.SECTION_MASKS.value] = section_masks
                                else:
                                    options[TaskOptions.MASK_DATA.value] = self.video_display_component.mask_data.copy()
                                self._is_manual_mask_processing = True
                                self.mask_progress_bar.setValue(0)
                                self.mask_progress_bar.setVisible(True)
                            task_item.output_path = None
                            output_path = task_item.output_path
                            process = self.run_subtitle_remover_process(task_item.path, output_path, options)

                            # 检查是否在处理过程中被停止
                            if self._stop_event.is_set():
                                if self._is_manual_mask_processing:
                                    self._is_manual_mask_processing = False
                                    self.mask_progress_bar.setVisible(False)
                                break

                            # 更新任务状态为已完成
                            task_obj = self.task_list_component.get_task(self.current_processing_task_index)
                            if process.exitcode == 0 and task_obj and task_obj.status == TaskStatus.PROCESSING:
                                self.progress_signal.emit(100, True)
                                # 任务完成, 更新输出路径为只读
                                task_obj.output_path = output_path
                                self.task_status_signal.emit(self.current_processing_task_index, TaskStatus.COMPLETED)
                            else:
                                self.task_status_signal.emit(self.current_processing_task_index, TaskStatus.FAILED)

                        except Exception as e:
                            print(e)
                            self.append_log_signal.emit([f"Error: {e}"])
                            # 更新任务状态为失败
                            if self.current_processing_task_index >= 0:
                                self.task_status_signal.emit(self.current_processing_task_index, TaskStatus.FAILED)
                            break
                        finally:
                            if self._is_manual_mask_processing:
                                self._is_manual_mask_processing = False
                                self.mask_progress_bar.setVisible(False)
                            with self._video_cap_lock:
                                if self.video_cap:
                                    self.video_cap.release()
                                    self.video_cap = None
                            time.sleep(1)
                finally:
                    self.toggle_buttons_signal.emit(True)

            self._worker_thread = threading.Thread(target=task, daemon=True)
            self._worker_thread.start()
        except Exception as e:
            print(traceback.format_exc())
            self.append_log_signal.emit([f"Error: {e}"])
            self.toggle_buttons_signal.emit(True)

    @staticmethod
    def remover_process(queue, video_path, output_path, options):
        """
        在子进程中执行字幕提取的函数
        
        Args:
            video_path: 视频文件路径
            output_path: 输出文件路径
            options: 选项
        """
        sr = None
        try:
            from backend.main import SubtitleRemover
            sr = SubtitleRemover(video_path, True)
            sr.video_out_path = output_path
            for key in options:
                setattr(sr, key, options[key])
            sr.add_progress_listener(lambda progress, isFinished: SubtitleRemoverRemoteCall.remote_call_update_progress(queue, progress, isFinished))
            sr.append_output = lambda *args: SubtitleRemoverRemoteCall.remote_call_append_log(queue, args)
            sr.manage_process = lambda pid: SubtitleRemoverRemoteCall.remote_call_manage_process(queue, pid)
            sr.update_preview_with_comp = lambda *args: SubtitleRemoverRemoteCall.remote_call_update_preview_with_comp(queue, args)
            sr.run()
        except Exception as e:
            traceback.print_exc()
            SubtitleRemoverRemoteCall.remote_call_catch_error(queue, e)
        finally:
            if sr:
                sr.isFinished = True
                sr.vsf_running = False
            SubtitleRemoverRemoteCall.remote_call_finish(queue)
            

    # 修改run_subtitle_remover_process方法
    def run_subtitle_remover_process(self, video_path, output_path, options):
        """
        使用多进程执行字幕提取，并等待进程完成
        
        Args:
            video_path: 视频文件路径
            output_path: 输出文件路径
            options: 任务选项
        """
        subtitle_remover_remote_caller = SubtitleRemoverRemoteCall()
        subtitle_remover_remote_caller.register_update_progress_callback(self.progress_signal.emit)
        subtitle_remover_remote_caller.register_log_callback(self.append_log_signal.emit)
        subtitle_remover_remote_caller.register_update_preview_with_comp_callback(self.update_preview_with_comp_signal.emit)
        subtitle_remover_remote_caller.register_error_callback(self.task_error_signal.emit)
        process = multiprocessing.Process(
            target=HomeInterface.remover_process,
            args=(subtitle_remover_remote_caller.queue, video_path, output_path, options)
        )
        try:
            if self._stop_event.is_set():
                return process
            process.start()
            ProcessManager.instance().add_process(process)
            self.running_process = process
            process.join()
            print(f"Process exited with code {process.exitcode}")
        finally:
            subtitle_remover_remote_caller.stop()
        return process

    @Slot()
    def processing_finished(self):
        pending_tasks = self.task_list_component.get_pending_tasks()
        if pending_tasks:
            # 还有待执行任务, 忽略
            return
        # 处理完成后恢复界面可用性
        self.run_button.setVisible(True)
        self.stop_button.setVisible(False)
        self.se = None
        # 重置视频滑块
        self.video_slider.setValue(1)
        # 重置当前处理任务索引
        self.current_processing_task_index = -1

    @Slot(int, bool)
    def update_progress(self, progress_total, isFinished):
        try:
            pos = min(self.frame_count - 1, int(progress_total / 100 * self.frame_count))
            if pos != self.video_slider.value():
                self.video_slider.blockSignals(True)
                self.video_slider.setValue(pos)
                self.video_slider.blockSignals(False)
            
            # 更新任务进度
            if self.current_processing_task_index >= 0:
                self.task_list_component.update_task_progress(
                    self.current_processing_task_index, 
                    progress_total,
                )
            if self._is_manual_mask_processing:
                self.mask_progress_bar.setValue(progress_total)
            
            # 检查是否完成
            if isFinished:
                self.processing_finished()
        except Exception as e:
            # 捕获任何异常，防止崩溃
            print(f"更新进度时出错: {str(e)}")

    @Slot(list)
    def append_log(self, log):
        self.append_output(*log)

    def append_output(self, *args):
        """添加文本到输出区域并控制滚动
        Args:
            *args: 要输出的内容，多个参数将用空格连接
        """
        # 将所有参数转换为字符串并用空格连接
        text = ' '.join(str(arg) for arg in args).rstrip()
        timestamp = datetime.now().strftime('%H:%M:%S')
        # 转义HTML特殊字符
        escaped = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        # 根据内容判断消息类型并着色
        if '错误' in text or 'Error' in text or '失败' in text or 'Failed' in text:
            color = '#e74c3c'
        elif '成功' in text or '完成' in text or 'Success' in text or 'Finished' in text:
            color = '#27ae60'
        elif '警告' in text or 'Warning' in text:
            color = '#f39c12'
        else:
            color = '#2980b9'
        html = f'<span style="color:#888;">[{timestamp}]</span> <span style="color:{color};">{escaped}</span><br>'
        self.output_text.append(html)
        print(*args)  # 保持原始的 print 行为
        # 如果启用了自动滚动，则滚动到底部
        if self.auto_scroll:
            scrollbar = self.output_text.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    @Slot(list)
    def update_preview_with_comp(self, args):
        """更新执行时预览"""
        frame_ori, frame_comp = args
        if self.current_processing_task_index >= 0:
            subtitle_areas = self.task_list_component.get_task_option(self.current_processing_task_index, TaskOptions.SUB_AREAS, [])
            if len(subtitle_areas) > 0:
                subtitle_areas = self.video_display_component.preview_coordinates_to_video_coordinates(subtitle_areas)
                if frame_ori is frame_comp:
                    frame_ori = frame_ori.copy()
                for rect in subtitle_areas:
                    ymin, ymax, xmin, xmax = rect
                    cv2.rectangle(frame_ori, (xmin, ymin), (xmax, ymax), (0, 255, 0), 2)
        preview_frame = cv2.hconcat([frame_ori, frame_comp])
        # 先缩放图像
        resized_frame = self._img_resize(preview_frame)
        # 更新视频显示（这会同时保存current_pixmap）
        self.video_display_component.update_video_display(resized_frame, draw_selection=False)
        self.video_display_component.set_dragger_enabled(False)

    @Slot(object)
    def on_task_error(self, e):
        self.append_output(tr['SubtitleExtractorGUI']['ErrorDuringProcessing'].format(str(e)))
        if self.current_processing_task_index >= 0:
            self.task_list_component.update_task_status(self.current_processing_task_index, TaskStatus.FAILED)

    def load_video(self, video_path):
        self.video_path = video_path
        with self._video_cap_lock:
            if self.video_cap:
                self.video_cap.release()
                self.video_cap = None
        # 如果是图片文件，直接走图片加载路径
        if is_image_file(video_path):
            return self.load_as_picture(video_path)
        with self._video_cap_lock:
            self.video_cap = cv2.VideoCapture(get_readable_path(self.video_path))
            if not self.video_cap.isOpened():
                self.video_cap = None
                return self.load_as_picture(video_path)
            ret, frame = self.video_cap.read()
            if not ret:
                self.video_cap.release()
                self.video_cap = None
                return self.load_as_picture(video_path)
            self.frame_count = int(self.video_cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self.frame_height = int(self.video_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            self.frame_width = int(self.video_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            self.fps = self.video_cap.get(cv2.CAP_PROP_FPS)

        self.update_preview(frame)
        self.video_slider.setMaximum(self.frame_count)
        self.video_slider.setValue(1)
        self.video_display_component.set_dragger_enabled(True)
        self._unlock_inpaint_mode()
        QTimer.singleShot(50, self._on_video_loaded_deferred)
        return True

    def _on_video_loaded_deferred(self):
        try:
            if hasattr(self, 'keyframe_timeline'):
                self.keyframe_timeline.set_total_frames(self.frame_count)
                self.keyframe_timeline.set_current_frame(1)
            self._load_ab_sections()
            self._refresh_preset_combo()
        except Exception:
            pass

    def load_as_picture(self, path):
        if not is_image_file(path):
            return False
        self.video_path = path
        self.video_cap = None
        frame = read_image(get_readable_path(path))
        if frame is None:
            return False
        self.frame_count = 1
        self.frame_height = frame.shape[0]
        self.frame_width = frame.shape[1]
        self.fps = 1
        self.update_preview(frame)
        self.video_slider.setMaximum(self.frame_count)
        self.video_slider.setValue(1)
        self.video_display_component.set_dragger_enabled(True)
        # 图片模式锁定为 LAMA
        self._lock_inpaint_mode_to_lama()
        return True

    def _lock_inpaint_mode_to_lama(self):
        """图片模式锁定 inpaint 模式为 LAMA"""
        if self._saved_inpaint_mode is None:
            self._saved_inpaint_mode = config.inpaintMode.value
        config.set(config.inpaintMode, InpaintMode.LAMA)
        self.setting_interface.set_inpaint_mode_enabled(False)

    def _unlock_inpaint_mode(self):
        """视频模式恢复用户原始的 inpaint 模式选择"""
        if self._saved_inpaint_mode is not None:
            config.set(config.inpaintMode, self._saved_inpaint_mode)
            self._saved_inpaint_mode = None
        self.setting_interface.set_inpaint_mode_enabled(True)
        self.video_slider.setValue(1)
        self.video_display_component.set_dragger_enabled(True)
        return True


    def open_file(self):
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            tr['SubtitleExtractorGUI']['Open'],
            "",
            "All Files (*.*);;Video Files (*.mp4 *.flv *.wmv *.avi *.mkv *.mov);;Image Files (*.jpg *.jpeg *.png *.bmp *.webp *.tiff)"
        )
        if files:
            files_loaded = []
            # 倒序打开, 确保第一个视频截图显示在屏幕上
            for path in reversed(files):
                if self.load_video(path):
                    self.append_output(f"{tr['SubtitleExtractorGUI']['OpenVideoSuccess']}: {path}")
                    files_loaded.append(path)
                else:
                    self.append_output(f"{tr['SubtitleExtractorGUI']['OpenVideoFailed']}: {path}")
            # 正序添加, 确保任务列表顺序一致
            for path in reversed(files_loaded):
                # 添加到任务列表
                self.task_list_component.add_task(path)
                index = max(0, self.task_list_component.find_task_index_by_path(path))
                self.task_list_component.select_task(index)

    def closeEvent(self, event):
        """窗口关闭时断开信号连接并清理资源"""
        try:
            # 通知 worker 线程停止
            self._stop_event.set()
            # 终止子进程
            ProcessManager.instance().terminate_all()
            # 等待 worker 线程结束（最多5秒）
            if self._worker_thread and self._worker_thread.is_alive():
                self._worker_thread.join(timeout=5)

            # 断开信号连接
            self.progress_signal.disconnect(self.update_progress)
            self.append_log_signal.disconnect(self.append_log)
            self.update_preview_with_comp_signal.disconnect(self.update_preview_with_comp)
            self.task_error_signal.disconnect(self.on_task_error)
            self.toggle_buttons_signal.disconnect(self._toggle_buttons)
            self.video_display_component.video_slider.valueChanged.disconnect(self.slider_changed)
            self.video_display_component.ab_sections_changed.disconnect(self.ab_sections_changed)
            self.video_display_component.selections_changed.disconnect(self.selections_changed)
            # 释放视频资源
            with self._video_cap_lock:
                if self.video_cap:
                    self.video_cap.release()
                    self.video_cap = None
        except Exception as e:
            print(f"Error during close window:", e)
        super().closeEvent(event)
    