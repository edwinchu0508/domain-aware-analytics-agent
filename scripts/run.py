"""Run from a source checkout without an editable install."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from domain_aware_analytics.cli import main
raise SystemExit(main())
