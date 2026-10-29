"""Model and effort selection from the fixed bridge catalog."""

from __future__ import annotations

from PySide6.QtCore import (
    QSize,
    Qt,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
)
from PySide6.QtWidgets import (
    QMenu,
)

from guard_app.services.bridge import (
    CODEX_DEFAULT_EFFORT,
    CODEX_MODEL_ID,
    CODEX_MODELS,
    CODEX_REASONING_EFFORTS,
)

from .presentation import (
    EFFORT_NAMES,
    MODEL_NAMES,
)
from .untrusted_text import untrusted_display_text
from .widgets import KeyboardActionButton, plain_tooltip


class ModelPicker(KeyboardActionButton):
    """Composer chip: the session's model and reasoning level, from fixed lists.

    The model is chosen before a Task starts and then belongs to that Task's
    thread; the reasoning level goes with each message from then on. Neither
    comes from model output.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("modelChip")
        self.set_visual(icon="chevron_down")
        self.setIconSize(QSize(12, 12))
        # Chevron after the text; the menu itself reads left to right.
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.model = CODEX_MODEL_ID
        self.effort = CODEX_DEFAULT_EFFORT
        self._fixed: str | None = None
        self._applied: str | None = None
        self._locked = False
        menu = QMenu(self)
        menu.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.model_actions = self._group(
            menu, "모델", {model: MODEL_NAMES.get(model, model) for model in CODEX_MODELS}
        )
        self.effort_actions = self._group(
            menu, "추론 수준", {effort: EFFORT_NAMES[effort] for effort in CODEX_REASONING_EFFORTS}
        )
        self.model_actions[self.model].setChecked(True)
        self.effort_actions[self.effort].setChecked(True)
        self.setMenu(menu)
        self._render()

    def _group(self, menu: QMenu, title: str, names: dict[str, str]) -> dict[str, QAction]:
        # A disabled heading: styled menus drop addSection's text.
        if menu.actions():
            menu.addSeparator()
        menu.addAction(title).setEnabled(False)
        group = QActionGroup(menu)
        actions = {}
        for value, name in names.items():
            action = menu.addAction(name)
            action.setCheckable(True)
            group.addAction(action)
            action.triggered.connect(lambda _=False, v=value: self._pick(v))
            actions[value] = action
        return actions

    def _pick(self, value: str) -> None:
        if value in self.model_actions:
            self.model = value
        else:
            self.effort = value
        self._render()

    @property
    def message_effort(self) -> str | None:
        """The level to send with a message; None when the model has no levels."""
        return None if self._fixed is not None else self.effort

    def set_model_locked(self, locked: bool, model_id: str | None = None) -> None:
        """A started Codex Task keeps its model; only the reasoning level changes."""
        self._fixed = None
        self._locked = locked
        self._applied = model_id
        for action in self.model_actions.values():
            action.setEnabled(not locked)
        self.setEnabled(True)
        self._render()

    def set_fixed(self, text: str) -> None:
        """A non-Codex (synthetic) model: shown, nothing to pick."""
        self._fixed = text
        self.setEnabled(False)
        self._render()

    def _render(self) -> None:
        if self._fixed is not None:
            self.setText(self._fixed)
            self.setAccessibleName(f"세션 {self._fixed}")
            self.setToolTip("")
            return
        value = self._applied if self._locked else self.model
        model = untrusted_display_text(MODEL_NAMES.get(value, value) if value else "모델 확인 전")[
            0
        ]
        effort = EFFORT_NAMES[self.effort]
        self.setText(f"{model} · 추론 {effort}")
        self.setAccessibleName(f"세션 모델 {model} · 추론 수준 {effort} · 눌러서 바꾸기")
        tooltip = "세션을 시작할 때 이 모델을 요청함"
        if self._locked:
            tooltip = "추론 수준은 다음 메시지부터 적용됨"
            if self.model != self._applied:
                applied = untrusted_display_text(self._applied or "모델 확인 전")[0]
                tooltip += f"\n요청: {self.model}, 적용: {applied}"
        self.setToolTip(plain_tooltip(tooltip))
