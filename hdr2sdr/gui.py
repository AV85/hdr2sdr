"""PySide6 GUI for hdr2sdr."""
from __future__ import annotations

import os
import subprocess
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QDragEnterEvent, QDropEvent, QImage, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QSlider, QSpinBox, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from . import __version__, caps as capsmod, pipeline, settings as cfg
from .i18n import LANGS, set_language, tr
from .probe import MediaInfo, collect_files, probe
from .settings import (AUDIO_MODES, CODECS, CONTAINERS, ColorSettings, ENC_PRESETS, ENGINES, EXISTS, HWACCEL,
                       NON_HDR, QUALITY_MODES, RESOLUTIONS, Settings, TONEMAP_ALGOS, COLOR_PRESETS)
from .worker import Job, Runner


class Bridge(QObject):
    """Thread -> GUI signal bridge."""
    probed = Signal(str, object)
    progress = Signal(object)
    job_done = Signal(object)
    all_done = Signal()
    caps_ready = Signal(object)
    preview_ready = Signal(bytes, bytes, str)


# ---------------------------------------------------------------- helpers
def _combo_set(combo: QComboBox, value) -> None:
    for i in range(combo.count()):
        if combo.itemData(i) == value:
            combo.setCurrentIndex(i)
            return
    combo.setCurrentIndex(0)


def _fill_combo(combo: QComboBox, items: list[tuple[str, object]]) -> None:
    cur = combo.currentData()
    combo.blockSignals(True)
    combo.clear()
    for label, data in items:
        combo.addItem(label, data)
    combo.blockSignals(False)
    if cur is not None:
        _combo_set(combo, cur)


class SliderRow:
    """Slider + numeric label bound to a float with a scale factor."""

    def __init__(self, grid: QGridLayout, row: int, lo: float, hi: float, default: float,
                 on_change, scale: int = 100):
        self.scale = scale
        self.default = default
        self.label = QLabel()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(int(lo * scale), int(hi * scale))
        self.value_label = QLabel()
        self.value_label.setMinimumWidth(48)
        self.slider.valueChanged.connect(self._changed)
        self.on_change = on_change
        grid.addWidget(self.label, row, 0)
        grid.addWidget(self.slider, row, 1)
        grid.addWidget(self.value_label, row, 2)
        self.set(default)

    def _changed(self, v: int) -> None:
        self.value_label.setText(f"{v / self.scale:.2f}")
        self.on_change()

    def value(self) -> float:
        return self.slider.value() / self.scale

    def set(self, v: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(v * self.scale)))
        self.slider.blockSignals(False)
        self.value_label.setText(f"{v:.2f}")


