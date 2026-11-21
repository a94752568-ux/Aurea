from __future__ import annotations

import struct
import wave
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# ============================================================
# PALETA — única fuente de verdad de color en toda la app
# ============================================================

COLORS = {
    "bg":          "#080c18",   # fondo raíz profundo (cyberpunk oscuro)
    "panel":       "#0c1424",   # paneles oscuros
    "panel2":      "#0e1524",   # cabeceras / inputs
    "line":        "#1e2d4a",   # bordes normales
    "line_bright": "#3a506b",   # bordes hover
    "text":        "#e8fbff",   # texto blanco puro para máximo contraste
    "dim":         "#6b8799",   # texto secundario cian claro
    "cyan":        "#00f3ff",   # acento primario cyberpunk cian
    "cyan_glow":   "rgba(0, 243, 255, 0.5)",
    "magenta":     "#ff00ff",   # acento secundario magenta
    "magenta_glow":"rgba(255, 0, 255, 0.5)",
    "amber":       "#ffb020",   # narrador / advertencias
    "green":       "#00ff9d",   # estado OK
}

MONO = "'DejaVu Sans Mono', 'Noto Sans Mono', 'Ubuntu Mono', 'Liberation Mono', monospace"
SANS = "'DejaVu Sans', 'Noto Sans', 'Liberation Sans', sans-serif"
# Familias simples (una sola fuente) para QFont/QPainter: QFont.setFamily no
# acepta pilas QSS con comas. Primera de cada pila, verificada con fc-match.
MONO_FAMILY = "DejaVu Sans Mono"
SANS_FAMILY = "DejaVu Sans"
# NOTA FUENTES (verificado 2026-11-20 con fc-list/fc-match en la VM):
# presentes: DejaVu Sans Mono, Noto Sans Mono, Noto Mono, Liberation Mono,
# DejaVu Sans, Noto Sans, Liberation Sans (+ Noto Color Emoji).
# AUSENTES: Ubuntu Mono, Cascadia Mono, JetBrains Mono, Fira Code, Segoe UI
# (fc-match cae a Noto Sans). 'Ubuntu Mono' queda solo como fallback por si
# existe en el Lubuntu/Ubuntu real de destino; nunca como primera opción.

# ============================================================
# ESCALA TIPOGRÁFICA + GRID DE ESPACIADO (múltiplos de 8 px)
# ============================================================
GRID = 8            # unidad base; todos los márgenes/espaciados son 8/16/24
SPACING_SM = 8
SPACING_MD = 16
FONT_SIZES = {
    "title": 20,   # título HUD (logo AUREA)
    "hud": 10,     # etiquetas HUD en mayúsculas
    "label": 11,   # etiquetas de formulario / botones
    "body": 13,    # cuerpo general
    "data": 12,    # mono de datos (inputs, log, toasts)
    "small": 11,   # texto secundario mono
}


# ============================================================
# QSS GLOBAL — CYBERPUNK NEON (cian + magenta, glow)
# Construido con concatenación para evitar problemas de llaves en f-strings
# ============================================================

_T = MONO
_S = SANS
_C = COLORS['cyan']
_M = COLORS['magenta']
_Cg = COLORS['cyan_glow']
_Mg = COLORS['magenta_glow']
_Tx = COLORS['text']
_Dm = COLORS['dim']
_P2 = COLORS['panel2']
_Pn = COLORS['panel']
_Ln = COLORS['line']
_Lb = COLORS['line_bright']
_Bg = COLORS['bg']

