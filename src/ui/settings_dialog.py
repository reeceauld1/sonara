from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from core import youtube_auth


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)

        info = QLabel(
            "Import the OAuth client file (client_secret.json) you downloaded "
            "from Google Cloud Console for this app's Desktop app credential.\n\n"
            "See README.md for the full setup walkthrough."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.status_label = QLabel()
        self._refresh_status()
        layout.addWidget(self.status_label)

        row = QHBoxLayout()
        import_btn = QPushButton("Import client_secret.json")
        import_btn.clicked.connect(self._import_secrets)
        row.addWidget(import_btn)
        layout.addLayout(row)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def _refresh_status(self) -> None:
        if youtube_auth.has_client_secrets():
            self.status_label.setText("Client credentials: configured ✔")
        else:
            self.status_label.setText("Client credentials: not configured")

    def _import_secrets(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select client_secret.json", "", "JSON files (*.json)"
        )
        if not path:
            return
        try:
            youtube_auth.install_client_secrets(path)
        except OSError as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            return
        self._refresh_status()
        QMessageBox.information(self, "Imported", "Client credentials saved.")
