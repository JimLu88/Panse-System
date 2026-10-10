"""Default read-only. --apply-two-confirmed-months is the exact 2026-09-09 user scope."""
import argparse,json
from sqlalchemy import text
from app.database import SessionLocal
from app.services.factory_shipping_confirmation_service import apply_confirmed_months,read_confirmed_months

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply-two-confirmed-months',action='store_true')
    args=parser.parse_args()
    with SessionLocal() as db:
        try:
            if args.apply_two_confirmed_months:
                result=apply_confirmed_months(db)
            else:
                if db.get_bind().dialect.name=='postgresql':db.execute(text('SET TRANSACTION READ ONLY'))
                result=read_confirmed_months(db)
                db.rollback()
            print(json.dumps(result,ensure_ascii=False,default=str))
            return 0 if result.get('ok',result.get('all_confirmed')) else 1
        except Exception as exc:
            db.rollback()
            print(json.dumps({'ok':False,'error_type':type(exc).__name__,'error':'Exact month correction or readback failed; no blind retry'},ensure_ascii=False))
            return 1

if __name__=='__main__':raise SystemExit(main())
