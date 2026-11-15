"""Local research health command."""
import argparse
import json
from .settings import load_settings, ConfigurationError

def main(argv=None):
    parser = argparse.ArgumentParser(description='Inspect Volatility Lab local research data.')
    parser.add_argument('--health', action='store_true', help='Inspect feeds, archives and SQLite as JSON')
    args = parser.parse_args(argv)
    if not args.health:
        parser.print_help()
        return 0
    try:
        settings = load_settings()
    except ConfigurationError as error:
        parser.error(str(error))
    from .health import inspect
    report = inspect(settings)
    print(json.dumps(report, indent=2))
    return {'ok': 0, 'degraded': 1, 'error': 2}[report['status']]
