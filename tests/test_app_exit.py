"""Sauberes Programmende: `release_widgets` löscht übrig gebliebene Fenster, bevor PySide beim Interpreter-Ende
aufräumt (PySide 6.12 stürzte dort gelegentlich ab – Fenster samt Kindern vor den Kindern gelöscht)."""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

SCRIPT = textwrap.dedent("""
    from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QPushButton, QVBoxLayout
    from notex.app import release_widgets
    app = QApplication([])
    keep = []
    for _ in range(20):                          # Dialoge ohne Eltern, Kinder per Layout, Zyklus über Lambda
        dialog = QDialog()
        field, button = QLineEdit(), QPushButton("x")
        layout = QVBoxLayout(dialog)
        layout.addWidget(field)
        layout.addWidget(button)
        field.textEdited.connect(lambda _t, d=dialog: d.setWindowTitle("y"))
        keep.append(dialog)
    release_widgets(app)
    print("deleted", sum(1 for d in keep if not __import__("shiboken6").Shiboken.isValid(d)))
""")


def test_release_widgets_deletes_top_levels_and_exits_cleanly():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    result = subprocess.run([sys.executable, "-c", SCRIPT], env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    assert "deleted 20" in result.stdout