_QS = (
    "* {"
    "font-family: " + _S + ";"
    "font-size: 13px;"
    "color: " + _Tx + ";"
    "selection-background-color: " + _C + ";"
    "selection-color: " + _Bg + ";"
    "}\n"

    "QMainWindow, QDialog, QFrame#root {"
    "background: " + _Bg + ";"
    "}\n"

    "QLabel {"
    "background: transparent;"
    "color: " + _Tx + ";"
    "}\n"
    "QLabel[hud=\"true\"] {"
    "font-family: " + _T + ";"
    "font-size: 10px;"
    "letter-spacing: 2px;"
    "text-transform: uppercase;"
    "color: " + _Dm + ";"
    "}\n"
    "QLabel[hud=\"title\"] {"
    "font-family: " + _T + ";"
    "font-size: 20px;"
    "letter-spacing: 4px;"
    "color: " + _C + ";"
    "}\n"

    "QRadioButton {"
    "color: " + _Tx + ";"
    "spacing: 8px;"
    "}\n"
    "QRadioButton::indicator {"
    "width: 16px; height: 16px;"
    "border: 1px solid " + _Lb + ";"
    "background: " + _P2 + ";"
    "border-radius: 2px;"
    "}\n"
    "QRadioButton::indicator:checked {"
    "border-color: " + _M + ";"
    "background: " + _M + ";"
    "}\n"

    "QPushButton {"
    "font-family: " + _T + ";"
    "font-size: 11px;"
    "letter-spacing: 1px;"
    "text-transform: uppercase;"
    "padding: 8px 16px;"
    "background: " + _P2 + ";"
    "border: 1px solid " + _Lb + ";"
    "border-radius: 0px;"
    "color: " + _Tx + ";"
    "}\n"
    "QPushButton:hover {"
    "border-color: " + _C + ";"
    "color: " + _C + ";"
    "background: rgba(0, 243, 255, 0.08);"
    "}\n"
    "QPushButton:pressed { background: " + _Bg + "; }\n"
    "QPushButton:disabled { color: " + _Dm + "; border-color: " + _Ln + "; }\n"

    "QPushButton[variant=\"primary\"] {"
    "color: " + _C + ";"
    "border-color: " + _C + ";"
    "background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
    "stop:0 rgba(0,243,255,0.16), stop:1 rgba(255,0,255,0.10));"
    "}\n"
    "QPushButton[variant=\"primary\"]:hover {"
    "background: rgba(0,229,255,0.25);"
    "}\n"

    "QPushButton[variant=\"ghost\"] {"
    "background: transparent;"
    "border-color: " + _Ln + ";"
    "color: " + _Dm + ";"
    "}\n"
    "QPushButton[variant=\"ghost\"]:hover { color: " + _Tx + "; border-color: " + _Lb + "; }\n"

    "QPushButton[variant=\"danger\"] {"
    "color: " + _M + ";"
    "border-color: " + _M + ";"
    "}\n"
    "QPushButton[variant=\"danger\"]:hover {"
    "color: " + _M + ";"
    "border-color: " + _M + ";"
    "background: rgba(255,0,255,0.12);"
    "}\n"

    "QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {"
    "background: " + _P2 + ";"
    "border: 1px solid " + _Lb + ";"
    "padding: 8px 16px;"
    "font-family: " + _T + ";"
    "font-size: 12px;"
    "color: " + _Tx + ";"
    "}\n"
    "QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {"
    "border-color: " + _C + ";"
    "}\n"
    "QComboBox::drop-down { border: none; width: 22px; }\n"
    "QComboBox QAbstractItemView {"
    "background: " + _P2 + ";"
    "border: 1px solid " + _Lb + ";"
    "selection-background-color: rgba(0,243,255,0.20);"
    "selection-color: " + _C + ";"
    "outline: none;"
    "}\n"

    "QSlider::groove:horizontal {"
    "height: 3px;"
    "background: " + _Lb + ";"
    "}\n"
    "QSlider::handle:horizontal {"
    "width: 14px;"
    "margin: -6px 0;"
    "background: " + _C + ";"
    "border: 2px solid " + _Bg + ";"
    "}\n"
    "QSlider::sub-page:horizontal { background: " + _C + "; }\n"

    "QListWidget {"
    "background: transparent;"
    "border: none;"
    "outline: none;"
    "}\n"
    "QListWidget::item {"
    "border-left: 2px solid " + _Lb + ";"
    "padding: 8px 16px;"
    "margin: 0 8px 8px 8px;"
    "background: rgba(255,255,255,0.012);"
    "color: " + _Tx + ";"
    "}\n"
    "QListWidget::item:hover {"
    "background: rgba(0,229,255,0.06);"
    "border-left: 2px solid " + _C + ";"
    "}\n"
    "QListWidget::item:selected {"
    "border-left: 2px solid " + _C + ";"
    "background: rgba(0,229,255,0.12);"
    "color: " + _C + ";"
    "}\n"

    "QTabWidget::pane { border: 1px solid " + _Ln + "; top: -1px; }\n"
    "QTabBar::tab {"
    "font-family: " + _T + ";"
    "font-size: 11px;"
    "letter-spacing: 1px;"
    "text-transform: uppercase;"
    "padding: 8px 16px;"
    "background: " + _P2 + ";"
    "color: " + _Dm + ";"
    "border: 1px solid " + _Ln + ";"
    "border-bottom: none;"
    "margin-right: 2px;"
    "}\n"
    "QTabBar::tab:selected { color: " + _C + "; background: " + _Pn + "; border-color: " + _C + "; }\n"

    "QScrollBar:vertical { background: transparent; width: 6px; margin: 0; }\n"
    "QScrollBar::handle:vertical { background: " + _Lb + "; min-height: 30px; border-radius: 3px; }\n"
    "QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }\n"
    "QScrollBar:horizontal { background: transparent; height: 6px; }\n"
    "QScrollBar::handle:horizontal { background: " + _Lb + "; min-width: 30px; border-radius: 3px; }\n"

    "QScrollArea { border: none; background: transparent; }\n"

    "QStatusBar {"
    "background: " + _Pn + ";"
    "border-top: 1px solid " + _Ln + ";"
    "font-family: " + _T + ";"
    "font-size: 11px;"
    "color: " + _Dm + ";"
    "}\n"

    "QToolTip {"
    "background: " + _P2 + ";"
    "color: " + _C + ";"
    "border: 1px solid " + _C + ";"
    "font-family: " + _T + ";"
    "font-size: 11px;"
    "padding: 8px;"
    "}\n"

    # (QProgressBar legible definido en el bloque de estados completos;
    # el antiguo aquí — texto transparente, 4 px — se eliminó, M9.)
    "QCheckBox { spacing: 8px; }\n"
    "QCheckBox::indicator {"
    "width: 16px; height: 16px;"
    "border: 1px solid " + _Lb + ";"
    "background: " + _P2 + ";"
    "border-radius: 2px;"
    "}\n"
    "QCheckBox::indicator:checked {"
    "border-color: " + _M + ";"
    "background: " + _M + ";"
    "}\n"
    "QCheckBox::indicator:checked:hover {"
    "border-color: " + _M + ";"
    "background: " + _M + ";"
    "}\n"

    # --- Estados completos (Fase 4): menús, diálogos, popup de combo,
    # botones de spinbox, splitter, editor de texto, progreso legible ---
    "QMenu {"
    "background: " + _P2 + ";"
    "border: 1px solid " + _Lb + ";"
    "padding: 8px;"
    "}\n"
    "QMenu::item {"
    "padding: 8px 16px;"
    "background: transparent;"
    "color: " + _Tx + ";"
    "}\n"
    "QMenu::item:selected {"
    "background: rgba(0,243,255,0.20);"
    "color: " + _C + ";"
    "}\n"
    "QMenu::item:disabled { color: " + _Dm + "; }\n"
    "QMenu::separator { height: 1px; background: " + _Ln + "; margin: 8px 0; }\n"

    "QMessageBox {"
    "background: " + _Bg + ";"
    "border: 1px solid " + _Lb + ";"
    "}\n"
    "QMessageBox QLabel { color: " + _Tx + "; background: transparent; }\n"

    "QComboBox::item { padding: 8px 16px; min-height: 24px; }\n"
    "QComboBox::item:hover { background: rgba(0,229,255,0.06); }\n"
    "QComboBox::item:selected { background: rgba(0,243,255,0.20); color: " + _C + "; }\n"
    "QComboBox:disabled { color: " + _Dm + "; border-color: " + _Ln + "; }\n"
    "QComboBox::drop-down:hover { background: rgba(0,243,255,0.08); }\n"

    "QSpinBox::up-button, QDoubleSpinBox::up-button,"
    "QSpinBox::down-button, QDoubleSpinBox::down-button {"
    "subcontrol-origin: border;"
    "width: 16px;"
    "background: " + _Pn + ";"
    "border-left: 1px solid " + _Ln + ";"
    "}\n"
    "QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,"
    "QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {"
    "background: rgba(0,243,255,0.12);"
    "}\n"
    "QSpinBox:disabled, QDoubleSpinBox:disabled { color: " + _Dm + "; border-color: " + _Ln + "; }\n"

    "QSplitter { background: transparent; }\n"
    "QSplitter::handle:horizontal { width: 2px; background: " + _Ln + "; }\n"
    "QSplitter::handle:vertical { height: 2px; background: " + _Ln + "; }\n"
    "QSplitter::handle:hover { background: " + _C + "; }\n"

    "QTextEdit {"
    "background: " + _P2 + ";"
    "border: 1px solid " + _Lb + ";"
    "padding: 8px;"
    "font-family: " + _T + ";"
    "font-size: 12px;"
    "color: " + _Tx + ";"
    "selection-background-color: " + _C + ";"
    "selection-color: " + _Bg + ";"
    "}\n"
    "QTextEdit:focus { border-color: " + _C + "; }\n"
    "QTextEdit:disabled { color: " + _Dm + "; }\n"

    # Barra de progreso con texto LEGIBLE (antes color: transparent y 4 px:
    # el porcentaje no se veía). Fondo oscuro, texto claro encima.
    "QProgressBar {"
    "background: " + _P2 + ";"
    "border: 1px solid " + _Ln + ";"
    "height: 16px;"
    "text-align: center;"
    "font-family: " + _T + ";"
    "font-size: 11px;"
    "color: " + _Tx + ";"
    "}\n"
    "QProgressBar::chunk {"
    "background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
    "stop:0 " + _C + ", stop:1 " + _M + ");"
    "}\n"
    "QProgressBar:disabled { color: " + _Dm + "; }\n"

    # Foco / deshabilitado coherentes en el resto de controles.
    "QPushButton:focus { border-color: " + _C + "; }\n"
    "QLineEdit:disabled { color: " + _Dm + "; border-color: " + _Ln + "; }\n"
    "QListWidget::item:disabled { color: " + _Dm + "; }\n"
    "QTabBar::tab:hover { color: " + _Tx + "; }\n"
    "QTabBar::tab:disabled { color: " + _Dm + "; }\n"
    "QCheckBox:disabled, QRadioButton:disabled { color: " + _Dm + "; }\n"
    "QCheckBox::indicator:disabled, QRadioButton::indicator:disabled { border-color: " + _Ln + "; }\n"
    "QSlider:disabled { color: " + _Dm + "; }\n"
    "QSlider::handle:horizontal:disabled { background: " + _Dm + "; }\n"
    "QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background: " + _C + "; }\n"
    "QSlider::groove:vertical { width: 3px; background: " + _Lb + "; }\n"
    "QSlider::handle:vertical { height: 14px; margin: 0 -6px; background: " + _C + "; border: 2px solid " + _Bg + "; }\n"
    "QSlider::sub-page:vertical { background: " + _C + "; }\n"

    # Barra de transporte y etiqueta de tiempo (antes setStyleSheet ad hoc
    # en main_window.py; ahora viven aquí por objectName).
    "QFrame#transportBar {"
    "background: " + _Pn + ";"
    "border-top: 1px solid " + _Ln + ";"
    "}\n"
    "QLabel#timeLabel {"
    "font-family: " + _T + ";"
    "font-size: 12px;"
    "color: " + _Dm + ";"
    "background: transparent;"
    "}\n"
    "QLabel[hud=\"title-accent\"] {"
    "font-family: " + _T + ";"
    "font-size: 20px;"
    "letter-spacing: 4px;"
    "color: " + _M + ";"
    "}\n"
    "QPushButton[transport=\"true\"] {"
    "font-size: 16px;"
    "padding: 8px 16px;"
    "min-width: 48px;"
    "}\n"
    "QFrame#sidebar QPushButton {"
    "font-size: 18px;"
    "padding: 0;"
    "}\n"

    "QGroupBox {"
    "font-family: " + _T + ";"
    "font-size: 11px;"
    "letter-spacing: 1px;"
    "text-transform: uppercase;"
    "color: " + _Dm + ";"
    "border: 1px solid " + _Lb + ";"
    "border-radius: 0;"
    "margin-top: 16px;"
    "padding-top: 16px;"
    "background: " + _Pn + ";"
    "}\n"
    "QGroupBox::title {"
    "subcontrol-origin: margin;"
    "left: 8px;"
    "padding: 0 8px;"
    "color: " + _C + ";"
    "}\n"
    "QGroupBox:focus-within {"
    "border-color: " + _C + ";"
    "}\n"
    # (M9) Los bloques duplicados de QListWidget y QPushButton que cerraban
    # el QSS — y pisaban los primeros — se eliminaron: cada selector se
    # define una sola vez en este archivo.
)

