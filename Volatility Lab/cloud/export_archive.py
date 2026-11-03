"""Export one UTC/IST-labelled archive day using an authenticated AWS SDK session.

Run in AWS CloudShell or with an existing local AWS profile. Never embeds credentials.
Example: python export_archive.py --table TABLE --day 2026-10-16 --region ap-southeast-2
Output is a ZIP of original broker responses, catalogues and capture reports.
"""
import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from collector import Archive

def export(table, day, destination):
    from boto3.dynamodb.conditions import Key
    date.fromisoformat(day)
    archive=Archive(table)
    rows=[]; cursor=None
    while True:
        options={'KeyConditionExpression':Key('pk').eq(day), 'ConsistentRead':True,
                 'ProjectionExpression':'pk, sk, sha256, parts'}
        if cursor: options['ExclusiveStartKey']=cursor
        page=table.query(**options)
        rows.extend(r for r in page['Items'] if not r['sk'].startswith('blob|'))
        cursor=page.get('LastEvaluatedKey')
        if not cursor:break
    if not rows:raise RuntimeError('No saved capture for this date')
    destination=Path(destination)
    temp=destination.with_suffix('.partial')
    manifest=[]
    try:
        with ZipFile(temp,'w',ZIP_DEFLATED) as out:
            for row in rows:
                value=archive.read(day,row['sk'])
                name=hashlib.sha256(row['sk'].encode()).hexdigest()+'.json'
                content=json.dumps(value,separators=(',',':'),allow_nan=False).encode()
                out.writestr(name,content)
                manifest.append({'key':row['sk'],'file':name,'sha256':hashlib.sha256(content).hexdigest()})
            out.writestr('manifest.json',json.dumps({'day':day,'provider':'Upstox','objects':manifest},indent=2))
        temp.replace(destination)
    finally:
        temp.unlink(missing_ok=True)
    return len(manifest)

if __name__=='__main__':
    import boto3
    parser=argparse.ArgumentParser()
    parser.add_argument('--table',required=True);parser.add_argument('--day',required=True)
    parser.add_argument('--region',default='ap-southeast-2');parser.add_argument('--output')
    args=parser.parse_args()
    target=args.output or ('upstox-'+date.fromisoformat(args.day).isoformat()+'.zip')
    table=boto3.resource('dynamodb',region_name=args.region).Table(args.table)
    print('Exported',export(table,args.day,target),'verified objects to',target)
