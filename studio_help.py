"""Searchable offline guide rendered by native Qt text widgets."""
import sys

from PySide6.QtCore import QRect, QSize, QSignalBlocker, Qt
from PySide6.QtGui import QKeySequence, QShortcut, QTextBlockFormat, QTextCursor
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                              QListWidgetItem, QPushButton, QSplitter, QTextBrowser, QVBoxLayout)

import studio_theme as T
from studio_help_content import TOPICS
from studio_help_course import COURSE_IDS


def platform_text(text):
    return text.replace("{MOD}", "Cmd" if sys.platform == "darwin" else "Ctrl").replace(
        "{ALT}", "Option" if sys.platform == "darwin" else "Alt").replace(
        "{REDO}", QKeySequence(QKeySequence.StandardKey.Redo).toString(QKeySequence.SequenceFormat.NativeText))


def matching_topics(query):
    words = query.casefold().split()
    return [topic for topic in TOPICS if all(word in platform_text(
        " ".join(topic[key] for key in ("title", "keywords", "body"))).casefold() for word in words)]


class HelpTopicList(QListWidget):
    """Give wrapped titles enough row height, including after splitter resizing."""
    def fit_titles(self):
        width = max(1, self.viewport().width() - 40)
        for index in range(self.count()):
            item = self.item(index)
            bounds = self.fontMetrics().boundingRect(
                QRect(0, 0, width, 1000), Qt.TextFlag.TextWordWrap, item.text())
            item.setSizeHint(QSize(0, max(2 * self.fontMetrics().lineSpacing(), bounds.height()) + 20))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_titles()


