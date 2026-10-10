"""Workspace launcher: use the explicit 3.0.0 source, never the legacy root package."""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'general_model_registration' / 'src'))
sys.path.insert(0, str(HERE / 'src'))

if __name__ == '__main__':
    if '--package-check' in sys.argv:
        from chairside_compare.package_check import main
        main(sys.argv[sys.argv.index('--package-check')+1])
    else:
        from chairside_compare.app import main
        main()
