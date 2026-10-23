"""Bounded public own-evidence export, never an authority/workspace backup.

The caller explicitly approves every exported content digest. Approval and known
public replay origin are trusted assertions, not secret detection/authentication.
The SQLite backup exists only in memory; all public metadata is newly projected.
"""
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import time

from services.scrimmage import accept_source_zip
from services import replay_feedback as safe
from services import replay_feedback_v2 as pages
from services import own_diagnostics as diagnostics

SCHEMA='bcenv.public-retention-snapshot.v1'
INVENTORY='bcenv.public-retention-inventory.v1'
CHUNK_BYTES=1048576
MAX_FILE_BYTES=67108864
MAX_EXPORT_BYTES=268435456
MAX_FILES=16384
MAX_METADATA_BYTES=8388608
MAX_DATABASE_BYTES=1073741824
COUNTERS={'mailbox','reads','notes','versions','training_games','validation_games','replays'}


class SnapshotRefused(ValueError):
    pass


def require(ok,reason):
    if not ok:raise SnapshotRefused(reason)


def canonical(value):
    try:return json.dumps(value,sort_keys=True,separators=(',', ':'),ensure_ascii=True,allow_nan=False).encode()
    except (ValueError,TypeError,RecursionError):raise SnapshotRefused('invalid JSON value') from None


def sha(data):return hashlib.sha256(data).hexdigest()


def digest(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None,'invalid digest')
    return value


def identifier(value):
    require(type(value) is str and re.fullmatch('[A-Za-z0-9_-]{1,128}',value) is not None,'invalid public identifier')
    return value


def _pairs(values):
    out={}
    for key,value in values:
        require(key not in out,'duplicate JSON key');out[key]=value
    return out


def decode(value,limit=1048576):
    require(type(value) in (str,bytes) and len(value.encode() if type(value) is str else value)<=limit,'JSON bound')
    try:return json.loads(value,object_pairs_hook=_pairs,parse_constant=lambda v: (_ for _ in ()).throw(SnapshotRefused('nonfinite JSON')))
    except (ValueError,UnicodeError,RecursionError):raise SnapshotRefused('invalid JSON') from None


def relative(value):
    require(type(value) is str and 0<len(value)<=512 and '\\' not in value,'invalid relative path')
    path=PurePosixPath(value)
    require(not path.is_absolute() and value==path.as_posix() and all(p not in ('','.','..') for p in path.parts),'path traversal/noncanonical path')
    return path


def _no_symlinks(path):
    path=Path(path).absolute()
    for part in (path,*path.parents):require(not part.is_symlink(),'symlink rejected')
    return path


def _read_file(root,name,size):
    relative(name);require(type(size) is int and 0<=size<=MAX_FILE_BYTES,'artifact size bound')
    root=_no_symlinks(root);require(root.is_dir(),'artifact root missing')
    directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        parts=PurePosixPath(name).parts
        for part in parts[:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
            os.close(directory);directory=child
        fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory)
        with os.fdopen(fd,'rb') as handle:
            before=os.fstat(handle.fileno());require(stat.S_ISREG(before.st_mode) and before.st_size==size,'artifact size/type changed')
            data=handle.read(size+1);after=os.fstat(handle.fileno())
            require(len(data)==size and (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==
                    (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns),'artifact changed during read')
            return data
    finally:os.close(directory)


def _backup(path):
    path=_no_symlinks(path)
    require(path.is_file() and path.stat().st_size<=MAX_DATABASE_BYTES,'database bound/type')
    snapshot=sqlite3.connect(':memory:');snapshot.row_factory=sqlite3.Row
    start=time.monotonic();calls=0
    def progress(status,remaining,total):
        nonlocal calls
        calls+=1
        require(calls<=8192 and total<=MAX_DATABASE_BYTES//512 and time.monotonic()-start<=30,'backup bound')
    try:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)) as source:
            page_size=source.execute('PRAGMA page_size').fetchone()[0]
            require(type(page_size) is int and page_size>0,'database page size')
            def bounded(status,remaining,total):
                require(total*page_size<=MAX_DATABASE_BYTES,'database page bound')
                progress(status,remaining,total)
            source.backup(snapshot,pages=256,progress=bounded,sleep=.001)
        return snapshot
    except BaseException:
        snapshot.close();raise


