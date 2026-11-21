"""Diálogo de corrección de género del reparto (v0.7.0).

Muestra cada personaje con su género inferido y el género de su voz,
y permite corregirlo a mano. Corregir el género NO reasigna la voz:
la voz asignada se conserva (el usuario la cambia en "Voz clonada"
si quiere). El género corregido queda marcado como manual y el
auto-casting no vuelve a inferirlo.
"""
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QGridLayout, QLabel, QVBoxLayout,
)

from ..casting.gender import DESCONOCIDO, FEMENINO, GENDERS, MASCULINO

GENDER_TEXT = {
    MASCULINO: "Masculino",
    FEMENINO: "Femenino",
    DESCONOCIDO: "Sin confirmar",
}


class GenderDialog(QDialog):
    """`characters`: lista de dicts de CastManager.cast_info()."""

    def __init__(self, parent, characters):
        super().__init__(parent)
        self.setWindowTitle("AUREA — Corregir género del reparto")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        lay.addWidget(QLabel(
            "Corrige el género de cada personaje. La voz asignada NO "
            "cambia al corregir el género; si la voz no encaja, cámbiala "
            "después en «Voz clonada»."))
        lay.addWidget(QLabel(
            "«Sin confirmar» = el género no se pudo inferir; la voz es "
            "la del narrador por defecto hasta que lo confirmes."))

        grid = QGridLayout()
        grid.addWidget(QLabel("Personaje"), 0, 0)
        grid.addWidget(QLabel("Voz"), 0, 1)
        grid.addWidget(QLabel("Género"), 0, 2)
        self._combos = {}
        for row, ch in enumerate(characters, start=1):
            name = ch["name"]
            if ch["is_narrator"]:
                name += " ● narrador"
            grid.addWidget(QLabel(name), row, 0)
            vg = ch["voice_gender"]
            voice_txt = f"{ch['voice']}"
            if ch["engine"] == "xtts":
                voice_txt = f"CLON: {ch['voice']}"
            elif vg != DESCONOCIDO:
                voice_txt += f" ({'M' if vg == MASCULINO else 'F'})"
            grid.addWidget(QLabel(voice_txt), row, 1)
            combo = QComboBox()
            for g in GENDERS:
                combo.addItem(GENDER_TEXT[g], g)
            combo.setCurrentIndex(GENDERS.index(ch["gender"]))
            if ch["gender_manual"]:
                combo.setToolTip("Género corregido a mano antes")
            self._combos[ch["id"]] = (combo, ch["gender"])
            grid.addWidget(combo, row, 2)
        lay.addLayout(grid)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Guardar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    @property
    def changes(self):
        """{character_id: nuevo_género} solo para los que cambiaron."""
        out = {}
        for cid, (combo, old) in self._combos.items():
            new = combo.currentData()
            if new != old:
                out[cid] = new
        return out
