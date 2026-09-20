"""Preview or apply the exact source-hashed multi-child derived-data closeout."""
import argparse, json
from sqlalchemy import text
from app.database import SessionLocal
from app.services import multi_child_closeout_service as service

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply-plan',default='')
    args=parser.parse_args()
    with SessionLocal() as db:
        if not args.apply_plan and db.get_bind().dialect.name=='postgresql':
            db.execute(text('SET TRANSACTION READ ONLY'))
        result=service.apply(db,args.apply_plan) if args.apply_plan else service.prepare(db)
        result.pop('source_fingerprints',None)
        print(json.dumps(result,ensure_ascii=False,default=str))

if __name__=='__main__':main()