QSS = _QS


def status_text_style(color: str) -> str:
    """Estilo en línea DOCUMENTADO para health_lbl/status_lbl.

    Excepción permitida al "sin setStyleSheet ad hoc": el color de estos dos
    indicadores cambia por estado (verde/ámbar/magenta/cian) y se fija desde
    la lógica de estado en main_window.py; la fuente y el tamaño viven aquí,
    en una sola función, no repartidos por el archivo.
    """
    return f"color:{color};font-family:{MONO};font-size:11px;"


def apply_theme(win: QMainWindow | QWidget | QApplication) -> None:
    """Aplica QSS global y ajustes de fuente. Llamar una vez al iniciar."""
    app = QApplication.instance()
    if app is not None:
        app.setStyleSheet(QSS)
        f = QFont(SANS_FAMILY)
        f.setPointSize(10)
        app.setFont(f)
    if isinstance(win, QWidget):
        win.setStyleSheet("")  # el win hereda del app-level


def apply_dark_palette(app: QApplication) -> None:
    """Aplica QPalette oscura sobre el estilo Fusion."""
    app.setStyle("Fusion")
    bg = QColor("#080c18")
    panel = QColor("#0c1424")
    text = QColor("#e8fbff")
    p = QPalette()
    p.setColor(QPalette.Window, bg)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, panel)
    p.setColor(QPalette.AlternateBase, bg)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, panel)
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.ToolTipBase, panel)
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.PlaceholderText, QColor("#6b8799"))
    p.setColor(QPalette.Highlight, QColor("#0f2a3a"))
    p.setColor(QPalette.HighlightedText, QColor("#00f3ff"))
    app.setPalette(p)


