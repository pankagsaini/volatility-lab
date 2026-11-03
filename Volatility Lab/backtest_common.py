"""Shared lightweight helpers for backtest engines."""


def record_skip(skips, day, reason):
    skips.append({'Date': day, 'Reason': reason})
