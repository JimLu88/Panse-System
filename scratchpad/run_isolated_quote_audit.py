"""Load a candidate quote service into this process, then run a read-only audit script."""
from pathlib import Path
import runpy
import sys

from app.services import custom_quote_v2_service as target

CANDIDATE = Path("/tmp/custom_quote_v2_service.py")
AUDIT = Path(sys.argv[1] if len(sys.argv) > 1
             else "/tmp/custom_quote_sideboard_audit_10_20260721.py")

code = compile(CANDIDATE.read_text(encoding="utf-8"), str(CANDIDATE), "exec")
exec(code, target.__dict__)
runpy.run_path(str(AUDIT), run_name="__main__")