# ---------------------------------------------------------------- preview
class PreviewDialog(QDialog):
    def __init__(self, parent: "MainWindow", info: MediaInfo):
        super().__init__(parent)
        self.main = parent
        self.info = info
        self.setWindowTitle(tr("preview_title"))
        self.resize(1200, 520)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel(tr("preview_time")))
        self.time = QDoubleSpinBox()
        self.time.setRange(0, max(0.0, info.duration))
        self.time.setDecimals(1)
        self.time.setValue(min(parent.settings.preview_time, max(0.0, info.duration - 0.1)))
        top.addWidget(self.time)
        self.btn = QPushButton(tr("btn_refresh"))
        self.btn.clicked.connect(self.render)
        top.addWidget(self.btn)
        top.addStretch()
        lay.addLayout(top)
        imgs = QHBoxLayout()
        self.before = QLabel(tr("preview_wait"))
        self.after = QLabel(tr("preview_wait"))
        for l in (self.before, self.after):
            l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            l.setMinimumSize(560, 320)
        col1 = QVBoxLayout()
        col1.addWidget(QLabel(f"<b>{tr('preview_before')}</b>"), alignment=Qt.AlignmentFlag.AlignCenter)
        col1.addWidget(self.before)
        col2 = QVBoxLayout()
        col2.addWidget(QLabel(f"<b>{tr('preview_after')}</b>"), alignment=Qt.AlignmentFlag.AlignCenter)
        col2.addWidget(self.after)
        imgs.addLayout(col1)
        imgs.addLayout(col2)
        lay.addLayout(imgs)
        self.status = QLabel("")
        lay.addWidget(self.status)
        parent.bridge.preview_ready.connect(self._on_ready, Qt.ConnectionType.QueuedConnection)
        self.render()

    def render(self) -> None:
        s = self.main.collect_settings()
        self.main.settings.preview_time = self.time.value()
        t = self.time.value()
        self.btn.setEnabled(False)
        self.status.setText(tr("preview_wait"))
        caps = self.main.caps
        info = self.info

        def work():
            try:
                c1 = pipeline.preview_command(info, s, caps, t, raw=True, width=560)
                c2 = pipeline.preview_command(info, s, caps, t, raw=False, width=560)
                p1 = subprocess.run(c1, capture_output=True, timeout=120)
                p2 = subprocess.run(c2, capture_output=True, timeout=120)
                err = ""
                if p1.returncode != 0:
                    err += p1.stderr.decode(errors="replace")
                if p2.returncode != 0:
                    err += p2.stderr.decode(errors="replace")
                self.main.bridge.preview_ready.emit(p1.stdout, p2.stdout, err)
            except Exception as e:  # noqa: BLE001
                self.main.bridge.preview_ready.emit(b"", b"", str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_ready(self, b1: bytes, b2: bytes, err: str) -> None:
        self.btn.setEnabled(True)
        for data, label in ((b1, self.before), (b2, self.after)):
            img = QImage.fromData(data, "PNG")
            if img.isNull():
                label.setText("—")
            else:
                label.setPixmap(QPixmap.fromImage(img).scaled(
                    label.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.status.setText(tr("preview_error", err=err.strip()[:500]) if err else "")


# ---------------------------------------------------------------- main window
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings: Settings = cfg.load()
        set_language(self.settings.language)
        self.caps: capsmod.Caps = capsmod.detect(self.settings.ffmpeg_path, quick=True)
        self.user_presets: dict[str, ColorSettings] = cfg.load_user_presets()
        self.infos: dict[str, MediaInfo] = {}
        self.jobs: list[Job] = []
        self.bridge = Bridge()
        self.bridge.probed.connect(self._on_probed, Qt.ConnectionType.QueuedConnection)
        self.bridge.progress.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)
        self.bridge.job_done.connect(self._on_job_done, Qt.ConnectionType.QueuedConnection)
        self.bridge.all_done.connect(self._on_all_done, Qt.ConnectionType.QueuedConnection)
        self.bridge.caps_ready.connect(self._on_caps, Qt.ConnectionType.QueuedConnection)
        self.runner = Runner(
            on_progress=lambda j: self.bridge.progress.emit(j),
            on_job_done=lambda j: self.bridge.job_done.emit(j),
            on_all_done=lambda: self.bridge.all_done.emit(),
        )
        self._loading = True
        self._build_ui()
        self._build_menu()
        self.apply_settings(self.settings)
        self.retranslate()
        self._loading = False
        self.resize(1100, 820)
        self.setAcceptDrops(True)
        self._detect_caps_async()

    # ---------------------------------------------------------- UI build
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # files
        self.grp_files = QGroupBox()
        fl = QVBoxLayout(self.grp_files)
        self.table = QTableWidget(0, 4)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(3, 160)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(180)
        fl.addWidget(self.table)
        self.drop_hint = QLabel()
        self.drop_hint.setStyleSheet("color: gray;")
        fl.addWidget(self.drop_hint)
        row = QHBoxLayout()
        self.btn_add_files = QPushButton()
        self.btn_add_folder = QPushButton()
        self.btn_remove = QPushButton()
        self.btn_clear = QPushButton()
        self.chk_recursive = QCheckBox()
        for b in (self.btn_add_files, self.btn_add_folder, self.btn_remove, self.btn_clear):
            row.addWidget(b)
        row.addWidget(self.chk_recursive)
        row.addStretch()
        fl.addLayout(row)
        self.btn_add_files.clicked.connect(self.add_files_dialog)
        self.btn_add_folder.clicked.connect(self.add_folder_dialog)
        self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_clear.clicked.connect(self.clear_files)
        root.addWidget(self.grp_files, 3)

        # output
        self.grp_output = QGroupBox()
        og = QGridLayout(self.grp_output)
        self.lbl_out_dir = QLabel()
        self.out_dir = QLineEdit()
        self.btn_browse = QPushButton()
        self.btn_browse.clicked.connect(self.browse_out_dir)
        og.addWidget(self.lbl_out_dir, 0, 0)
        og.addWidget(self.out_dir, 0, 1, 1, 4)
        og.addWidget(self.btn_browse, 0, 5)
        self.lbl_suffix = QLabel()
        self.suffix = QLineEdit()
        self.suffix.setMaximumWidth(100)
        self.lbl_container = QLabel()
        self.container = QComboBox()
        self.lbl_non_hdr = QLabel()
        self.non_hdr = QComboBox()
        self.lbl_exists = QLabel()
        self.exists = QComboBox()
        og.addWidget(self.lbl_suffix, 1, 0)
        og.addWidget(self.suffix, 1, 1)
        og.addWidget(self.lbl_container, 1, 2)
        og.addWidget(self.container, 1, 3)
        og.addWidget(self.lbl_non_hdr, 2, 0)
        og.addWidget(self.non_hdr, 2, 1)
        og.addWidget(self.lbl_exists, 2, 2)
        og.addWidget(self.exists, 2, 3)
        root.addWidget(self.grp_output)

        # tabs
        self.tabs = QTabWidget()
        self.tab_color = QWidget()
        self.tab_quality = QWidget()
        self.tab_adv = QWidget()
        self.tabs.addTab(self.tab_color, "")
        self.tabs.addTab(self.tab_quality, "")
        self.tabs.addTab(self.tab_adv, "")
        self._build_color_tab()
        self._build_quality_tab()
        self._build_adv_tab()
        root.addWidget(self.tabs, 2)

        # bottom
        bottom = QHBoxLayout()
        self.btn_preview = QPushButton()
        self.btn_preview.clicked.connect(self.open_preview)
        self.btn_start = QPushButton()
        self.btn_start.setDefault(True)
        self.btn_start.clicked.connect(self.start)
        self.btn_cancel = QPushButton()
        self.btn_cancel.clicked.connect(self.cancel)
        self.btn_cancel.setEnabled(False)
        bottom.addWidget(self.btn_preview)
        bottom.addStretch()
        bottom.addWidget(self.btn_start)
        bottom.addWidget(self.btn_cancel)
        root.addLayout(bottom)
        prow = QHBoxLayout()
        self.lbl_total = QLabel()
        self.total_bar = QProgressBar()
        self.total_bar.setRange(0, 100)
        prow.addWidget(self.lbl_total)
        prow.addWidget(self.total_bar)
        root.addLayout(prow)
        self.status = QLabel()
        root.addWidget(self.status)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setMinimumHeight(120)
        root.addWidget(self.log, 1)
        self.table.itemSelectionChanged.connect(self._selection_changed)

    def _build_color_tab(self) -> None:
        lay = QVBoxLayout(self.tab_color)
        form = QFormLayout()
        self.lbl_preset = QLabel()
        self.preset = QComboBox()
        self.preset.currentIndexChanged.connect(self._preset_chosen)
        form.addRow(self.lbl_preset, self.preset)
        self.lbl_tonemap = QLabel()
        self.tonemap = QComboBox()
        form.addRow(self.lbl_tonemap, self.tonemap)
        self.lbl_engine = QLabel()
        self.engine = QComboBox()
        form.addRow(self.lbl_engine, self.engine)
        self.lbl_peak = QLabel()
        self.peak = QDoubleSpinBox()
        self.peak.setRange(0, 10000)
        self.peak.setDecimals(0)
        self.peak.setSingleStep(100)
        form.addRow(self.lbl_peak, self.peak)
        lay.addLayout(form)
        grid = QGridLayout()
        self.sl_sat = SliderRow(grid, 0, 0.0, 2.5, 1.0, self._slider_changed)
        self.sl_con = SliderRow(grid, 1, 0.5, 1.8, 1.0, self._slider_changed)
        self.sl_bri = SliderRow(grid, 2, -0.3, 0.3, 0.0, self._slider_changed)
        self.sl_gam = SliderRow(grid, 3, 0.5, 1.8, 1.0, self._slider_changed)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        row = QHBoxLayout()
        self.btn_reset_color = QPushButton()
        self.btn_reset_color.clicked.connect(lambda: self._apply_color(ColorSettings(), keep_preset="natural"))
        row.addWidget(self.btn_reset_color)
        row.addStretch()
        lay.addLayout(row)
        self.hint_color = QLabel()
        self.hint_color.setWordWrap(True)
        self.hint_color.setStyleSheet("color: gray;")
        lay.addWidget(self.hint_color)
        lay.addStretch()

    def _build_quality_tab(self) -> None:
        form = QFormLayout(self.tab_quality)
        self.lbl_quality = QLabel()
        self.quality = QComboBox()
        self.quality.currentIndexChanged.connect(self._quality_changed)
        form.addRow(self.lbl_quality, self.quality)
        self.lbl_crf = QLabel()
        self.crf = QSpinBox()
        self.crf.setRange(0, 51)
        form.addRow(self.lbl_crf, self.crf)
        self.lbl_bitrate = QLabel()
        self.bitrate = QSpinBox()
        self.bitrate.setRange(500, 400000)
        self.bitrate.setSingleStep(1000)
        form.addRow(self.lbl_bitrate, self.bitrate)
        self.lbl_codec = QLabel()
        self.codec = QComboBox()
        form.addRow(self.lbl_codec, self.codec)
        self.lbl_enc_preset = QLabel()
        self.enc_preset = QComboBox()
        for p in ENC_PRESETS:
            self.enc_preset.addItem(p, p)
        form.addRow(self.lbl_enc_preset, self.enc_preset)
        self.lbl_hwaccel = QLabel()
        self.hwaccel = QComboBox()
        form.addRow(self.lbl_hwaccel, self.hwaccel)
        self.lbl_resolution = QLabel()
        self.resolution = QComboBox()
        form.addRow(self.lbl_resolution, self.resolution)
        self.lbl_fps = QLabel()
        self.fps = QDoubleSpinBox()
        self.fps.setRange(0, 240)
        self.fps.setDecimals(3)
        form.addRow(self.lbl_fps, self.fps)
        self.lbl_audio = QLabel()
        self.audio = QComboBox()
        form.addRow(self.lbl_audio, self.audio)
        self.lbl_audio_kbps = QLabel()
        self.audio_kbps = QSpinBox()
        self.audio_kbps.setRange(64, 512)
        self.audio_kbps.setSingleStep(32)
        form.addRow(self.lbl_audio_kbps, self.audio_kbps)

    def _build_adv_tab(self) -> None:
        form = QFormLayout(self.tab_adv)
        self.lbl_parallel = QLabel()
        self.parallel = QSpinBox()
        self.parallel.setRange(1, 8)
        form.addRow(self.lbl_parallel, self.parallel)
        self.lbl_preview_time = QLabel()
        self.preview_time = QDoubleSpinBox()
        self.preview_time.setRange(0, 100000)
        self.preview_time.setDecimals(1)
        form.addRow(self.lbl_preview_time, self.preview_time)
        self.lbl_ffmpeg_path = QLabel()
        row = QHBoxLayout()
        self.ffmpeg_path = QLineEdit()
        self.btn_redetect = QPushButton()
        self.btn_redetect.clicked.connect(self._detect_caps_async)
        row.addWidget(self.ffmpeg_path)
        row.addWidget(self.btn_redetect)
        form.addRow(self.lbl_ffmpeg_path, row)
        self.chk_keep_meta = QCheckBox()
        form.addRow(self.chk_keep_meta)
        self.chk_show_log = QCheckBox()
        self.chk_show_log.toggled.connect(lambda v: self.log.setVisible(v))
        form.addRow(self.chk_show_log)
        self.btn_show_cmd = QPushButton()
        self.btn_show_cmd.clicked.connect(self.show_command)
        form.addRow(self.btn_show_cmd)
        self.caps_label = QLabel()
        self.caps_label.setWordWrap(True)
        self.caps_label.setStyleSheet("color: gray;")
        form.addRow(self.caps_label)

    def _build_menu(self) -> None:
        mb = self.menuBar()
        self.menu_lang = mb.addMenu("")
        grp = QActionGroup(self)
        self.lang_actions = {}
        for code, name in LANGS.items():
            a = QAction(name, self, checkable=True)
            a.setData(code)
            a.triggered.connect(lambda _c=False, code=code: self.change_language(code))
            grp.addAction(a)
            self.menu_lang.addAction(a)
            self.lang_actions[code] = a
        self.menu_presets = mb.addMenu("")
        self.act_save_preset = QAction(self)
        self.act_save_preset.triggered.connect(self.save_preset)
        self.act_del_preset = QAction(self)
        self.act_del_preset.triggered.connect(self.delete_preset)
        self.act_reset = QAction(self)
        self.act_reset.triggered.connect(self.reset_settings)
        self.menu_presets.addAction(self.act_save_preset)
        self.menu_presets.addAction(self.act_del_preset)
        self.menu_presets.addSeparator()
        self.menu_presets.addAction(self.act_reset)
        self.menu_help = mb.addMenu("")
        self.act_caps = QAction(self)
        self.act_caps.triggered.connect(self.show_caps)
        self.act_about = QAction(self)
        self.act_about.triggered.connect(self.show_about)
        self.menu_help.addAction(self.act_caps)
        self.menu_help.addAction(self.act_about)

    # ---------------------------------------------------------- i18n
    def retranslate(self) -> None:
        self.setWindowTitle(tr("app_title"))
        self.menu_lang.setTitle(tr("menu_language"))
        self.menu_presets.setTitle(tr("menu_presets"))
        self.menu_help.setTitle(tr("menu_help"))
        self.act_save_preset.setText(tr("menu_save_preset"))
        self.act_del_preset.setText(tr("menu_delete_preset"))
        self.act_reset.setText(tr("menu_reset"))
        self.act_caps.setText(tr("menu_caps"))
        self.act_about.setText(tr("menu_about"))
        self.grp_files.setTitle(tr("grp_files"))
        self.table.setHorizontalHeaderLabels([tr("col_file"), tr("col_info"), tr("col_status"), tr("col_progress")])
        self.drop_hint.setText(tr("drop_hint"))
        self.btn_add_files.setText(tr("btn_add_files"))
        self.btn_add_folder.setText(tr("btn_add_folder"))
        self.btn_remove.setText(tr("btn_remove"))
        self.btn_clear.setText(tr("btn_clear"))
        self.chk_recursive.setText(tr("chk_recursive"))
        self.grp_output.setTitle(tr("grp_output"))
        self.lbl_out_dir.setText(tr("lbl_out_dir"))
        self.out_dir.setPlaceholderText(tr("out_dir_placeholder"))
        self.btn_browse.setText(tr("btn_browse"))
        self.lbl_suffix.setText(tr("lbl_suffix"))
        self.lbl_container.setText(tr("lbl_container"))
        self.lbl_non_hdr.setText(tr("lbl_non_hdr"))
        self.lbl_exists.setText(tr("lbl_exists"))
        _fill_combo(self.container, [(c.upper(), c) for c in CONTAINERS])
        _fill_combo(self.non_hdr, [(tr(f"non_hdr_{v}"), v) for v in NON_HDR])
        _fill_combo(self.exists, [(tr(f"exists_{v}"), v) for v in EXISTS])
        self.tabs.setTabText(0, tr("tab_color"))
        self.tabs.setTabText(1, tr("tab_quality"))
        self.tabs.setTabText(2, tr("tab_advanced"))
        # color
        self.lbl_preset.setText(tr("lbl_preset"))
        self._fill_presets()
        self.lbl_tonemap.setText(tr("lbl_tonemap"))
        _fill_combo(self.tonemap, [(tr("tonemap_auto") if a == "auto" else a, a) for a in TONEMAP_ALGOS])
        self.lbl_engine.setText(tr("lbl_engine"))
        _fill_combo(self.engine, [(tr(f"engine_{e}"), e) for e in ENGINES])
        self.lbl_peak.setText(tr("lbl_peak"))
        self.peak.setSpecialValueText(tr("peak_auto"))
        self.sl_sat.label.setText(tr("lbl_saturation"))
        self.sl_con.label.setText(tr("lbl_contrast"))
        self.sl_bri.label.setText(tr("lbl_brightness"))
        self.sl_gam.label.setText(tr("lbl_gamma"))
        self.btn_reset_color.setText(tr("btn_reset_color"))
        self.hint_color.setText(tr("hint_color"))
        # quality
        self.lbl_quality.setText(tr("lbl_quality"))
        _fill_combo(self.quality, [(tr(f"q_{q}"), q) for q in QUALITY_MODES])
        self.lbl_crf.setText(tr("lbl_crf"))
        self.lbl_bitrate.setText(tr("lbl_bitrate"))
        self.lbl_codec.setText(tr("lbl_codec"))
        _fill_combo(self.codec, [(tr(f"codec_{c}"), c) for c in CODECS])
        self.lbl_enc_preset.setText(tr("lbl_enc_preset"))
        self.lbl_hwaccel.setText(tr("lbl_hwaccel"))
        _fill_combo(self.hwaccel, [(tr(f"hw_{h}"), h) for h in HWACCEL])
        self._update_hw_availability()
        self.lbl_resolution.setText(tr("lbl_resolution"))
        _fill_combo(self.resolution, [(tr("res_keep") if r == "keep" else f"{r}p", r) for r in RESOLUTIONS])
        self.lbl_fps.setText(tr("lbl_fps"))
        self.fps.setSpecialValueText(tr("fps_keep"))
        self.lbl_audio.setText(tr("lbl_audio"))
        _fill_combo(self.audio, [(tr(f"audio_{a}"), a) for a in AUDIO_MODES])
        self.lbl_audio_kbps.setText(tr("lbl_audio_kbps"))
        # advanced
        self.lbl_parallel.setText(tr("lbl_parallel"))
        self.lbl_preview_time.setText(tr("lbl_preview_time"))
        self.lbl_ffmpeg_path.setText(tr("lbl_ffmpeg_path"))
        self.ffmpeg_path.setPlaceholderText(tr("ffmpeg_auto"))
        self.btn_redetect.setText(tr("btn_redetect"))
        self.chk_keep_meta.setText(tr("chk_keep_meta"))
        self.chk_show_log.setText(tr("chk_show_log"))
        self.btn_show_cmd.setText(tr("btn_show_cmd"))
        self.caps_label.setText(self.caps.summary())
        # bottom
        self.btn_preview.setText(tr("btn_preview"))
        self.btn_start.setText(tr("btn_start"))
        self.btn_cancel.setText(tr("btn_cancel"))
        self.lbl_total.setText(tr("lbl_total"))
        if not self.runner.running:
            self.status.setText(tr("status_ready"))
        for j in self.jobs:
            self._update_row(j)
        for i in range(self.table.rowCount()):
            self._refresh_info_cell(i)

    def _fill_presets(self) -> None:
        items = [(tr(f"preset_{k}"), k) for k in COLOR_PRESETS] + \
                [(name, "user:" + name) for name in sorted(self.user_presets)] + \
                [(tr("preset_custom"), "custom")]
        _fill_combo(self.preset, items)

    def change_language(self, code: str) -> None:
        set_language(code)
        self.settings.language = code
        self.lang_actions[code].setChecked(True)
        self.retranslate()

    # ---------------------------------------------------------- settings <-> widgets
    def apply_settings(self, s: Settings) -> None:
        self._loading = True
        self.lang_actions.get(s.language, self.lang_actions["en"]).setChecked(True)
        self.out_dir.setText(s.output_dir)
        self.suffix.setText(s.suffix)
        _fill_combo(self.container, [(c.upper(), c) for c in CONTAINERS])
        _combo_set(self.container, s.container)
        self.chk_recursive.setChecked(s.recursive)
        _fill_combo(self.non_hdr, [(v, v) for v in NON_HDR])
        _combo_set(self.non_hdr, s.non_hdr)
        _fill_combo(self.exists, [(v, v) for v in EXISTS])
        _combo_set(self.exists, s.exists)
        self._fill_presets()
        _fill_combo(self.tonemap, [(a, a) for a in TONEMAP_ALGOS])
        _fill_combo(self.engine, [(e, e) for e in ENGINES])
        self._apply_color(s.color, keep_preset=s.color_preset)
        _fill_combo(self.quality, [(q, q) for q in QUALITY_MODES])
        _combo_set(self.quality, s.quality_mode)
        self.crf.setValue(s.crf)
        self.bitrate.setValue(s.bitrate_kbps)
        _fill_combo(self.codec, [(c, c) for c in CODECS])
        _combo_set(self.codec, s.codec)
        _combo_set(self.enc_preset, s.enc_preset)
        _fill_combo(self.hwaccel, [(h, h) for h in HWACCEL])
        _combo_set(self.hwaccel, s.hwaccel)
        _fill_combo(self.resolution, [(r, r) for r in RESOLUTIONS])
        _combo_set(self.resolution, s.resolution)
        self.fps.setValue(s.fps)
        _fill_combo(self.audio, [(a, a) for a in AUDIO_MODES])
        _combo_set(self.audio, s.audio)
        self.audio_kbps.setValue(s.audio_kbps)
        self.parallel.setValue(s.parallel)
        self.preview_time.setValue(s.preview_time)
        self.ffmpeg_path.setText(s.ffmpeg_path)
        self.chk_keep_meta.setChecked(s.keep_metadata)
        self.chk_show_log.setChecked(s.show_log)
        self.log.setVisible(s.show_log)
        self._quality_changed()
        self._loading = False

    def _apply_color(self, c: ColorSettings, keep_preset: str = "custom") -> None:
        self._loading = True
        self.sl_sat.set(c.saturation)
        self.sl_con.set(c.contrast)
        self.sl_bri.set(c.brightness)
        self.sl_gam.set(c.gamma)
        _combo_set(self.tonemap, c.tonemap)
        _combo_set(self.engine, c.engine)
        self.peak.setValue(c.source_peak)
        _combo_set(self.preset, keep_preset)
        self._loading = False

    def collect_settings(self) -> Settings:
        s = self.settings
        s.output_dir = self.out_dir.text().strip()
        s.suffix = self.suffix.text()
        s.container = self.container.currentData() or "mp4"
        s.recursive = self.chk_recursive.isChecked()
        s.non_hdr = self.non_hdr.currentData() or "skip"
        s.exists = self.exists.currentData() or "rename"
        s.color = ColorSettings(
            saturation=self.sl_sat.value(), contrast=self.sl_con.value(),
            brightness=self.sl_bri.value(), gamma=self.sl_gam.value(),
            tonemap=self.tonemap.currentData() or "auto", engine=self.engine.currentData() or "auto",
            source_peak=self.peak.value(),
        )
        s.color_preset = self.preset.currentData() or "custom"
        s.quality_mode = self.quality.currentData() or "original"
        s.crf = self.crf.value()
        s.bitrate_kbps = self.bitrate.value()
        s.codec = self.codec.currentData() or "h264"
        s.enc_preset = self.enc_preset.currentData() or "medium"
        s.hwaccel = self.hwaccel.currentData() or "auto"
        s.resolution = self.resolution.currentData() or "keep"
        s.fps = self.fps.value()
        s.audio = self.audio.currentData() or "copy"
        s.audio_kbps = self.audio_kbps.value()
        s.parallel = self.parallel.value()
        s.preview_time = self.preview_time.value()
        s.ffmpeg_path = self.ffmpeg_path.text().strip()
        s.keep_metadata = self.chk_keep_meta.isChecked()
        s.show_log = self.chk_show_log.isChecked()
        return s

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.runner.running:
            if QMessageBox.question(self, "", tr("msg_confirm_cancel")) != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.runner.cancel()
        try:
            cfg.save(self.collect_settings())
        except OSError:
            pass
        event.accept()

    # ---------------------------------------------------------- caps
    def _detect_caps_async(self) -> None:
        path = self.ffmpeg_path.text().strip()
        self.caps_label.setText("…")

        def work():
            self.bridge.caps_ready.emit(capsmod.detect(path))

        threading.Thread(target=work, daemon=True).start()

    def _on_caps(self, c) -> None:
        self.caps = c
        self.caps_label.setText(c.summary() + ("\n" + "\n".join(c.notes) if c.notes else ""))
        self._update_hw_availability()
        if not c.found:
            self.status.setText(tr("msg_no_ffmpeg"))
        elif not c.can_tonemap:
            self.status.setText(tr("msg_no_tonemap"))

    def _update_hw_availability(self) -> None:
        idx = self.hwaccel.findData("vaapi")
        if idx < 0:
            return
        label = tr("hw_vaapi") if self.caps.vaapi_ok else f"{tr('hw_vaapi')} — {tr('hw_unavailable')}"
        self.hwaccel.setItemText(idx, label)

    # ---------------------------------------------------------- color / quality reactions
    def _slider_changed(self) -> None:
        if self._loading:
            return
        _combo_set(self.preset, "custom")

    def _preset_chosen(self) -> None:
        if self._loading:
            return
        key = self.preset.currentData()
        if key == "custom" or key is None:
            return
        if key.startswith("user:"):
            c = self.user_presets.get(key[5:])
        else:
            c = COLOR_PRESETS.get(key)
        if c:
            self._apply_color(c, keep_preset=key)

    def _quality_changed(self) -> None:
        mode = self.quality.currentData()
        self.crf.setEnabled(mode == "crf")
        self.bitrate.setEnabled(mode == "bitrate")

    def save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, tr("menu_save_preset"), tr("msg_preset_name"))
        name = name.strip()
        if not ok or not name:
            return
        self.user_presets[name] = self.collect_settings().color
        cfg.save_user_presets(self.user_presets)
        self._fill_presets()
        _combo_set(self.preset, "user:" + name)

    def delete_preset(self) -> None:
        if not self.user_presets:
            return
        name, ok = QInputDialog.getItem(self, tr("menu_delete_preset"), tr("msg_preset_name"),
                                        sorted(self.user_presets), 0, False)
        if ok and name in self.user_presets:
            del self.user_presets[name]
            cfg.save_user_presets(self.user_presets)
            self._fill_presets()

    def reset_settings(self) -> None:
        if QMessageBox.question(self, "", tr("msg_reset")) != QMessageBox.StandardButton.Yes:
            return
        lang = self.settings.language
        self.settings = Settings(language=lang)
        self.apply_settings(self.settings)
        self.retranslate()

    # ---------------------------------------------------------- files
    def dragEnterEvent(self, e: QDragEnterEvent) -> None:  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent) -> None:  # noqa: N802
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        self.add_paths(paths)

    def add_files_dialog(self) -> None:
        start = self.settings.last_input_dir or os.path.expanduser("~")
        files, _ = QFileDialog.getOpenFileNames(self, tr("dlg_select_files"), start,
                                                f"{tr('filter_files')} (*.mp4 *.mov *.mkv *.m4v *.hevc *.ts *.mts);;* (*)")
        if files:
            self.settings.last_input_dir = os.path.dirname(files[0])
            self.add_paths(files)

    def add_folder_dialog(self) -> None:
        start = self.settings.last_input_dir or os.path.expanduser("~")
        d = QFileDialog.getExistingDirectory(self, tr("dlg_select_folder"), start)
        if d:
            self.settings.last_input_dir = d
            self.add_paths([d])

    def add_paths(self, paths: list[str]) -> None:
        if self.runner.running:
            QMessageBox.information(self, "", tr("warn_running"))
            return
        existing = {self.table.item(i, 0).data(Qt.ItemDataRole.UserRole) for i in range(self.table.rowCount())}
        files = [f for f in collect_files(paths, self.chk_recursive.isChecked()) if f not in existing]
        for f in files:
            row = self.table.rowCount()
            self.table.insertRow(row)
            it = QTableWidgetItem(os.path.basename(f))
            it.setData(Qt.ItemDataRole.UserRole, f)
            it.setToolTip(f)
            self.table.setItem(row, 0, it)
            self.table.setItem(row, 1, QTableWidgetItem(tr("probing")))
            self.table.setItem(row, 2, QTableWidgetItem(""))
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setTextVisible(True)
            self.table.setCellWidget(row, 3, bar)
        ffprobe = self.caps.ffprobe

        def work(flist):
            for f in flist:
                self.bridge.probed.emit(f, probe(f, ffprobe))

        if files:
            threading.Thread(target=work, args=(files,), daemon=True).start()

    def _row_of(self, path: str) -> int:
        for i in range(self.table.rowCount()):
            if self.table.item(i, 0).data(Qt.ItemDataRole.UserRole) == path:
                return i
        return -1

    def _on_probed(self, path: str, info) -> None:
        self.infos[path] = info
        row = self._row_of(path)
        if row >= 0:
            self._refresh_info_cell(row)

    def _refresh_info_cell(self, row: int) -> None:
        path = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        info = self.infos.get(path)
        if info is None:
            return
        if info.error and not info.width:
            self.table.item(row, 1).setText(tr("err_probe"))
            self.table.item(row, 1).setToolTip(info.error)
            return
        kind = (tr("hdr_yes") + ("10+" if info.has_hdr10plus else (" " + info.hdr_kind if info.hdr_kind else "")))\
            if info.is_hdr else tr("hdr_no")
        mbps = info.bitrate / 1e6 if info.bitrate else 0
        txt = f"{kind} · {info.width}x{info.height} · {info.fps:.3g} fps · {info.vcodec} {info.bit_depth}-bit · {mbps:.1f} Mb/s · {info.duration:.0f}s"
        self.table.item(row, 1).setText(txt)
        self.table.item(row, 1).setToolTip(
            f"trc={info.color_trc} primaries={info.color_primaries} space={info.color_space} pix={info.pix_fmt}")

    def remove_selected(self) -> None:
        if self.runner.running:
            return
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            path = self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            self.infos.pop(path, None)
            self.table.removeRow(r)

    def clear_files(self) -> None:
        if self.runner.running:
            return
        self.table.setRowCount(0)
        self.infos.clear()
        self.jobs.clear()
        self.total_bar.setValue(0)

    def _selected_info(self) -> MediaInfo | None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        if not rows:
            if self.table.rowCount() == 0:
                return None
            rows = {0}
        path = self.table.item(min(rows), 0).data(Qt.ItemDataRole.UserRole)
        return self.infos.get(path)

    def _selection_changed(self) -> None:
        pass

    def browse_out_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, tr("dlg_select_out"), self.out_dir.text() or os.path.expanduser("~"))
        if d:
            self.out_dir.setText(d)

    # ---------------------------------------------------------- preview / command
    def open_preview(self) -> None:
        info = self._selected_info()
        if info is None or not info.width:
            QMessageBox.information(self, "", tr("preview_select"))
            return
        dlg = PreviewDialog(self, info)
        dlg.exec()

    def show_command(self) -> None:
        info = self._selected_info()
        if info is None or not info.width:
            QMessageBox.information(self, "", tr("preview_select"))
            return
        s = self.collect_settings()
        p = pipeline.plan(info, s, self.caps)
        text = tr("plan_line", engine=p.engine, tonemap=p.tonemap, enc=p.encoder, q=p.quality_desc, pix=p.pix_fmt)
        text += "\n\n" + p.as_shell()
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("cmd_title"))
        dlg.resize(900, 300)
        lay = QVBoxLayout(dlg)
        te = QPlainTextEdit(text)
        te.setReadOnly(True)
        lay.addWidget(te)
        dlg.exec()

    def show_caps(self) -> None:
        QMessageBox.information(self, tr("caps_title"), self.caps.summary().replace(" | ", "\n") +
                                ("\n\n" + "\n".join(self.caps.notes) if self.caps.notes else ""))

    def show_about(self) -> None:
        QMessageBox.about(self, tr("menu_about"), tr("about_text", ver=__version__))

    # ---------------------------------------------------------- run
    def start(self) -> None:
        if self.runner.running:
            return
        if self.table.rowCount() == 0:
            QMessageBox.information(self, "", tr("msg_no_files"))
            return
        if not self.caps.found:
            QMessageBox.warning(self, "", tr("msg_no_ffmpeg"))
            return
        s = self.collect_settings()
        cfg.save(s)
        self.jobs = []
        for row in range(self.table.rowCount()):
            path = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            info = self.infos.get(path) or probe(path, self.caps.ffprobe)
            self.infos[path] = info
            job = Job(index=row, info=info, plan=None)
            if info.error and not info.width:
                job.status, job.message = "error", info.error
            elif not info.is_hdr and s.non_hdr == "skip":
                job.status, job.message = "skipped", tr("hdr_no")
            elif info.is_hdr and not self.caps.can_tonemap:
                job.status, job.message = "error", tr("msg_no_tonemap")
            else:
                codec, _ = pipeline.effective_codec(s, self.caps)
                out = pipeline.resolve_collision(pipeline.output_path(info, s, codec), s.exists)
                if out is None:
                    job.status, job.message = "skipped", tr("exists_skip")
                else:
                    job.plan = pipeline.plan(info, s, self.caps, out)
            self.jobs.append(job)
            self._update_row(job)
        runnable = [j for j in self.jobs if j.plan is not None]
        self.log.clear()
        for j in self.jobs:
            if j.plan:
                self.log.appendPlainText(j.plan.as_shell())
        self.total_bar.setValue(0)
        self.status.setText(tr("status_running"))
        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        if not runnable:
            self._on_all_done()
            return
        self.runner.start(runnable, s.parallel)

    def cancel(self) -> None:
        if self.runner.running:
            self.runner.cancel()

    def _update_row(self, job: Job) -> None:
        row = job.index
        if row >= self.table.rowCount():
            return
        st = tr(f"st_{job.status}")
        if job.message:
            st += f" — {job.message}"
        self.table.item(row, 2).setText(st)
        self.table.item(row, 2).setToolTip(job.message)
        bar = self.table.cellWidget(row, 3)
        if isinstance(bar, QProgressBar):
            bar.setValue(int(job.progress))
            extra = " ".join(x for x in (job.speed, job.eta) if x)
            bar.setFormat(f"%p% {extra}".strip() if job.status == "running" else "%p%")

    def _on_progress(self, job: Job) -> None:
        self._update_row(job)
        self._update_total()

    def _on_job_done(self, job: Job) -> None:
        self._update_row(job)
        self._update_total()
        if job.status == "error":
            for line in job.log[-15:]:
                self.log.appendPlainText(line)

    def _update_total(self) -> None:
        active = [j for j in self.jobs if j.plan is not None]
        if not active:
            return
        pct = sum(j.progress for j in active) / len(active)
        self.total_bar.setValue(int(pct))

    def _on_all_done(self) -> None:
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        ok = sum(1 for j in self.jobs if j.status == "done")
        err = sum(1 for j in self.jobs if j.status == "error")
        skip = sum(1 for j in self.jobs if j.status in ("skipped", "cancelled"))
        if any(j.status == "cancelled" for j in self.jobs):
            self.status.setText(tr("status_cancelled"))
        else:
            self.status.setText(tr("status_done", ok=ok, err=err, skip=skip))
            if ok and not err:
                self.total_bar.setValue(100)


def run() -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("hdr2sdr")
    w = MainWindow()
    w.show()
    return app.exec()