# ============================================================
# HUD PANEL — panel con esquinas en bracket (firma AUREA)
# ============================================================

class HudPanel(QFrame):
    """Panel con marco y esquinas en bracket cyan.

    HudPanel("TITULO", "nota derecha")  ->  titulo + contenido own layout
    Layout propio: panel.body (QVBoxLayout).
    """

    def __init__(self, title: str = "", note: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("hudPanel")
        self._accent = QColor(COLORS["cyan"])

        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)

        if title:
            head = QFrame()
            # Estilo local del widget (excepción documentada: pertenece a
            # HudPanel, no al QSS global; usa solo colores de COLORS).
            head.setStyleSheet(
                "background:" + COLORS['panel2'] + ";"
                "border-bottom:1px solid " + COLORS['line'] + ";"
            )
            hl = QHBoxLayout(head)
            hl.setContentsMargins(16, 8, 16, 8)
            t = QLabel(title)
            t.setProperty("hud", "true")
            hl.addWidget(t)
            hl.addStretch(1)
            if note:
                n = QLabel(note)
                n.setProperty("hud", "true")
                hl.addWidget(n)
            outer.addWidget(head)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(8, 8, 8, 8)
        self.body.setSpacing(8)
        outer.addLayout(self.body)

    def paintEvent(self, ev) -> None:  # noqa: N802
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # marco
        p.setPen(QPen(QColor(COLORS["line"]), 1))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        # brackets — B9: las 4 esquinas (antes solo sup-izq e inf-der).
        L = 14
        pen = QPen(self._accent, 2)
        p.setPen(pen)
        w, h = self.width(), self.height()
        # esquina sup-izq
        p.drawPolyline([QPointF(0.5, L), QPointF(0.5, 0.5), QPointF(L, 0.5)])
        # esquina sup-der
        p.drawPolyline([QPointF(w - L, 0.5), QPointF(w - 0.5, 0.5), QPointF(w - 0.5, L)])
        # esquina inf-izq
        p.drawPolyline([QPointF(0.5, h - L), QPointF(0.5, h - 0.5), QPointF(L, h - 0.5)])
        # esquina inf-der
        p.drawPolyline([QPointF(w - 0.5, h - L), QPointF(w - 0.5, h - 0.5), QPointF(w - L, h - 0.5)])
        p.end()


