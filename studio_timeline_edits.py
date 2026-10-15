"""Reversible, visual-only drag previews; the owner commits once on release."""
from dataclasses import replace

from PySide6.QtCore import QRectF, Qt

import djmix
from studio_engine import layout, overlap_limit
from studio_fades import duration, set_duration


class TimelineEdits:
    def __init__(self, view):
        self.view = view
        self.state = None

    def reorder_grip(self, index):
        rect = self.view.clip_rect(self.view.positions[index], index)
        return QRectF(max(0., rect.left()) + 3, rect.top(), 14, self.view.CLIP_HEADER)

    def fade_handles(self):
        v = self.view
        if not 0 < v.transition < len(v.positions): return []
        owner = v.session.clips[v.transition]
        start = v.positions[v.transition]['start'] / djmix.SR
        end = v.positions[v.transition - 1]['end'] / djmix.SR
        handles = []
        for incoming in (False, True):
            index = v.transition if incoming else v.transition - 1
            rect = v.clip_rect(v.positions[index], index)
            span = duration(owner, incoming) * 240 / v.session.target_bpm
            x = v.x(start + span if incoming else end - span)
            y = rect.top() + v.CLIP_HEADER + 4
            handles.append((QRectF(x - 6, y - 6, 12, 12), v.transition, incoming))
        return handles

    def _begin(self, kind, index, event, incoming=None):
        v = self.view
        v.selected.emit(v.session.clips[index].id)
        v.setFocus(Qt.FocusReason.MouseFocusReason)
        clip = v.session.clips[index]
        self.state = dict(kind=kind, index=index, incoming=incoming, x=event.position().x(),
                          session=v.session, positions=v.positions[:], moved=False,
                          original=duration(clip, incoming) if kind == 'fade' else clip.blend_bars,
                          value=duration(clip, incoming) if kind == 'fade' else clip.blend_bars)
        v.drag_origin = v.drag_clip = None

    def press_fade(self, event):
        for rect, index, incoming in self.fade_handles():
            if rect.contains(event.position()):
                self._begin('fade', index, event, incoming)
                return True
        return False

    def press_clip(self, event):
        v = self.view
        for index, pos in enumerate(v.positions):
            if not v.clip_rect(pos, index).contains(event.position()): continue
            if self.reorder_grip(index).contains(event.position()): return False
            self._begin('move' if index else 'anchor', index, event)
            return True
        return False

    def move(self, event):
        s, v = self.state, self.view
        if s is None: return False
        delta = event.position().x() - s['x']
        if not s['moved'] and abs(delta) < 5: return True
        s['moved'] = True
        if s['kind'] == 'anchor':
            v.editStatus.emit('The first song anchors the start of the mix. Move its title-bar grip to reorder it.')
            return True
        bar_pixels = 240 / s['session'].target_bpm * v.scale
        if s['kind'] == 'move':
            maximum = overlap_limit(s['session'], s['index'], s['positions'])['bars']
            requested = round(s['original'] - delta / bar_pixels)
            value = max(1, min(maximum, requested))
            if requested != value:
                v.editStatus.emit(f'This entry supports 1–{maximum} bars of overlap with the current cues and protected passages.')
        else:
            sign = 1 if s['incoming'] else -1
            value = max(.25, min(s['session'].clips[s['index']].blend_bars,
                                round((s['original'] + sign * delta / bar_pixels) * 4) / 4))
        if value == s['value']: return True
        s['value'] = value
        preview = replace(s['session'], clips=[replace(c) for c in s['session'].clips])
        clip = preview.clips[s['index']]
        if s['kind'] == 'move':
            clip.blend_bars = int(value)
            message = f'Overlap {value:g} bars · later songs follow · release to keep, Escape to cancel'
        else:
            set_duration(clip, s['incoming'], value)
            side = 'Incoming' if s['incoming'] else 'Outgoing'
            message = f'{side} fade {value:g} bars' + (' · fades linked' if clip.fades_linked else '')
        v.session = preview
        v.positions = layout(preview)
        v.invalidate()
        v.viewChanged.emit()
        v.editStatus.emit(message)
        v.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        return True

    def _restore(self):
        s, v = self.state, self.view
        v.session, v.positions = s['session'], s['positions']
        self.state = None
        v.invalidate()
        v.viewport().unsetCursor()
        v.viewChanged.emit()
        return s

    def release(self, event):
        if self.state is None: return False
        s = self._restore()
        if not s['moved'] or s['value'] == s['original']: return True
        if s['kind'] == 'move': self.view.overlapChanged.emit(s['index'], int(s['value']))
        elif s['kind'] == 'fade': self.view.fadeChanged.emit(s['index'], s['incoming'], s['value'])
        return True

    def cancel(self):
        if self.state is None: return False
        self._restore()
        self.view.editStatus.emit('Drag cancelled. Mix unchanged.')
        return True

    def hover(self, point):
        v = self.view
        for rect, _, incoming in self.fade_handles():
            if rect.contains(point):
                v.viewport().setCursor(Qt.CursorShape.SizeHorCursor)
                v.viewport().setToolTip(('Incoming' if incoming else 'Outgoing') +
                    ' fade: drag this white handle to change its length. Snaps to beats. '
                    'Link fades and curve choices are under Fades…. Escape cancels.')
                return True
        for index, pos in enumerate(v.positions):
            if v.clip_rect(pos, index).contains(point) and self.reorder_grip(index).contains(point):
                v.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
                v.viewport().setToolTip('Drag this grip to reorder songs. Drag the waveform to move its entry time.')
                return True
        return False
