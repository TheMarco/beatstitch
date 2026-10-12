"""Temporary listening decisions, keyed to the sound the user actually heard."""
from dataclasses import dataclass, field

import djmix
from studio_render_cache import listening_recipe


def blend_pairs(session):
    return [(a.id, b.id) for a, b in zip(session.clips, session.clips[1:])]


@dataclass
class ReviewWalk:
    kept: dict = field(default_factory=dict)
    queue: list = field(default_factory=list)
    current: tuple | None = None
    active: bool = False
    started: bool = False
    message: str = ''

    def reconcile(self, session):
        pairs = blend_pairs(session)
        previous = len(self.kept)
        for pair, fingerprint in list(self.kept.items()):
            try:
                index = pairs.index(pair) + 1
                valid = listening_recipe(session, 'transition', index)['fingerprint'] == fingerprint
            except (ValueError, OSError, djmix.MixError):
                valid = False
            if not valid:
                del self.kept[pair]
        self.queue = [pair for pair in self.queue if pair in pairs]
        if self.current is not None and self.current not in pairs:
            self.stop('Song order changed. Review the remaining blends.')
        return previous - len(self.kept)

    def start(self, session, remaining=False):
        self.reconcile(session)
        if not remaining:
            self.kept.clear()
        self.queue = [pair for pair in blend_pairs(session) if not remaining or pair not in self.kept]
        self.active, self.started, self.current = True, True, None
        self.message = ''

    def advance(self):
        if not self.active:
            return None
        if not self.queue:
            self.stop('Review finished.')
            return None
        self.current = self.queue.pop(0)
        self.message = ''
        return self.current

    def stop(self, message='Review paused.'):
        self.active = False
        self.current = None
        self.queue.clear()
        self.message = message

    def index(self, session):
        try:
            return blend_pairs(session).index(self.current) + 1
        except ValueError:
            return None