def _rows(db,table,limit):
    # table names are fixed call-site literals, never caller-controlled SQL.
    rows=db.execute('SELECT * FROM '+table+' LIMIT ?', (limit+1,)).fetchall()
    require(len(rows)<=limit,'table row bound')
    return rows


def _text(value,approved,limit):
    require(type(value) is str,'text type')
    try:data=value.encode('utf-8')
    except UnicodeError:raise SnapshotRefused('text encoding') from None
    require(len(data)<=limit and sha(data) in approved,'unapproved text or text bound')
    return value


def _game(value):
    require(type(value) is dict,'game object')
    index=value.get('index');require(type(index) is int and 0<=index<8,'game index')
    label=value.get('label');identifier(label)
    slot=value.get('own_slot');require(slot in ('A','B'),'own slot')
    status=value.get('status');require(status in ('completed','infrastructure_failure'),'game status')
    outcome=value.get('outcome');require(outcome in ('win','loss',None),'game outcome')
    replay=value.get('replay_sha256');require(replay is None or digest(replay),'replay hash')
    rounds=value.get('total_rounds');require(rounds is None or type(rounds) is int and 1<=rounds<=1000000,'round count')
    return dict(index=index,label=label,own_slot=slot,status=status,outcome=outcome,replay_sha256=replay,total_rounds=rounds)


