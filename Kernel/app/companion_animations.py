"""Apply a backend-authored pairing through the existing performance loader."""
from .. import host as host_port
from . import loading


def perform(context, rig, binding, seeds, companions, options):
    host = host_port.current()
    if host_port.CompanionAnimations not in host.capabilities:
        raise ValueError("This host has no companion animation capability.")
    total, lines = 0, []
    with host.companion_animations(context, rig, binding) as session:
        for index, seed in enumerate(seeds):
            count, said = loading.perform(context, [seed], rig=rig, options=options, activate=False)
            primary = session.capture(rig)
            total += count
            lines.extend(said)
            if index:
                continue
            companion = None
            counterpart = companions[seed]
            if counterpart and count == 1 and session.can_pair:
                count, said = loading.perform(context, [counterpart], rig=session.owner, options=options, activate=False)
                companion = session.capture(session.owner)
                if count != 1:
                    raise ValueError("The companion source must yield one action.")
                total += count
                lines.extend(said)
            elif not counterpart:
                lines.append("No unique companion; the owner's current action is retained.")
            elif not session.can_pair:
                lines.append("Active NLA; the owner's current action is retained.")
            session.synchronize(primary, companion)
    return total, lines
