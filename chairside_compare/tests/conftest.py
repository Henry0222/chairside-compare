from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent/'general_model_registration'/'src'))
sys.path.insert(0,str(ROOT/'src'))