def _projection(feedback,query,sid,slot,approved):
    require(sha(canonical(feedback)) in approved,'unapproved projection bytes')
    common={'schema','status','spec_version','submission_id','job_id','own_team_id','query','records','coverage','limits'}
    mode=query['mode'];team=1 if slot=='A' else 2
    expected_schema=pages.SCHEMA if mode=='actions' else diagnostics.SCHEMA
    require(set(feedback)==common|({'matches'} if mode=='actions' else {'semantics'}) and
            feedback['schema']==expected_schema and feedback['status']=='available' and
            feedback['spec_version'] in safe.SUPPORTED_SPEC_VERSIONS and feedback['submission_id']==sid and
            feedback['job_id']=='j_'+sid[2:] and type(feedback['own_team_id']) is int and feedback['own_team_id']==team,'projection known schema/context')
    basic={key:query[key] for key in ('round_start','round_end','robot_id','record_offset','record_limit')}
    require(query['round_end']-query['round_start']<=256,'projection round span')
    if mode=='actions':
        expected=dict(round_start=basic['round_start'],round_end=basic['round_end'],robot_id=basic['robot_id'],record_offset=basic['record_offset'],
            source_record_limit=basic['record_limit'],round_bounds_inclusive=True,team_resource_rows_included_with_robot_filter=True,match_scope='all_matches')
        require(feedback['query']==expected,'action query binding')
        require(type(feedback['matches']) is list and 1<=len(feedback['matches'])<=8,'match count')
        matches=[safe._match(v,i,expected) for i,v in enumerate(feedback['matches'])]
        require(matches==feedback['matches'],'extra match fields')
        rows=pages._records(feedback['records'],matches,expected,team)
    else:
        require(feedback['query']==dict(basic,own_team_id=team) and feedback['semantics']==list(diagnostics.SEMANTICS),'diagnostic query/semantics')
        context=pages.ReplayPageQuery(**basic);matches=[]
        rows=[diagnostics._row(v,context,safe.FeedbackLimits(max_records=128,max_output_encoded_bytes=32768)) for v in feedback['records']]
        orders=[(r['match_index'],r['round'],r['robot_id']) for r in rows]
        require(orders==sorted(set(orders)),'diagnostic record ordering')
    require(rows==feedback['records'],'extra record fields')
    coverage=feedback['coverage'];require(type(coverage) is dict,'coverage type')
    common_coverage={'matching_record_count','returned_record_count','output_page_end_offset','next_record_offset',
        'has_more','supplied_page_records_complete','selected_records_complete','source_stream_complete',
        'extracted_fields_valid','all_schema_fields_validated','truncated'}
    extras=({'matching_counts_by_kind','source_returned_record_count','source_page_records_complete','source_page_end_offset',
             'source_next_record_offset','truncation_reasons'} if mode=='actions' else {'count_scope'})
    require(set(coverage)==common_coverage|extras,'extra coverage fields')
    for key in ('has_more','supplied_page_records_complete','selected_records_complete','source_stream_complete',
                'extracted_fields_valid','all_schema_fields_validated','truncated'):
        require(type(coverage[key]) is bool,'coverage boolean')
    if mode=='own_diagnostics':require(coverage['count_scope']=='own_team_filtered_rows','diagnostic count scope')
    else:
        bykind=coverage['matching_counts_by_kind'];require(type(bykind) is dict and set(bykind)<=set(safe.KINDS) and
            all(type(v) is int and 0<=v<=5000000 for v in bykind.values()),'coverage kind counts')
        require(type(coverage['source_page_records_complete']) is bool,'source page flag')
        for key in ('source_returned_record_count','source_page_end_offset'):
            require(type(coverage[key]) is int and 0<=coverage[key]<=5000000,'source coverage count')
        require(coverage['source_next_record_offset'] is None or type(coverage['source_next_record_offset']) is int and 0<=coverage['source_next_record_offset']<=5000000,'source cursor')
        require(type(coverage['truncation_reasons']) is list and len(coverage['truncation_reasons'])<=4 and
            all(v in ('source_record_offset','source_record_limit','projection_record_limit','projection_output_limit') for v in coverage['truncation_reasons']),'truncation reason')
    limits=feedback['limits'];require(type(limits) is dict and set(limits)=={'max_records','max_round_span','max_output_encoded_bytes'} and
        all(type(v) is int and v>0 for v in limits.values()) and limits['max_records']<=128 and limits['max_round_span']<=256 and limits['max_output_encoded_bytes']<=32768,'projection limits')
    counts={key:coverage.get(key) for key in ('matching_record_count','returned_record_count','output_page_end_offset')}
    require(all(type(v) is int and 0<=v<=5000000 for v in counts.values()) and counts['returned_record_count']==len(rows),'coverage counts')
    flags={key:coverage[key] for key in ('has_more','truncated','supplied_page_records_complete',
        'selected_records_complete','source_stream_complete','extracted_fields_valid','all_schema_fields_validated')}
    if mode=='actions':flags['source_page_records_complete']=coverage['source_page_records_complete']
    require(all(type(v) is bool for v in flags.values()),'coverage flags')
    next_offset=coverage.get('next_record_offset');require(next_offset is None or type(next_offset) is int and 0<=next_offset<=5000000,'coverage offset')
    result=dict(schema='bcenv.public-retention-projection.v1',source_schema=expected_schema,spec_version=feedback['spec_version'],
        own_team_id=team,query=query,matches=matches,records=rows,coverage=dict(counts,**flags,next_record_offset=next_offset))
    if mode=='own_diagnostics':result['semantics']=list(diagnostics.SEMANTICS)
    return result


