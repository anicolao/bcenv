"""Real authority SQLite + synthetic closed files; no subprocess or games."""
import base64
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

from services.epoch_authority import ClockSample
from services.resumable_authority import ResumableAuthority
from tools import public_retention_snapshot as snapshot


def sha(value):return hashlib.sha256(value).hexdigest()


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.guard=mock.patch.object(subprocess,'Popen',side_effect=AssertionError('no subprocess'))
        self.guard.start();self.addCleanup(self.guard.stop)
        self.code=b'package own; public class RobotPlayer {}'
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:z.writestr('own/RobotPlayer.java',self.code)
        self.archive=stream.getvalue();self.note='own approved prediction';self.checkpoint='own approved checkpoint'
        self.a=ResumableAuthority.create(self.root/'authority.sqlite',config={},author='/PRIVATE/operator',
            paths={k:str(self.root/'PRIVATE'/k) for k in ('service','artifacts','mailbox','control','author')},
            clock=lambda:ClockSample('private-clock',100,200))
        self.i=0
        body=dict(package='own',source_zip_base64=base64.b64encode(self.archive).decode())
        self.noted=self.req(dict(body,operation='note',text=self.note,replay_references=[]))
        self.accepted=self.req(dict(body,operation='submit',note_id=self.noted['note_id'],note_sha256=self.noted['note_sha256']))
        self.sid=self.accepted['submission_id'];self.source=self.accepted['source_sha256']
        self.req(dict(operation='checkpoint',text=self.checkpoint,submission_ids=[self.sid]))
        self.artifacts=self.root/'closed';self.artifacts.mkdir()
        payload={'source/own/RobotPlayer.java':self.code,'build/classes/own/RobotPlayer.class':b'synthetic own class',
            'build/artifact.json':b'{"PRIVATE":"/PRIVATE/operator","command":"credential-never-public"}',
            'package/source.zip':self.archive,'package/package.json':b'{"PRIVATE":"raw package metadata"}'}
        for i in range(8):payload[f'game-{i}/replay.bc23']=('synthetic public replay'+str(i)).encode()
        for name,data in payload.items():
            path=self.artifacts/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        hashes={name:sha(data) for name,data in payload.items()}
        binding=dict(source_sha256=self.source,engine_sha256='e'*64,
            binary_sha256=sha(snapshot.canonical({'own/RobotPlayer.class':hashes['build/classes/own/RobotPlayer.class']})),
            files={cat:{name[len(cat)+1:]:h for name,h in hashes.items() if name.startswith(cat+'/')} for cat in ('source','build','package')})
        self.manifest=dict(source_sha256=self.source,batch_id=self.sid,build_identity=binding,files=hashes,sizes={name:len(data) for name,data in payload.items()})
        self.a.start_batch(0,'start',self.sid)
        for i in range(8):self.a.before_game(0,'game'+str(i),self.sid,i)
        games=[dict(index=i,label='public-cell-'+str(i),own_slot='A' if i%2==0 else 'B',status='completed',outcome='win',
            replay_sha256=hashes[f'game-{i}/replay.bc23'],total_rounds=100,terminal_reason=None) for i in range(8)]
        self.a.finish_batch(0,'finish',self.sid,dict(status='complete',games=games,wins=8,losses=0),self.manifest)
        self.approved={self.source,sha(self.code),sha(self.note.encode()),sha(self.checkpoint.encode()),*hashes.values()}
        self.public_replays={hashes[f'game-{i}/replay.bc23'] for i in range(8)}
        self.requests={self.sid:{'root':str(self.artifacts),'files':{name:hashes[name] for name in ('build/classes/own/RobotPlayer.class','package/source.zip','game-0/replay.bc23')}}}

    def req(self,body):
        self.i+=1;return self.a.request(0,'request'+str(self.i),body)['response']

    def export(self,name='out',**overrides):
        args=dict(expected_authority_id=self.a.authority_id,expected_config_digest=self.a.config_digest,fixed_inputs_sha256='f'*64,
            approved_sha256=self.approved,artifact_requests=self.requests,public_replay_sha256=self.public_replays)
        args.update(overrides)
        return snapshot.export_snapshot(self.a.path,self.root/name,**args)

    def test_real_authority_snapshot_sanitizes_and_chunks_only_approved_evidence(self):
        inventory=self.export();out=self.root/'out';evidence=json.loads((out/'evidence.json').read_bytes())
        self.assertEqual(self.a.authority_id,evidence['authority_id']);self.assertEqual(self.a.config_digest,evidence['config_digest'])
        self.assertEqual(self.note,evidence['notes'][0]['text']);self.assertEqual(self.checkpoint,evidence['checkpoint']['text'])
        self.assertEqual(4,len(evidence['own_files']))
        raw=b''.join(p.read_bytes() for p in out.rglob('*') if p.is_file())
        self.assertNotIn(b'PRIVATE',raw);self.assertNotIn(b'credential-never-public',raw)
        self.assertFalse(any(p.suffix in ('.sqlite','.db') for p in out.rglob('*')))
        inner=json.loads((out/'inventory.json').read_bytes());self.assertNotIn('inventory.json',inner['files'])
        self.assertEqual(set(inner['files'])|{'inventory.json'},set(inventory['files']))
        for name,entry in inventory['files'].items():self.assertEqual(entry,dict(sha256=sha((out/name).read_bytes()),bytes=(out/name).stat().st_size))
        for entry in evidence['own_files'].values():
            data=b''.join((out/'blobs'/h).read_bytes() for h in entry['chunks'])
            self.assertEqual(entry['sha256'],sha(data));self.assertEqual(entry['bytes'],len(data))

    def test_source_name_and_bytes_and_text_need_separate_approvals(self):
        for i,missing in enumerate((self.source,sha(self.code),sha(self.note.encode()),sha(self.checkpoint.encode()))):
            with self.subTest(missing=missing),self.assertRaises(snapshot.SnapshotRefused):
                self.export('out'+str(i),approved_sha256=self.approved-{missing})

    def test_replay_needs_explicit_public_origin_not_merely_content_approval(self):
        with self.assertRaises(snapshot.SnapshotRefused):self.export(public_replay_sha256=set())

    def test_logs_metadata_opponent_paths_and_extra_request_fields_rejected(self):
        for i,name in enumerate(('build/artifact.json','package/package.json','game-0/team-A/Enemy.class','../private')):
            request={self.sid:{'root':str(self.artifacts),'files':{name:self.manifest['files'].get(name,'0'*64)}}}
            with self.subTest(name=name),self.assertRaises(snapshot.SnapshotRefused):self.export('out'+str(i),artifact_requests=request)
        bad={self.sid:dict(self.requests[self.sid],private='must reject')}
        with self.assertRaises(snapshot.SnapshotRefused):self.export('extra',artifact_requests=bad)

    def test_symlink_and_changed_closed_file_fail(self):
        file=self.artifacts/'build/classes/own/RobotPlayer.class';file.unlink();file.symlink_to(self.artifacts/'source/own/RobotPlayer.java')
        with self.assertRaises((snapshot.SnapshotRefused,OSError)):self.export('symlink')
        file.unlink();file.write_bytes(b'changed')
        with self.assertRaises(snapshot.SnapshotRefused):self.export('changed')

    def test_wrong_identity_config_and_existing_destination_fail(self):
        with self.assertRaises(snapshot.SnapshotRefused):self.export('wrong',expected_authority_id='wrong')
        with self.assertRaises(snapshot.SnapshotRefused):self.export('config',expected_config_digest='0'*64)
        self.export()
        with self.assertRaises(snapshot.SnapshotRefused):self.export()

    def test_incomplete_batch_never_exports_closed_files(self):
        with self.a._connect() as db:db.execute("UPDATE domain_batches SET state='UNKNOWN' WHERE id=?",(self.sid,))
        with self.assertRaises(snapshot.SnapshotRefused):self.export()
        self.export('metadata',artifact_requests={})
        value=json.loads((self.root/'metadata/evidence.json').read_bytes())
        self.assertNotIn('games',value['batches'][self.sid])

    def test_manifest_hash_corruption_is_not_trusted(self):
        with self.a._connect() as db:db.execute("UPDATE domain_batches SET manifest_sha256=? WHERE id=?",('0'*64,self.sid))
        with self.assertRaises(snapshot.SnapshotRefused):self.export()

    def test_wal_backup_uses_committed_snapshot_not_raw_db_copy(self):
        with self.a._connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('BEGIN IMMEDIATE');db.execute("UPDATE domain_meta SET checkpoint=NULL WHERE id=1")
            self.export('during',artifact_requests={})
            db.rollback()
        value=json.loads((self.root/'during/evidence.json').read_bytes())
        self.assertEqual(self.checkpoint,value['checkpoint']['text'])

    def test_bounds_refuse_without_silent_truncation(self):
        with mock.patch.object(snapshot,'MAX_EXPORT_BYTES',10):
            with self.assertRaises(snapshot.SnapshotRefused):self.export()

    def projection(self):
        from tests.test_own_diagnostics import fixture,project
        raw=fixture(limit=100)
        feedback=json.loads(project(raw,submission_id=self.sid,job_id='j_'+self.sid[2:],limits=snapshot.safe.FeedbackLimits(max_records=128,max_output_encoded_bytes=32768)))
        pending=self.req(dict(operation='replay',submission_id=self.sid,game_index=0,
            query=dict(round_start=0,round_end=2,robot_id=None,record_offset=0,record_limit=100,mode='own_diagnostics')))
        self.a.finish_replay(0,'projection',pending['replay_id'],feedback)
        self.approved.add(sha(snapshot.canonical(feedback)))
        return feedback

    def test_committed_projection_is_typed_and_approved(self):
        feedback=self.projection();self.export();raw=(self.root/'out/evidence.json').read_bytes()
        self.assertNotIn(b'OPPONENT_PRIVATE_CANARY',raw)
        value=json.loads(raw);self.assertEqual(1,len(value['projection_receipts']))
        projected=value['projection_receipts'][0]['projection']
        self.assertEqual(feedback['records'],projected['records'])
        self.assertEqual('bcenv.public-retention-projection.v1',projected['schema'])
        self.assertEqual(list(snapshot.diagnostics.SEMANTICS),projected['semantics'])
        for key,value in feedback['coverage'].items():
            if type(value) is bool:self.assertIs(value,projected['coverage'][key])
        self.assertFalse(projected['coverage']['all_schema_fields_validated'])

    def test_unapproved_projection_refuses(self):
        feedback=self.projection();self.approved.remove(sha(snapshot.canonical(feedback)))
        with self.assertRaises(snapshot.SnapshotRefused):self.export()

    def test_counter_overdraft_is_retained_without_clamping(self):
        with self.a._connect() as db:db.execute("UPDATE counters SET used=ceiling+1 WHERE name='reads'")
        self.export();value=json.loads((self.root/'out/evidence.json').read_bytes())
        self.assertEqual(2001,value['counters']['reads']['used']);self.assertEqual(2000,value['counters']['reads']['ceiling'])

    def test_explicit_closed_own_test_and_typed_receipt_are_not_workspace_scan(self):
        directory=self.root/'approved-tests';directory.mkdir()
        test=b'def test_case(): assert 1 == 1';(directory/'test_case.py').write_bytes(test)
        receipt=snapshot.canonical(dict(schema='bcenv.own-test-receipt.v1',source_sha256=self.source,test_sha256=sha(test),status='pass'))
        (directory/'receipt.json').write_bytes(receipt);(directory/'UNCOMMITTED_SECRET').write_text('DO_NOT_SCAN')
        records={key:dict(root=str(directory),path=name,sha256=sha(data),bytes=len(data),kind=kind,source_sha256=self.source)
            for key,name,data,kind in (('case','test_case.py',test,'test'),('receipt','receipt.json',receipt,'test_receipt'))}
        self.approved.update((sha(test),sha(receipt)))
        self.export(own_records=records)
        value=json.loads((self.root/'out/evidence.json').read_bytes())
        self.assertEqual(2,len(value['own_records']));self.assertTrue(all('not a SQL-committed' in r['provenance'] for r in value['own_records']))
        self.assertNotIn(b'DO_NOT_SCAN',b''.join(p.read_bytes() for p in (self.root/'out').rglob('*') if p.is_file()))
        bad=dict(records);bad['receipt']=dict(records['receipt'],source_sha256='0'*64)
        with self.assertRaises(snapshot.SnapshotRefused):self.export('bad-source',own_records=bad)

    def test_original_clocks_and_epoch_are_numeric_audit_only(self):
        self.export();value=json.loads((self.root/'out/evidence.json').read_bytes())
        self.assertEqual(0,value['epoch']);self.assertEqual(21800,value['original_clocks']['mono_deadline'])
        self.assertNotIn('private-clock',json.dumps(value));self.assertNotIn('author',value)

    def test_actions_projection_uses_actual_projector_known_schema(self):
        from tests.test_replay_feedback import fixture,encode
        from services import replay_feedback_v2 as pages
        full=fixture(start=1,end=1,robot=None,limit=128)
        count=full['matching_record_count'];offset=0;rows=full['records']
        full.update(format=pages.QUERY_FORMAT,returned_record_count=len(rows),truncated=False,selected_records_complete=True,
            page_records_complete=True,page_end_offset=len(rows),next_record_offset=None)
        full['query'].update(record_offset=0,record_limit=128)
        context=pages.ReplayPageQuery(1,1,None,0,128)
        feedback=json.loads(pages.project_replay_feedback_page(encode(full),submission_id=self.sid,job_id='j_'+self.sid[2:],
            own_team_id=1,expected_spec_version='2.0.3',expected_query=context,
            limits=snapshot.safe.FeedbackLimits(max_records=128,max_output_encoded_bytes=32768)))
        pending=self.req(dict(operation='replay',submission_id=self.sid,game_index=0,
            query=dict(round_start=1,round_end=1,robot_id=None,record_offset=0,record_limit=128)))
        self.a.finish_replay(0,'actionprojection',pending['replay_id'],feedback)
        self.approved.add(sha(snapshot.canonical(feedback)))
        self.export();value=json.loads((self.root/'out/evidence.json').read_bytes())
        projected=value['projection_receipts'][0]['projection']
        self.assertEqual(feedback['records'],projected['records'])
        for key,flag in feedback['coverage'].items():
            if type(flag) is bool:self.assertIs(flag,projected['coverage'][key])
        self.assertFalse(projected['coverage']['all_schema_fields_validated'])

    def test_own_receipt_requires_supplied_test_and_rejects_command_fields(self):
        directory=self.root/'receipts';directory.mkdir()
        base=dict(schema='bcenv.own-test-receipt.v1',source_sha256=self.source,test_sha256='1'*64,status='pass')
        for index,body in enumerate((base,dict(base,command='/PRIVATE/run'))):
            data=snapshot.canonical(body);(directory/'receipt.json').write_bytes(data);self.approved.add(sha(data))
            record={'receipt':dict(root=str(directory),path='receipt.json',sha256=sha(data),bytes=len(data),kind='test_receipt',source_sha256=self.source)}
            with self.assertRaises(snapshot.SnapshotRefused):self.export('receipt'+str(index),own_records=record)
