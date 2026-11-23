from pathlib import Path
import sqlite3
import subprocess
import sys


def main():
    if sys.version_info < (3, 11) or sqlite3.sqlite_version_info < (3, 35):
        raise SystemExit('需要 Python 3.11+ 及 SQLite 3.35+')
    for folder in ('diegrade', 'tools'):
        for path in Path(folder).glob('*.py'):
            compile(path.read_bytes(), str(path), 'exec')
    for path in Path('web/js').glob('*.js'):
        subprocess.run(['node', '--input-type=module', '--check'], input=path.read_bytes(), check=True)


if __name__ == '__main__':
    main()
