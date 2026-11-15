"""Build a portable source archive with Python's standard-library zipapp."""
import argparse
import os
from pathlib import Path
import shutil
import tempfile
import zipapp

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = '''"""Portable chemical-cabinet launcher. No dependencies are installed."""
import os
import runpy
import sys

if sys.version_info < (3, 9):
    sys.exit("chemical-cabinet needs Python 3.9 or newer.")
from chemical_cabinet import __version__

HELP = """chemical-cabinet portable archive
Usage: python chemical-cabinet.pyz COMMAND [arguments]
Commands: start, web, mcp, backup
Start locally and open a browser: python chemical-cabinet.pyz start
The start command uses chemical-cabinet-data in your home folder by default.
web and mcp require --data with an absolute folder path.
Example: python chemical-cabinet.pyz web --data /absolute/path/to/cabinet
Use COMMAND --help for that command's options. --version prints the version.
No login is provided. The web app defaults to localhost only.
"""
def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(HELP)
        return
    if args == ["--version"]:
        print(__version__)
        return
    modules = {"start": "chemical_cabinet.launcher", "web": "chemical_cabinet.app", "mcp": "chemical_cabinet.mcp",
               "backup": "chemical_cabinet.backup"}
    command, remaining = args[0], args[1:]
    if command not in modules:
        sys.exit("Unknown command. Use --help for start, web, mcp and backup.")
    if command in ("web", "mcp") and not any(x in ("-h", "--help") for x in remaining):
        # Validate the last value, matching argparse's duplicate-option behavior.
        data = None
        if any(x.startswith("--d") and x != "--data" and not x.startswith("--data=") for x in remaining):
            sys.exit("Use the full --data option with an absolute folder path.")
        for i, value in enumerate(remaining):
            if value == "--data":
                data = remaining[i + 1] if i + 1 < len(remaining) else None
            elif value.startswith("--data="):
                data = value.split("=", 1)[1]
        if not data or not os.path.isabs(data):
            sys.exit("Use --data with an absolute folder path; no data folder was created.")
    sys.argv = [sys.argv[0] + " " + command] + remaining
    runpy.run_module(modules[command], run_name="__main__")

main()
'''


def build(output, root=ROOT, overwrite=False):
    """Copy only package Python files plus LICENSE, never inventories or uploads."""
    root = Path(root).resolve()
    output = Path(output).absolute()
    if output.suffix != '.pyz':
        raise ValueError('Output must have a .pyz suffix.')
    package = root / 'chemical_cabinet'
    if package.is_symlink() or not package.is_dir():
        raise ValueError('The source package must be a real directory.')
    if package.resolve() in output.resolve().parents:
        raise ValueError('Output must be outside the source package.')
    if output.is_symlink() or (output.exists() and (not overwrite or not output.is_file())):
        raise ValueError('Output exists or is a symlink; choose a new path or use --overwrite for a regular archive.')
    license_path = root / 'LICENSE'
    if license_path.is_symlink() or not license_path.is_file():
        raise ValueError('A regular LICENSE file is required.')
    sources = []
    # Walk without following symlinks, rejecting them instead of silently omitting code.
    for directory, dirs, files in os.walk(package, followlinks=False):
        for name in dirs + files:
            if (Path(directory) / name).is_symlink():
                raise ValueError('Source package contains a symlink.')
        dirs[:] = [name for name in dirs if name != '__pycache__']
        for name in files:
            path = Path(directory) / name
            if path.suffix == '.py':
                sources.append(path)
    required = {'__init__.py', '__main__.py', 'app.py', 'db.py', 'mcp.py', 'backup.py'}
    if not required.issubset({str(p.relative_to(package)) for p in sources}):
        raise ValueError('The source package is incomplete.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cabinet-build-') as scratch:
        stage = Path(scratch) / 'source'
        stage.mkdir()
        for source in sources:
            target = stage / source.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        shutil.copyfile(license_path, stage / 'LICENSE')
        (stage / '__main__.py').write_text(LAUNCHER, encoding='utf-8')
        # Same-directory temporary output keeps final replacement atomic on one filesystem.
        fd, name = tempfile.mkstemp(prefix='.cabinet-', suffix='.pyz', dir=output.parent)
        os.close(fd)
        temporary = Path(name)
        try:
            zipapp.create_archive(stage, temporary, compressed=True)
            if output.is_symlink() or (output.exists() and (not overwrite or not output.is_file())):
                raise ValueError('Output appeared during build; not replaced.')
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / 'chemical-cabinet.pyz')
    parser.add_argument('--overwrite', action='store_true', help='replace an existing regular archive, not inventory data')
    args = parser.parse_args(argv)
    try:
        output = build(args.output, overwrite=args.overwrite)
    except (OSError, ValueError) as exc:
        parser.exit(2, 'Build failed: %s\n' % exc)
    print('Built %s. Run with Python; no data was included.' % output)


if __name__ == '__main__':
    main()
