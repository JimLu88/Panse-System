"""Explicit owner entry; default validates, --apply adopts/imports, never delivers."""
import argparse
import json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--attempt', required=True)
    parser.add_argument('--receipt-sha256', required=True)
    parser.add_argument('--source-job-id', required=True)
    parser.add_argument('--business-date', required=True)
    parser.add_argument('--previous-attempt', required=True)
    parser.add_argument('--apply', action='store_true')
    args = vars(parser.parse_args())
    from app.database import SessionLocal
    from app.services.order_job_adoption import adopt
    with SessionLocal() as db:
        result = adopt(db, **args)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