# ============================================================
# SIDEBAR — navegación vertical con indicador lateral
# ============================================================

class Sidebar(QFrame):
    """Sidebar vertical estilo HUD con indicador de sección activa.

    items: lista de (key, icono, tooltip). Señal `changed(str)` emite la key.
    Señal `footer_action(str)`: el botón inferior (p. ej. borrar libro).
    """

    changed = Signal(str)
    footer_action = Signal(str)

    def __init__(self, items: list[tuple[str, str, str]], parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("sidebar")  # QSS: QFrame#sidebar QPushButton
        self.setFixedWidth(64)
        # Estilo local del widget (excepción documentada: fondo/borde del
        # sidebar; los estados activo/inactivo viven en set_active()).
        self.setStyleSheet(
            "background:" + COLORS['panel'] + "; border-right:1px solid " + COLORS['line'] + ";"
        )
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 16, 8, 16)
        v.setSpacing(8)

        self._buttons: dict[str, QPushButton] = {}
        self._active: str | None = None

        for key, icon, tip in items:
            b = QPushButton(icon)
            b.setFixedSize(46, 46)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.set_active(k))
            v.addWidget(b, 0, Qt.AlignHCenter)
            self._buttons[key] = b

        v.addStretch(1)

    def add_footer(self, key: str, icon: str, tip: str, danger: bool = True) -> None:
        """Botón inferior (p. ej. borrar libro); no emite `changed` si danger."""
        b = QPushButton(icon)
        b.setFixedSize(46, 46)
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        if danger:
            b.setProperty("variant", "danger")
            b.clicked.connect(lambda _=False, k=key: self._flash_danger(b))
            # A12: el botón inferior también actúa (antes solo parpadeaba).
            b.clicked.connect(lambda _=False, k=key: self.footer_action.emit(k))
        else:
            b.clicked.connect(lambda _=False, k=key: self.set_active(k))
        self.layout().addWidget(b, 0, Qt.AlignHCenter)
        self._buttons[key] = b

    def _flash_danger(self, b: QPushButton) -> None:
        # Estilo local del widget (excepción documentada: parpadeo temporal
        # de peligro; se restaura a "" a los 400 ms).
        b.setStyleSheet(
            "color:" + COLORS['magenta'] + ";"
            "border:1px solid " + COLORS['magenta'] + ";"
            "background:rgba(255,45,120,0.10);"
        )
        QTimer.singleShot(400, lambda: b.setStyleSheet(""))

    def set_active(self, key: str) -> None:
        # Estilo local del widget (excepción documentada: el estado
        # activo/inactivo del botón es estado dinámico del Sidebar).
        if key == self._active or key not in self._buttons:
            return
        self._active = key
        for k, b in self._buttons.items():
            if k == key:
                b.setStyleSheet(
                    "color:" + COLORS['cyan'] + ";"
                    "border:1px solid rgba(0,229,255,0.4);"
                    "background:rgba(0,229,255,0.06);"
                )
            else:
                b.setStyleSheet(
                    "color:" + COLORS['dim'] + ";border:1px solid transparent;background:none;"
                )
        self.changed.emit(key)


