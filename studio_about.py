"""Native About screen and searchable, offline third-party acknowledgements."""
from __future__ import annotations

from html import escape
import json
from pathlib import Path
import sys

from PySide6.QtCore import QRectF, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                              QLineEdit, QListWidget, QListWidgetItem, QSplitter,
                              QPushButton, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

import studio_theme as T

SUPPORT_URL = 'https://ko-fi.com/aianddesign'


def asset_directory():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'assets/about'


def load_credits():
    return json.loads((asset_directory() / 'catalog.json').read_text(encoding='utf-8'))


def entry_html(entry):
    def text(key):
        return escape(str(entry.get(key, '')))
    result = f"<h2>{text('name')}</h2>"
    if entry.get('version'):
        result += f"<p>Version {text('version')}</p>"
    result += f"<p><b>{text('credit')}</b></p><p>{text('purpose')}</p>"
    result += f"<p>{text('category')}<br>License: {text('license')}</p>"
    for url in entry.get('urls', []):
        if QUrl(url).scheme() in ('https', 'http'):
            safe = escape(url, quote=True)
            result += f'<p><a href="{safe}">{safe}</a></p>'
    for notice in entry.get('notices', []):
        body = escape(notice['text']).replace('\n', '<br>')
        result += f"<hr><h3>{escape(notice['title'])}</h3><p>{body}</p>"
    if not entry.get('notices'):
        result += '<p>No separate notice text was supplied in this installation. The project link provides its published terms.</p>'
    return result


class LogoWidget(QWidget):
    """Preserve the supplied PNG and its aspect ratio on every display."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.source = QPixmap(str(asset_directory() / 'beatstitch.png'))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#101114'))
        if self.source.isNull():
            painter.setPen(Qt.GlobalColor.white)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'BEATSTITCH')
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        size = self.source.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        target = QRectF((self.width()-size.width())/2, (self.height()-size.height())/2,
                        size.width(), size.height())
        painter.drawPixmap(target, self.source, QRectF(self.source.rect()))


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('About BeatStitch')
        self.setModal(False)
        self.resize(880, 740)
        self.setMinimumSize(660, 540)
        self.setAccessibleName('About BeatStitch and third-party credits')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        about = QWidget()
        column = QVBoxLayout(about)
        column.setContentsMargins(24, 24, 24, 18)
        column.setSpacing(16)
        self.logo = LogoWidget()
        self.logo.setFixedHeight(230)
        self.logo.setAccessibleName('BeatStitch: the purple and amber spliced groove logo')
        self.logo.setStyleSheet('background: #101114; border-radius: 8px;')
        column.addWidget(self.logo)
        title = QLabel('Created by Marco van Hylckama Vlieg')
        title.setWordWrap(True)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(T.font(22, QFont.Weight.DemiBold))
        column.addWidget(title)
        description = QLabel('A desktop studio for making EDM mixes.\nYour music. Your transitions. Your decisions.')
        description.setWordWrap(True)
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description.setFont(T.font(16))
        column.addWidget(description)
        thanks = QLabel('Built with the work of open-source audio, research, and software communities.\n'
                        'Explore Credits & licenses for the projects, contributors, and original notices.')
        thanks.setWordWrap(True)
        thanks.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thanks.setFont(T.font(14))
        thanks.setStyleSheet(f'color: {T.TEXT_2};')
        column.addWidget(thanks)
        self.support_button = QPushButton('If you like this tool, consider buying me a coffee.')
        self.support_button.setAutoDefault(False)
        self.support_button.setToolTip('Optional support for Marco. Opens the Ko-fi page in your browser. Donations do not unlock features.')
        self.support_button.clicked.connect(lambda: self.open_project_link(QUrl(SUPPORT_URL)))
        column.addWidget(self.support_button, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addStretch(1)
        self.tabs.addTab(about, 'About')

        credits = QWidget()
        credit_layout = QVBoxLayout(credits)
        credit_layout.setContentsMargins(10, 14, 10, 10)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Find a project, contributor, feature, or license…')
        self.search.setAccessibleName('Search third-party credits')
        self.search.setToolTip('Search included libraries, model credits, optional features, and build tools. All notices are available offline.')
        self.search.setClearButtonEnabled(True)
        credit_layout.addWidget(self.search)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setFont(T.font(13))
        credit_layout.addWidget(self.summary)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.list = QListWidget()
        self.list.setAccessibleName('Credited projects and libraries')
        self.list.setMinimumWidth(190)
        self.list.setFont(T.font(14))
        self.list.setStyleSheet('QListWidget::item { padding: 7px 6px; }')
        splitter.addWidget(self.list)
        self.details = QTextBrowser()
        self.details.setAccessibleName('Project credit and full license notices')
        self.details.setFont(T.font(14))
        self.details.setOpenLinks(False)
        self.details.setOpenExternalLinks(False)
        self.details.anchorClicked.connect(self.open_project_link)
        self.details.document().setDefaultStyleSheet(
            f'a {{ color: {T.ACCENT}; }} h2 {{ font-size: 22px; }} '
            'h3 { font-size: 15px; } p { line-height: 125%; }')
        splitter.addWidget(self.details)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([230, 560])
        credit_layout.addWidget(splitter, 1)
        self.tabs.addTab(credits, 'Credits && licenses')
        self.list.currentItemChanged.connect(self.select_entry)
        self.search.textChanged.connect(self.filter_entries)

        try:
            self.catalog = load_credits()
            self.entries = self.catalog['entries']
        except (OSError, ValueError, KeyError):
            self.catalog = {}
            self.entries = []
            self.summary.setText('The credits catalog is missing. Reinstall the app to restore its offline notices.')
        self.filter_entries('')
        footer = QHBoxLayout()
        self.build_label = QLabel('Development build · GPL v3 or later')
        self.build_label.setFont(T.font(13))
        self.build_label.setStyleSheet(f'color: {T.TEXT_2};')
        footer.addWidget(self.build_label)
        footer.addStretch(1)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.close)
        close.button(QDialogButtonBox.StandardButton.Close).setAutoDefault(False)
        footer.addWidget(close)
        layout.addLayout(footer)

    def filter_entries(self, query):
        words = query.casefold().split()
        self.list.clear()
        for index, entry in enumerate(self.entries):
            haystack = ' '.join(str(entry.get(k, '')) for k in ('name', 'credit', 'category', 'license', 'purpose')).casefold()
            if not all(word in haystack for word in words):
                continue
            item = QListWidgetItem(entry['name'])
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setToolTip(entry['category'] + ' · ' + entry.get('purpose', ''))
            self.list.addItem(item)
        if self.entries:
            self.summary.setText(f'{self.list.count()} of {len(self.entries)} credits. '
                                 'Optional features and build tools are labeled; notices include upstream component credits.')
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self.details.setPlainText('No matching credits. Try a project name such as Rubber Band, OpenKeyScan, or Qt.'
                                      if self.entries else 'The offline credits catalog is unavailable.')

    def select_entry(self, item, previous=None):
        if item is not None:
            self.details.setHtml(entry_html(self.entries[item.data(Qt.ItemDataRole.UserRole)]))
            self.details.verticalScrollBar().setValue(0)

    @staticmethod
    def open_project_link(url):
        if url.scheme() in ('https', 'http'):
            QDesktopServices.openUrl(url)