class HelpDialog(QDialog):
    def __init__(self, parent=None, listen_examples=None):
        super().__init__(parent)
        self.setWindowTitle("DJ Studio guide")
        self.setModal(False)
        self.resize(920, 660)
        self.setMinimumSize(740, 470)
        self.topics = {topic["id"]: topic for topic in TOPICS}
        self.current_topic = None
        self.reading_positions = {}
        self.history, self.history_index = [], -1
        column = QVBoxLayout(self)
        column.setContentsMargins(18, 16, 18, 16)
        column.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel("DJ Studio guide")
        title.setFont(T.font(22))
        header.addWidget(title)
        header.addStretch(1)
        self.course_button = QPushButton('Beginner course')
        self.course_button.setToolTip('Open the step-by-step course: exercises, listening checks and links to the full reference.')
        self.course_button.setAccessibleDescription(self.course_button.toolTip())
        self.course_button.setAutoDefault(False)
        self.course_button.clicked.connect(lambda: self.open_topic('beginner-course'))
        header.addWidget(self.course_button)
        if listen_examples is not None:
            examples = QPushButton('Listening examples…')
            examples.setToolTip('Hear drifting beats, clashing notes and a bass handoff in short A/B examples. Your arrangement stays unchanged.')
            examples.setAccessibleDescription(examples.toolTip())
            examples.setAutoDefault(False); examples.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            examples.clicked.connect(listen_examples); header.addWidget(examples)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        close.setAutoDefault(False)
        header.addWidget(close)
        column.addLayout(header)
        hint = QLabel("Keep this guide open while you mix. Use the ? buttons in the app for help with a section.")
        hint.setWordWrap(True)
        hint.setProperty("role", "secondary")
        column.addWidget(hint)
        navigation = QHBoxLayout()
        self.back_btn = QPushButton("Back")
        self.forward_btn = QPushButton("Forward")
        self.back_btn.setToolTip('Return to the previous topic at your saved reading position.')
        self.forward_btn.setToolTip('Revisit the next topic at your saved reading position.')
        for widget, step in ((self.back_btn, -1), (self.forward_btn, 1)):
            widget.setAutoDefault(False)
            widget.setAccessibleDescription(widget.toolTip())
            widget.clicked.connect(lambda checked=False, direction=step: self.go(direction))
            navigation.addWidget(widget)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search topics, controls, or questions…")
        self.search.setAccessibleName("Search the DJ Studio guide")
        self.search.setToolTip("Search all help text. Try stabilize, BPM, key, preview, AirPods, or export.")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter_topics)
        navigation.addWidget(self.search, 1)
        column.addLayout(navigation)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.topic_list = HelpTopicList()
        self.topic_list.setFont(T.font(14))
        self.topic_list.setAccessibleName("Help topics")
        self.topic_list.setMinimumWidth(185)
        self.topic_list.setWordWrap(True)
        self.topic_list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.topic_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.topic_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.topic_list.setStyleSheet(f"QListWidget {{ background: {T.FIELD}; border: 1px solid {T.LINE_STRONG}; }}"
                                     f"QListWidget::item {{ padding: 9px 7px; }}"
                                     f"QListWidget::item:selected {{ background: {T.SELECTION}; color: {T.TEXT}; }}")
        self.topic_list.currentItemChanged.connect(self.topic_selected)
        split.addWidget(self.topic_list)
        self.browser = QTextBrowser()
        self.browser.setAccessibleName("Help article")
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.anchorClicked.connect(self.follow_link)
        self.browser.setFont(T.font(14))
        self.browser.setStyleSheet(f"QTextBrowser {{ background: {T.BASE}; border: 1px solid {T.LINE_STRONG}; padding: 14px; }}")
        self.browser.document().setDefaultStyleSheet(
            f"h1 {{ font-size: 24px; }} h2 {{ font-size: 18px; }} h3 {{ font-size: 15px; }} "
            f"a {{ color: {T.ACCENT}; }} p, li {{ line-height: 135%; }}")
        split.addWidget(self.browser)
        split.setChildrenCollapsible(False)
        split.setStretchFactor(1, 1)
        split.setSizes([215, 665])
        column.addWidget(split, 1)
        self.course_navigation = QHBoxLayout()
        self.previous_lesson = QPushButton('Previous lesson')
        self.next_lesson = QPushButton('Next lesson')
        self.lesson_position = QLabel()
        self.lesson_position.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lesson_position.setAccessibleName('Position in beginner course')
        for button, step in ((self.previous_lesson, -1), (self.next_lesson, 1)):
            button.setAutoDefault(False)
            button.setToolTip('Read the adjacent course lesson. This does not edit the mix or start playback.')
            button.setAccessibleDescription(button.toolTip())
            button.clicked.connect(lambda checked=False, direction=step: self.move_lesson(direction))
        self.course_navigation.addWidget(self.previous_lesson)
        self.course_navigation.addWidget(self.lesson_position, 1)
        self.course_navigation.addWidget(self.next_lesson)
        column.addLayout(self.course_navigation)
        self.results = QLabel()
        self.results.setProperty("role", "secondary")
        column.addWidget(self.results)
        QShortcut(QKeySequence.StandardKey.Find, self, activated=self.focus_search)
        self.filter_topics("")
        self.open_topic('beginner-course')
        for button in self.findChildren(QPushButton):
            button.setMinimumHeight(30)

    def focus_search(self):
        self.search.setFocus()
        self.search.selectAll()

    def populate(self, topics):
        with QSignalBlocker(self.topic_list):
            self.topic_list.clear()
            for topic in topics:
                item = QListWidgetItem(topic["title"])
                item.setData(Qt.ItemDataRole.UserRole, topic["id"])
                item.setToolTip(topic['title'])
                self.topic_list.addItem(item)
            self.topic_list.fit_titles()
        self.results.setText(f"{len(topics)} {'topic' if len(topics) == 1 else 'topics'} · "
                             + platform_text("{MOD}+F to search · Esc to close"))

    def filter_topics(self, query):
        matches = matching_topics(query)
        self.populate(matches)
        if matches:
            identity = next((t["id"] for t in matches if t["id"] == self.current_topic), matches[0]["id"])
            self.open_topic(identity, remember=False, clear_search=False)
        else:
            self.save_reading_position()
            self.current_topic = None
            self.update_course_navigation()
            self.browser.setMarkdown("# No matching topics\n\nTry a shorter search, such as **tempo**, **key**, "
                                     "**transition**, **stabilize**, or **export**. Clear the search to show every topic.")

    def topic_selected(self, item, previous):
        if item:
            self.open_topic(item.data(Qt.ItemDataRole.UserRole), clear_search=False)

    def open_topic(self, identity, remember=True, clear_search=True):
        if identity not in self.topics:
            return
        # Search previews do not fill history for every keystroke. Once the
        # reader follows a link, keep the visible search result as the origin.
        if remember and self.current_topic is not None and (
                self.history_index < 0 or self.history[self.history_index] != self.current_topic):
            self.history = self.history[:self.history_index + 1] + [self.current_topic]
            self.history_index = len(self.history) - 1
        self.save_reading_position()
        if clear_search and self.search.text():
            with QSignalBlocker(self.search):
                self.search.clear()
            self.populate(TOPICS)
        with QSignalBlocker(self.topic_list):
            for index in range(self.topic_list.count()):
                if self.topic_list.item(index).data(Qt.ItemDataRole.UserRole) == identity:
                    self.topic_list.setCurrentRow(index)
                    break
        topic = self.topics[identity]
        self.current_topic = identity
        self.update_course_navigation()
        self.browser.setMarkdown(platform_text(f"# {topic['title']}\n\n{topic['body']}"))
        block = self.browser.document().begin()
        while block.isValid():
            cursor = QTextCursor(block)
            formatting = block.blockFormat()
            formatting.setLineHeight(125, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            if cursor.currentTable() is None:
                heading = formatting.headingLevel()
                formatting.setTopMargin(12 if heading and block.position() else 0)
                formatting.setBottomMargin(6 if heading else 4 if block.textList() else 10)
            cursor.setBlockFormat(formatting)
            block = block.next()
        self.browser.verticalScrollBar().setValue(self.reading_positions.get(identity, 0))
        if remember and (self.history_index < 0 or self.history[self.history_index] != identity):
            self.history = self.history[:self.history_index + 1] + [identity]
            self.history_index = len(self.history) - 1
        self.back_btn.setEnabled(self.history_index > 0)
        self.forward_btn.setEnabled(self.history_index + 1 < len(self.history))

    def save_reading_position(self):
        if self.current_topic is not None:
            self.reading_positions[self.current_topic] = self.browser.verticalScrollBar().value()

    def follow_link(self, url):
        if url.scheme() == "help":
            self.open_topic(url.path())

    def update_course_navigation(self):
        index = COURSE_IDS.index(self.current_topic) if self.current_topic in COURSE_IDS else -1
        for widget in (self.previous_lesson, self.lesson_position, self.next_lesson):
            widget.setVisible(index >= 0)
        self.previous_lesson.setEnabled(index > 0)
        self.next_lesson.setEnabled(0 <= index < len(COURSE_IDS) - 1)
        self.lesson_position.setText(f'Lesson {index + 1} of {len(COURSE_IDS)}' if index >= 0 else '')

    def move_lesson(self, direction):
        if self.current_topic in COURSE_IDS:
            index = COURSE_IDS.index(self.current_topic) + direction
            if 0 <= index < len(COURSE_IDS):
                self.open_topic(COURSE_IDS[index])

    def go(self, direction):
        index = self.history_index + direction
        if 0 <= index < len(self.history):
            self.history_index = index
            self.open_topic(self.history[index], remember=False)