# ============================================================
# WAVEFORM VIEW — dibuja la onda de un WAV con QPainter
# ============================================================

class WaveformView(QWidget):
    """Muestra la forma de onda de un WAV (o picos precalculados).

    set_wav(path)                -> lee picos del archivo
    set_peaks(peaks, played=0.3) -> picos en [-1, 1], fracción reproducida
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumHeight(44)
        self._peaks: list[float] = []
        self._played = 0.0

    # -- carga --
    def set_wav(self, path: str | Path, played: float = 0.0) -> None:
        try:
            self.set_peaks(_wav_peaks(str(path), buckets=600), played)
        except Exception:
            self._peaks = []
            self.update()

    def set_peaks(self, peaks: list[float], played: float = 0.0) -> None:
        self._peaks = peaks
        self._played = max(0.0, min(1.0, played))
        self.update()

    def set_played(self, frac: float) -> None:
        self._played = max(0.0, min(1.0, frac))
        self.update()

    # -- pintura --
    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        mid = h / 2

        # línea base
        p.setPen(QPen(QColor(COLORS["line"]), 1))
        p.drawLine(0, int(mid), w, int(mid))

        if not self._peaks:
            p.setPen(QPen(QColor(COLORS["dim"])))
            f = p.font()
            f.setFamily(MONO_FAMILY)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignCenter, "// SIN AUDIO CARGADO")
            p.end()
            return

        n = len(self._peaks)
        step = max(1, w // n)
        split_x = int(w * self._played)
        for i, pk in enumerate(self._peaks):
            x = int(i * w / n)
            amp = max(1.0, abs(pk) * (h * 0.42))
            color = (
                QColor(COLORS["cyan"]) if x <= split_x else QColor(COLORS["dim"])
            )
            p.fillRect(x, int(mid - amp), max(1, step - 1), int(amp * 2), color)

        # cursor de reproducción
        p.setPen(QPen(QColor(COLORS["magenta"]), 2))
        p.drawLine(split_x, 0, split_x, h)
        p.end()


def _wav_peaks(path: str, buckets: int = 600) -> list[float]:
    """Lee un WAV 16-bit y devuelve `buckets` picos normalizados a [-1, 1]."""
    with wave.open(path, "rb") as w:
        nch = w.getnchannels()
        sw = w.getsampwidth()
        n = w.getnframes()
        raw = w.readframes(n)
    if sw != 2:
        raise ValueError("solo WAV PCM 16-bit")
    step = max(1, (n // buckets))
    peaks: list[float] = []
    fmt = f"<{n * nch}h"
    try:
        samples = struct.unpack(fmt, raw)
    except struct.error:
        return peaks
    for i in range(0, len(samples) - nch + 1, step * nch):
        chunk = samples[i : i + step * nch]
        peak = max(abs(s) for s in chunk) / 32768.0
        peaks.append(peak)
    return peaks[:buckets]


# ============================================================
# TOAST — aviso no bloqueante que se desvanece
# ============================================================

def toast(parent: QWidget, message: str, ms: int = 2600, kind: str = "info") -> None:
    """Muestra un aviso flotante abajo al centro; se autodestruye.

    kind: 'info' (cyan) | 'ok' (verde) | 'warn' (ámbar) | 'error' (magenta)
    """
    color = {"info": "cyan", "ok": "green", "warn": "amber", "error": "magenta"}.get(
        kind, "cyan"
    )
    box = QLabel(message, parent)
    # Estilo local del widget (excepción documentada: el color depende del
    # kind del aviso y se calcula aquí; fuente/tamaño en grid de 8 px).
    box.setStyleSheet(
        "background:" + COLORS['panel2'] + ";"
        "border:1px solid " + COLORS[color] + ";"
        "color:" + COLORS[color] + ";"
        "font-family:" + MONO + ";font-size:12px;"
        "padding:8px 16px;"
    )
    box.adjustSize()
    x = (parent.width() - box.width()) // 2
    y = parent.height() - box.height() - 28
    box.move(max(8, x), max(8, y))
    box.show()
    box.raise_()

    QTimer.singleShot(ms, box.deleteLater)