def export_snapshot(db_path,destination,*,expected_authority_id,expected_config_digest,
                    fixed_inputs_sha256,approved_sha256,artifact_requests=None,public_replay_sha256=(),own_records=None,expected_retention_descriptor_sha256=None):
    """Export to an exclusive fresh directory; partial failures are not resumable.

    artifact_requests: COMPLETE batch ID -> {root: local path, files: {rel: SHA}}.
    Only approved own source/classes/package and public-origin replay bytes are
    eligible. No log/config/result JSON, opponent class, DB, or workspace scanning.
    Returned inventory covers every emitted file including inventory.json; the
    on-disk inventory covers evidence/blobs and excludes itself.
    """
    identifier(expected_authority_id);digest(expected_config_digest);digest(fixed_inputs_sha256)
    approved={digest(v) for v in approved_sha256};public_replays={digest(v) for v in public_replay_sha256}
    closed_records={} if own_records is None else own_records
    require(type(closed_records) is dict and len(closed_records)<=256,'own record bound')
    requests={} if artifact_requests is None else artifact_requests
    require(type(requests) is dict and len(requests)<=26,'artifact request bound')
    destination=Path(destination);require(not destination.exists() and not destination.is_symlink(),'fresh destination required')
    _no_symlinks(destination.parent);destination.mkdir();(destination/'blobs').mkdir()
    files={};own_files={};total_bytes=0
    def emit(name,data):
        nonlocal total_bytes
        relative(name);require(len(files)<MAX_FILES and total_bytes+len(data)<=MAX_EXPORT_BYTES,'export bound')
        with (destination/name).open('xb') as handle:require(handle.write(data)==len(data),'short export write')
        files[name]={'sha256':sha(data),'bytes':len(data)};total_bytes+=len(data)
    def content(name,data):
        relative(name);require(name not in own_files and len(own_files)<MAX_FILES-2,'duplicate/file bound')
        require(len(data)<=MAX_FILE_BYTES and sha(data) in approved,'unapproved content or file bound')
        chunks=[]
        for offset in range(0,len(data),CHUNK_BYTES):
            chunk=data[offset:offset+CHUNK_BYTES];key=sha(chunk);blob='blobs/'+key
            if blob not in files:emit(blob,chunk)
            chunks.append(key)
        own_files[name]={'sha256':sha(data),'bytes':len(data),'chunks':chunks}
    with closing(_backup(db_path)) as db:
        run=db.execute('SELECT * FROM run WHERE id=1').fetchone()
        require(run is not None and run['authority_id']==expected_authority_id and run['config_digest']==expected_config_digest and
                sha(run['config'].encode())==expected_config_digest,'authority/config mismatch')
        require(type(run['epoch']) is int and 0<=run['epoch']<64,'epoch bound')
        clocks={key:run[key] for key in ('wall_start','mono_start','wall_deadline','mono_deadline')}
        require(all(type(v) in (int,float) and math.isfinite(v) for v in clocks.values()) and
                clocks['wall_start']<clocks['wall_deadline'] and clocks['mono_start']<clocks['mono_deadline'],'original clock evidence')
        retention = None
        retention_history = []
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='domain_retention'").fetchone():
            from services.public_retention_authority import retention_descriptor_from_db, retention_history_from_db
            retention = retention_descriptor_from_db(db,run)
            retention_history = retention_history_from_db(db,run)
        if expected_retention_descriptor_sha256 is not None:
            digest(expected_retention_descriptor_sha256)
            require(retention is not None and retention['descriptor_sha256'] == expected_retention_descriptor_sha256,
                    'retention descriptor changed')
        config=decode(run['config']);profile=config['config'].get('domain_profile')
        require(profile in ('resumable-v1','public-transfer-v1'),'unsupported public profile')
        meta=db.execute('SELECT * FROM domain_meta WHERE id=1').fetchone();require(meta is not None and meta['version']==1,'domain schema')
        phase=meta['phase'];require(phase in ('training','locked','validation','complete','selected','halted','incomplete'),'phase')
        require(type(meta['halted']) is int and meta['halted'] in (0,1),'halted type')
        sources={}
        for row in _rows(db,'domain_sources',72):
            fingerprint=digest(row['fingerprint']);archive=row['archive']
            require(type(archive) is bytes and len(archive)<=1048576 and sha(archive)==row['archive_digest'],'source archive integrity')
            accepted=accept_source_zip(archive,row['package']);require(accepted.content_sha256==fingerprint,'normalized source integrity')
            require(fingerprint in approved,'unapproved normalized source identity/paths')
            sources[fingerprint]={'package':accepted.package,'files':[]}
            for item in accepted.files:
                name='sources/'+fingerprint+'/'+relative(item.path).as_posix();content(name,item.data)
                sources[fingerprint]['files'].append(name)
        notes=[]
        for row in _rows(db,'domain_notes',72):
            value=decode(row['response'],65536);source=digest(row['source']);require(source in sources,'note source')
            require(value.get('package')==sources[source]['package'] and value.get('source_sha256')==source and value.get('note_id')==row['id'] and value.get('note_sha256')==row['digest'],'note binding')
            basis={key:value[key] for key in ('note_id','source_sha256','package','text','replay_references')}
            require(sha(canonical(basis))==row['digest'],'note digest')
            refs=value['replay_references'];require(type(refs) is list and len(refs)<=8,'note reference bound')
            require(type(row['used']) is int and row['used'] in (0,1),'note used type')
            notes.append(dict(id=identifier(row['id']),source_sha256=source,note_sha256=digest(row['digest']),
                text=_text(value['text'],approved,4096),used=bool(row['used']),reference_ids=[identifier(r['reference_id']) for r in refs]))
        batches={};manifests={}
        for row in _rows(db,'domain_batches',26):
            sid=identifier(row['id']);source=digest(row['source']);require(source in sources,'batch source')
            require(row['phase'] in ('training','validation','transfer') and row['state'] in ('QUEUED','RUNNING','COMPLETE','REJECTED','FAILED','UNKNOWN'),'batch state')
            value=decode(row['response'],65536)
            require(value.get('submission_id')==sid and value.get('source_sha256')==source,'batch response binding')
            entry=dict(id=sid,phase=row['phase'],original_submission_id=identifier(row['original']),source_sha256=source,state=row['state'])
            if row['state']=='COMPLETE':
                manifest=decode(row['manifest']);require(sha(canonical(manifest))==row['manifest_sha256'] and manifest.get('source_sha256')==source and manifest.get('batch_id')==sid,'complete manifest binding')
                require(type(manifest.get('files')) is dict and type(manifest.get('sizes')) is dict and set(manifest['files'])==set(manifest['sizes']) and len(manifest['files'])<=MAX_FILES,'manifest schema')
                for name,h in manifest['files'].items():
                    path=relative(name);require(path.parts[0] in ('source','build','package',*(f'game-{i}' for i in range(8))),'manifest category')
                    digest(h);require(type(manifest['sizes'][name]) is int and 0<=manifest['sizes'][name]<=MAX_FILE_BYTES,'manifest size')
                require(type(value.get('games')) is list and len(value['games'])==8,'complete game count')
                games=[_game(g) for g in value['games']];require([g['index'] for g in games]==list(range(8)) and all(g['status']=='completed' and g['outcome'] in ('win','loss') and g['replay_sha256'] is not None and g['total_rounds'] is not None for g in games),'complete game identity')
                binding=manifest.get('build_identity');require(type(binding) is dict and binding.get('source_sha256')==source and type(binding.get('files')) is dict and set(binding['files'])=={'source','build','package'},'build source binding')
                build_files=binding['files']['build'];require(type(build_files) is dict,'build inventory')
                classes={name[len('classes/'):]:h for name,h in build_files.items() if name.startswith('classes/')}
                require(bool(classes) and sha(canonical(classes))==binding['binary_sha256'],'compiled binary identity')
                entry.update(complete_manifest_sha256=digest(row['manifest_sha256']),games=games,
                    build=dict(source_sha256=source,binary_sha256=digest(binding['binary_sha256']),engine_sha256=digest(binding['engine_sha256'])))
                if row['phase']=='transfer':entry['transfer_schedule_sha256']=digest(value['transfer_schedule_sha256'])
                manifests[sid]=manifest
            batches[sid]=entry
        # Audit only: retain typed query/reference identity and the hash of the
        # committed candidate-facing projection, not its arbitrary nested payload.
        projection_receipts=[];references={}
        for row in _rows(db,'domain_references',450):
            value=decode(row['response'],65536);rid=identifier(row['id'])
            require(value.get('reference_id')==rid,'reference ID mismatch');references[rid]=value
        for row in _rows(db,'domain_replays',450):
            if row['state']!='COMPLETE':continue
            value=decode(row['response'],65536);ref=value.get('replay_reference');feedback=value.get('feedback')
            require(type(ref) is dict and type(feedback) is dict and ref.get('reference_id') in references and references[ref['reference_id']]==ref,'projection reference binding')
            sid=row['batch'];require(sid in manifests,'projection complete batch')
            index=row['game_index'];require(type(index) is int and 0<=index<8,'projection game index')
            require(ref.get('submission_id')==sid and ref.get('source_sha256')==batches[sid]['source_sha256'] and ref.get('game_index')==index and
                    ref.get('replay_sha256')==batches[sid]['games'][index]['replay_sha256'] and
                    ref.get('feedback_sha256')==sha(canonical(feedback)),'projection evidence mismatch')
            query=ref.get('query');keys={'round_start','round_end','robot_id','record_offset','record_limit','mode'}
            require(type(query) is dict and set(query)==keys and query['mode'] in ('actions','own_diagnostics'),'projection query schema')
            require(all(type(query[k]) is int and 0<=query[k]<=2147483647 for k in ('round_start','round_end','record_offset','record_limit')) and
                    query['round_start']<=query['round_end'] and 1<=query['record_limit']<=128 and
                    (query['robot_id'] is None or type(query['robot_id']) is int and 0<=query['robot_id']<=2147483647),'projection query bounds')
            records=feedback.get('records');require(type(records) is list and len(records)<=128,'projection record bound')
            projection_receipts.append(dict(replay_id=identifier(row['id']),reference_id=identifier(ref['reference_id']),submission_id=sid,
                game_index=index,source_sha256=batches[sid]['source_sha256'],replay_sha256=digest(ref['replay_sha256']),
                feedback_sha256=digest(ref['feedback_sha256']),query=query,returned_record_count=len(records),
                projection=_projection(feedback,query,sid,batches[sid]['games'][index]['own_slot'],approved),
                retained='typed approved projection; arbitrary metadata omitted'))
        checkpoint=None
        if meta['checkpoint'] is not None:
            value=decode(meta['checkpoint'],65536);ids=value.get('submission_ids')
            require(type(ids) is list and len(ids)<=24 and len(set(ids))==len(ids) and all(i in batches for i in ids),'checkpoint bindings')
            require(sha(canonical(dict(text=value['text'],submission_ids=ids)))==value['checkpoint_sha256'],'checkpoint digest')
            checkpoint=dict(text=_text(value['text'],approved,32768),submission_ids=ids,checkpoint_sha256=digest(value['checkpoint_sha256']))
        counters={}
        for row in _rows(db,'counters',7):
            require(row['name'] in COUNTERS and type(row['used']) is int and type(row['ceiling']) is int and 0<=row['used']<=1000000 and 0<=row['ceiling']<=2200,'counter schema')
            counters[row['name']]={'used':row['used'],'ceiling':row['ceiling']}
        require(set(counters)==COUNTERS,'counter set')
        finalists=decode(meta['finalists']);require(type(finalists) is list and len(finalists) in (0,2) and all(i in batches for i in finalists),'finalist IDs')
        for choice in (meta['provisional'],meta['selected']):require(choice is None or choice in finalists,'choice ID')
        for sid,request in requests.items():
            require(sid in manifests and type(request) is dict and set(request)=={'root','files'} and type(request['files']) is dict and len(request['files'])<=MAX_FILES,'artifact request schema/complete state')
            batch=batches[sid];manifest=manifests[sid]
            for name,expected in request['files'].items():
                path=relative(name);digest(expected)
                require(name in manifest['files'] and expected==manifest['files'][name],'artifact not committed')
                replay=re.fullmatch(r'game-([0-7])/replay\.bc23',name)
                own=(name.startswith('source/') and path.suffix=='.java' or name.startswith('build/classes/') and path.suffix=='.class' or name=='package/source.zip')
                require(own or replay is not None,'artifact category not public allowlist')
                if own:
                    require(batch['phase']=='training' and batch['original_submission_id']==sid,'own build export requires completed original')
                    category,rest=name.split('/',1)
                    require(manifest['build_identity']['files'][category].get(rest)==expected,'artifact not in own build identity')
                if replay:
                    require(expected in public_replays and batch['games'][int(replay.group(1))]['replay_sha256']==expected,'replay public origin/binding absent')
                data=_read_file(request['root'],name,manifest['sizes'][name]);require(sha(data)==expected,'closed artifact hash mismatch')
                if name=='package/source.zip':require(accept_source_zip(data,sources[batch['source_sha256']]['package']).content_sha256==batch['source_sha256'],'package source mismatch')
                if name.startswith('source/'):
                    logical='sources/'+batch['source_sha256']+'/'+name[len('source/'):]
                    require(logical in own_files and own_files[logical]['sha256']==expected,'source file mismatch')
                content('batches/'+sid+'/'+name,data)
        retained_records=[]
        declared_tests={(item.get('source_sha256'),item.get('sha256')) for item in closed_records.values()
                        if type(item) is dict and item.get('kind')=='test' and type(item.get('source_sha256')) is str and type(item.get('sha256')) is str}
        for record_id,item in closed_records.items():
            identifier(record_id)
            require(type(item) is dict and set(item)=={'source_sha256','root','path','sha256','bytes','kind'},'own record schema')
            source=digest(item['source_sha256']);require(source in sources,'own record source binding')
            kind=item['kind'];require(kind in ('test','test_receipt'),'own record kind')
            path=relative(item['path']);require(path.suffix in (('.java','.py') if kind=='test' else ('.json',)),'own record suffix')
            data=_read_file(item['root'],item['path'],item['bytes']);require(sha(data)==digest(item['sha256']),'own record digest')
            if kind=='test_receipt':
                receipt=decode(data,65536)
                require(type(receipt) is dict and set(receipt)=={'schema','source_sha256','test_sha256','status'} and
                        receipt['schema']=='bcenv.own-test-receipt.v1' and receipt['source_sha256']==source and
                        receipt['status'] in ('pass','fail','error'),'typed own test receipt')
                digest(receipt['test_sha256'])
                require((source,receipt['test_sha256']) in declared_tests,'test receipt lacks supplied source-bound test')
            logical='records/'+record_id+'/'+path.name;content(logical,data)
            retained_records.append(dict(id=record_id,source_sha256=source,kind=kind,file=logical,
                provenance='explicit operator-approved closed own record; not a SQL-committed test'))
        evidence=dict(schema=SCHEMA,scope='approved public own evidence only; not service authority or execution recovery',
            authority_id=expected_authority_id,config_digest=expected_config_digest,fixed_inputs_sha256=fixed_inputs_sha256,
            retention=retention,retention_history=retention_history,profile=profile,epoch=run['epoch'],original_clocks=clocks,clock_scope='audit only; cannot authorize new-host continuation',phase=phase,halted=bool(meta['halted']),finalists=finalists,provisional_choice=meta['provisional'],
            selected=meta['selected'],counters=counters,sources=sources,notes=notes,checkpoint=checkpoint,batches=batches,
            own_files=own_files,own_records=retained_records,projection_receipts=projection_receipts,uncommitted_tests_work='not scanned; only explicit approved closed records retained, no SQL test-artifact mechanism')
        encoded=canonical(evidence);require(len(encoded)<=MAX_METADATA_BYTES,'metadata bound');emit('evidence.json',encoded)
    inventory={'schema':INVENTORY,'files':dict(files)}
    encoded=canonical(inventory);require(len(encoded)<=MAX_METADATA_BYTES,'inventory bound');emit('inventory.json',encoded)
    return {'schema':INVENTORY,'files':files}
