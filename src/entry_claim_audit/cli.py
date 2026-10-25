"""Command-line interface."""
import argparse
import json
import sys
from . import __version__
from .audit import audit


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, f'{self.prog}: error: {message}\n')


def main(argv=None):
    parser = Parser(prog='entry-claim-audit')
    parser.add_argument('path', nargs='?', default='.')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--fail-on', choices=['HIGH', 'MEDIUM'], default='MEDIUM')
    parser.add_argument('--ignore', default='', metavar='RULE,...')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--version', action='version', version=__version__)
    args = parser.parse_args(argv)
    try:
        result = audit(args.path, args.fail_on, args.ignore.split(','))
    except (OSError, ValueError, SyntaxError) as exc:
        # Never echo source fragments or unrelated file contents.
        parser.exit(1, f'entry-claim-audit: unable to read or parse project ({type(exc).__name__})\n')
    if args.json:
        print(json.dumps(result))
    else:
        for f in result['findings']:
            print(f"{f['severity']} {f['rule']} {f['file']}:{f['line']} {f['message']}")
        if args.verbose:
            for script in result['verified_scripts']:
                print(f"verified {script['script']} ({script['table']}): {script['target']}")
        print(f"{result['status']}: {len(result['findings'])} finding(s)")
    return 2 if result['status'] == 'FAIL' else 0
