import logging

import pytest


@pytest.fixture(autouse=True)
def log_pulito():
    """main.configura_log aggiunge handler globali: li togliamo dopo ogni test."""
    radice = logging.getLogger()
    prima = list(radice.handlers)
    yield
    for h in list(radice.handlers):
        if h not in prima:
            radice.removeHandler(h)
            h.close()
