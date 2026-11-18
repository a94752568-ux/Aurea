"""Panel de clonación de voz (XTTS v2): importar muestras, listar perfiles,
asignar a personajes y eliminar. Flujo simplificado y directo."""
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QListWidget, QPushButton,
    QHBoxLayout, QLabel, QFileDialog, QMessageBox,
    QLineEdit, QGroupBox, QComboBox,
)

from ..core.database import Database
from ..voice import sample_prep, clone_store

# Voces de Kokoro disponibles (sin em_alex que es del narrador)
KOKORO_VOICES = ["em_alex", "ef_dora", "em_santa"]


class ClonePanel(QWidget):
    """Panel para gestionar perfiles de voz clonada y asignarlos a personajes."""

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        clone_store.ensure_schema()
        self._current_book_id = None
        main_lay = QVBoxLayout(self)

        # ---- Importar nueva voz ----
        import_group = QGroupBox("1. Importar muestra de voz")
        import_lay = QVBoxLayout()

        name_lay = QHBoxLayout()
        name_lay.addWidget(QLabel("Nombre del perfil:"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("ej. María, Pedro, Ana...")
        name_lay.addWidget(self.name_edit)
        import_lay.addLayout(name_lay)

        self.import_btn = QPushButton("🎙️ Seleccionar archivo de audio")
        self.import_btn.clicked.connect(self.import_sample)
        import_lay.addWidget(self.import_btn)

        self.quality_label = QLabel("")
        import_lay.addWidget(self.quality_label)

        import_group.setLayout(import_lay)
        main_lay.addWidget(import_group)

        # ---- Perfiles disponibles ----
        list_group = QGroupBox("2. Voces clonadas disponibles")
        list_lay = QVBoxLayout()
        self.listw = QListWidget()
        self.listw.setSelectionMode(QListWidget.ExtendedSelection)
        list_lay.addWidget(self.listw)
        list_group.setLayout(list_lay)
        main_lay.addWidget(list_group)

        # ---- Asignar a personajes ----
        assign_group = QGroupBox("3. Asignar voz a personaje")
        assign_lay = QVBoxLayout()

        char_lay = QHBoxLayout()
        char_lay.addWidget(QLabel("Personaje:"))
        self.char_combo = QComboBox()
        char_lay.addWidget(self.char_combo, 1)
        assign_lay.addLayout(char_lay)

        # Tipo de voz (kokoro o clon)
        engine_lay = QHBoxLayout()
        engine_lay.addWidget(QLabel("Tipo de voz:"))
        self.engine_combo = QComboBox()
        self.engine_combo.addItems(["kokoro", "clon (XTTS)"])
        self.engine_combo.currentTextChanged.connect(self._on_engine_changed)
        engine_lay.addWidget(self.engine_combo)
        assign_lay.addLayout(engine_lay)

        # Voz de Kokoro (solo visible cuando engine=kokoro)
        self.kokoro_lay = QHBoxLayout()
        self.kokoro_lay.addWidget(QLabel("Voz Kokoro:"))
        self.kokoro_combo = QComboBox()
        self.kokoro_combo.addItems(KOKORO_VOICES)
        self.kokoro_lay.addWidget(self.kokoro_combo)
        assign_lay.addLayout(self.kokoro_lay)

        btn_lay = QHBoxLayout()
        self.btn_assign = QPushButton("✔ Asignar voz seleccionada")
        self.btn_assign.clicked.connect(self.assign)
        btn_lay.addWidget(self.btn_assign)

        self.btn_delete = QPushButton("🗑️ Eliminar perfil seleccionado")
        self.btn_delete.clicked.connect(self.delete)
        btn_lay.addWidget(self.btn_delete)
        assign_lay.addLayout(btn_lay)

        assign_group.setLayout(assign_lay)
        main_lay.addWidget(assign_group)

        main_lay.addStretch(1)
        self.refresh()

    # ------------------------------------------------------------------ #
    def set_book_id(self, book_id):
        """Actualiza la lista de personajes según el libro seleccionado."""
        self._current_book_id = book_id
        self.refresh()

    def refresh(self):
        """Refresca listas de perfiles y personajes."""
        self.listw.clear()
        for p in clone_store.list_profiles():
            self.listw.addItem(f'{p["name"]}  ·  {Path(p["sample_path"]).name}')
        if self.listw.count() > 0:
            self.listw.setCurrentRow(0)

        self.char_combo.clear()
        if self._current_book_id is not None:
            chars = self.db.characters(self._current_book_id)
            for c in chars:
                engine = getattr(c, 'engine', 'kokoro') or 'kokoro'
                tag = " (XTTS)" if engine == "xtts" else ""
                self.char_combo.addItem(f"{c.name}{tag} ({c.voice})", c.id)

    def _on_engine_changed(self, text):
        """Mostrar u ocultar el combo de voces Kokoro según el motor seleccionado."""
        is_kokoro = text == "kokoro"
        self.kokoro_lay.itemAt(1).widget().setVisible(is_kokoro)
        self.kokoro_lay.itemAt(0).widget().setVisible(is_kokoro)

    # ------------------------------------------------------------------ #
    def import_sample(self):
        base_name = self.name_edit.text().strip()
        if not base_name:
            QMessageBox.information(self, "Falta nombre",
                                    "Escribe un nombre para el perfil primero.")
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar muestra de voz", "",
            "Audio (*.mp3 *.wav *.flac *.m4a *.ogg *.opus)")
        if not path:
            return

        try:
            pid, rep = sample_prep.import_sample(path, base_name)
        except Exception as e:
            QMessageBox.warning(self, "No se pudo importar", str(e))
            return

        quality_info = (f"✓ Perfil '{base_name}' creado: "
                        f"{rep['duration']}s, {rep['verdict']}")
        if rep.get("problems"):
            quality_info += f"\n⚠ {', '.join(rep['problems'])}"
        self.quality_label.setText(quality_info)
        self.name_edit.clear()
        self.refresh()

    # ------------------------------------------------------------------ #
    def assign(self):
        cid = self.char_combo.currentData()
        if cid is None:
            QMessageBox.information(self, "Sin personaje",
                                    "Selecciona un personaje de la lista.")
            return
        engine = self.engine_combo.currentText()
        if engine == "kokoro":
            voice = self.kokoro_combo.currentText()
            clone_store.assign_character(cid, "kokoro", None)
            QMessageBox.information(self, "Listo",
                                    f"Voz Kokoro '{voice}' asignada al personaje.")
        else:
            row = self.listw.currentRow()
            if row < 0:
                QMessageBox.information(self, "Sin perfil",
                                        "Selecciona un perfil de voz de la lista.")
                return
            profiles = clone_store.list_profiles()
            pid = profiles[row]["id"]
            prof_name = profiles[row]["name"]
            clone_store.assign_character(cid, prof_name, "xtts", pid)
            QMessageBox.information(self, "Listo",
                                    f"Voz clonada '{prof_name}' asignada al personaje.")
        self.refresh()

    # ------------------------------------------------------------------ #
    def delete(self):
        row = self.listw.currentRow()
        if row < 0:
            return
        profiles = clone_store.list_profiles()
        clone_store.delete_profile(profiles[row]["id"])
        self.refresh()
