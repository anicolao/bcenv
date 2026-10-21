#!/usr/bin/env python3
"""Client for a cooperative public-development trial; no evaluator paths needed."""
import argparse
import base64
import io
import json
import os
import re
import stat
import time
import uuid
from pathlib import Path
import urllib.request
import urllib.error
import urllib.parse
import zipfile


def source_zip(root, package):
    root = Path(root).resolve()
    paths = sorted(root.rglob('*'))
    if any(p.is_symlink() for p in paths):
        raise ValueError('Source symlinks are not supported')
    files = [p for p in paths if p.is_file()]
    if not 1 <= len(files) <= 128 or any(p.suffix != '.java' for p in files):
        raise ValueError('Source directory must contain 1–128 Java files only')
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            if path.stat().st_size > 524288:
                raise ValueError('Source file too large')
            info = zipfile.ZipInfo(path.relative_to(root).as_posix(), (1980,1,1,0,0,0))
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    return output.getvalue()



def mailbox_request(mailbox, body, timeout=150):
    """One request, no automatic retry after an uncertain outcome."""
    mailbox = Path(mailbox).absolute()
    for directory in (mailbox, mailbox/'requests', mailbox/'responses'):
        for component in (directory, *directory.parents):
            if component.is_symlink():
                raise ValueError('Mailbox symlinks are not supported')
        if not directory.is_dir():
            raise ValueError('Mailbox is not ready')
    if (mailbox/'stopped.json').exists():
        raise ValueError('Mailbox is stopped')
    payload = json.dumps(body).encode()
    if len(payload)>1500000:
        raise ValueError('Request limit exceeded')
    name=uuid.uuid4().hex
    temporary=mailbox/'requests'/(name+'.tmp')
    with temporary.open('xb') as handle:
        handle.write(payload)
    os.replace(temporary,mailbox/'requests'/(name+'.json'))
    response=mailbox/'responses'/(name+'.json')
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            fd=os.open(response,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        except FileNotFoundError:
            time.sleep(.05);continue
        with os.fdopen(fd,'rb') as handle:
            metadata=os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode) or not 0<metadata.st_size<=65536:
                raise ValueError('Response limit exceeded')
            data=handle.read(65537)
        if len(data)!=metadata.st_size:
            raise ValueError('Response limit exceeded')
        return json.loads(data)
    raise TimeoutError('Request outcome uncertain; do not automatically resubmit. Request ID: '+name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    route=parser.add_mutually_exclusive_group(required=True)
    route.add_argument('--endpoint')
    route.add_argument('--mailbox')
    subs = parser.add_subparsers(dest='operation', required=True)
    submit = subs.add_parser('submit'); submit.add_argument('--source', required=True); submit.add_argument('--package', required=True)
    result = subs.add_parser('result'); result.add_argument('--submission', required=True)
    replay = subs.add_parser('replay'); replay.add_argument('--submission', required=True); replay.add_argument('--game', type=int, required=True)
    replay.add_argument('--start', type=int, required=True); replay.add_argument('--end', type=int, required=True)
    replay.add_argument('--robot', type=int); replay.add_argument('--offset', type=int, default=0); replay.add_argument('--limit', type=int, default=64)
    args = parser.parse_args()
    endpoint = urllib.parse.urlsplit(args.endpoint or '')
    if args.endpoint and (endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1' or endpoint.path != '/v1' or endpoint.query or endpoint.fragment or endpoint.username or endpoint.password):
        parser.error('Use the provided 127.0.0.1 /v1 endpoint')
    body = {'operation': args.operation}
    if args.operation == 'submit':
        body.update(package=args.package, source_zip_base64=base64.b64encode(source_zip(args.source,args.package)).decode())
    else:
        body['submission_id'] = args.submission
        if args.operation == 'replay':
            body.update(game_index=args.game, query={'round_start':args.start,'round_end':args.end,'robot_id':args.robot,'record_offset':args.offset,'record_limit':args.limit})
    if args.mailbox:
        print(json.dumps(mailbox_request(args.mailbox,body),indent=2,sort_keys=True))
        return
    request = urllib.request.Request(args.endpoint, data=json.dumps(body).encode(), headers={'Content-Type':'application/json'})
    # Do not send local requests through ambient external proxies.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(request, timeout=150)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        data = response.read(65537)
    if len(data)>65536:
        raise ValueError('Response limit exceeded')
    print(json.dumps(json.loads(data),indent=2,sort_keys=True))

if __name__ == '__main__':
    main()
