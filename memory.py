MAX_HISTORY_TURNS = 20


def trimmed_history(history: list[dict]) -> list[dict]:
    if len(history) > MAX_HISTORY_TURNS * 2:
        return history[-(MAX_HISTORY_TURNS * 2):]
    return history
