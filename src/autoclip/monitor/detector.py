"""Détection de pics d'activité du chat, relative à la moyenne propre à chaque streamer.

Logique pure (pas d'I/O) pour pouvoir la tester et régler les seuils hors ligne.
"""

from dataclasses import dataclass, field

# Emotes typiques d'un moment fort : elles pèsent plus qu'un message normal.
HYPE_TOKENS = {"KEKW", "LUL", "OMEGALUL", "PogChamp", "Pog", "POGGERS", "monkaS", "W", "LMAO", "😂", "💀"}


def message_weight(text: str) -> float:
    """1 point par message, +0.5 s'il contient une emote de hype (plafonné à 2)."""
    tokens = text.split()
    hype = sum(1 for t in tokens if t in HYPE_TOKENS)
    return min(1.0 + 0.5 * hype, 2.0)


@dataclass
class _ChannelState:
    ewma: float = 0.0
    samples: int = 0
    last_spike_at: float = float("-inf")


@dataclass
class SpikeDetector:
    """Compare l'activité d'une fenêtre (ex. 10 s) à une moyenne glissante exponentielle.

    - `ratio`     : seuil de déclenchement (activité / moyenne).
    - `alpha`     : vitesse d'adaptation de la moyenne (petit = mémoire longue).
    - `warmup`    : nombre de fenêtres à observer avant de pouvoir déclencher.
    - `cooldown_s`: pas de nouveau pic sur la même chaîne pendant ce délai (déduplication).
    - `min_activity`: plancher absolu, pour ignorer les pics sur des chats quasi vides.
    """

    ratio: float = 3.0
    alpha: float = 0.05
    warmup: int = 30
    cooldown_s: float = 150.0
    min_activity: float = 20.0
    _channels: dict[str, _ChannelState] = field(default_factory=dict)

    def seed(self, channel: str, ewma: float, samples: int | None = None) -> None:
        """Restaure une moyenne sauvegardée (évite de refaire le warmup après un redémarrage)."""
        self._channels[channel] = _ChannelState(ewma=ewma, samples=samples or self.warmup)

    def baseline(self, channel: str) -> float:
        return self._channels.get(channel, _ChannelState()).ewma

    def update(self, channel: str, activity: float, now: float) -> float | None:
        """Ajoute une fenêtre d'activité. Retourne le score (activité / moyenne) si c'est un pic."""
        st = self._channels.setdefault(channel, _ChannelState())

        if st.samples == 0:
            st.ewma = activity
            st.samples = 1
            return None

        score = activity / st.ewma if st.ewma > 0 else 0.0
        is_spike = (
            st.samples >= self.warmup
            and activity >= self.min_activity
            and score >= self.ratio
            and now - st.last_spike_at >= self.cooldown_s
        )

        if is_spike:
            st.last_spike_at = now
        else:
            # On n'intègre pas les pics à la moyenne, sinon elle monte et masque les suivants.
            st.ewma = (1 - self.alpha) * st.ewma + self.alpha * activity
        st.samples += 1

        return score if is_spike else None
