"""python -m app.cli.business_relationships [--changed PATH ...] [--node ID]."""
import argparse
from datetime import datetime, timezone
import json
from app.services import business_relationship_service as service


def main():
    parser=argparse.ArgumentParser(description='只读业务影响审查；freeze仅保存审查后的本地源码指纹')
    parser.add_argument('--changed',nargs='+')
    parser.add_argument('--node')
    parser.add_argument('--field',help='例如 OrderDetail.qty；保守同名引用扫描，不等于精确类型血缘')
    parser.add_argument('--freeze-reviewed',action='store_true')
    parser.add_argument('--check',action='store_true',help='检查目录/来源指纹；不将未建模关系冒称已验证')
    args=parser.parse_args()
    if args.freeze_reviewed:
        if service.validate(): raise SystemExit('目录校验失败，不能锁定')
        sources=service.fingerprints()
        if any(v is None for v in sources.values()): raise SystemExit('缺来源文件，不能锁定')
        service.LOCK.write_text(json.dumps({'version':service.catalog.VERSION,
            'reviewed_at':datetime.now(timezone.utc).isoformat(),'sources':sources},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print('已显式记录源码指纹；不代表业务运行验收')
        return
    data=service.snapshot()
    if args.check:
        faults=[{'path':p,'state':v} for p,v in data['source_states'].items() if v!='matching']
        faults.extend({'id':n['id'],'source':ref} for n in data['nodes']+data['edges']
                      for ref in n['sources'] if ref['state']!='matching')
        print(json.dumps({'faults':faults,'validation_errors':data['validation_errors'],
                          'scan_errors':data['coverage']['errors'],
                          'unmapped_file_count':len(data['coverage']['unmapped_files']),
                          'runtime_verified':False},ensure_ascii=False))
        raise SystemExit(1 if faults or data['validation_errors'] or data['coverage']['errors'] else 0)
    result=(service.field_references(*args.field.split('.',1)) if args.field else
            service.change_report(args.changed,data=data) if args.changed else
            service.impact([args.node],data=data) if args.node else data)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
