"""Default read-only. Only the business owner invokes --apply with a reviewed manifest."""
import argparse
import json
from pathlib import Path
from app.database import SessionLocal
from app.services import factory_size_delivery_service as service


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--manifest')
    parser.add_argument('--output')
    args = parser.parse_args()
    if args.apply and not args.manifest:
        parser.error('--apply requires --manifest')
    with SessionLocal() as db:
        result = service.apply_manifest(db, json.loads(Path(args.manifest).read_text(encoding='utf-8-sig'))) if args.apply else service.scan(db)
    value = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(value + '\n', encoding='utf-8')
    print(value)


if __name__ == '__main__':
    main()
