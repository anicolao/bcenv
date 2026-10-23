"""Pure static map bytes/Fraction/tempfile/ZIP fixtures; no engine/process calls."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

from services import public_transfer_maps as maps


def flatmap(width,height,origin=(0,0),name='synthetic'):
    # Hand-built ordinary FlatBuffer: root16, vtable4 (three fields), object24.
    name=name.encode('utf-8');data=bytearray(44+len(name)+1)
    struct.pack_into('<I',data,0,16)
    struct.pack_into('<HHHHH',data,4,10,24,4,8,16)
    struct.pack_into('<iIiiii',data,16,12,20,*origin,origin[0]+width,origin[1]+height)
    struct.pack_into('<I',data,40,len(name));data[44:44+len(name)]=name
    return bytes(data)


class MetadataTests(unittest.TestCase):
    def test_handbuilt_dimensions_follow_corner_difference(self):
        self.assertEqual(maps.dimensions(flatmap(37,51,(-7,100))),dict(width=37,height=51))

    def test_truncated_every_byte_fails(self):
        data=flatmap(20,30)
        for size in range(len(data)):
            with self.subTest(size=size),self.assertRaises(ValueError):maps.dimensions(data[:size])

    def test_invalid_root_vtable_missing_fields_overlap_and_offsets(self):
        cases=[(0,'<I',0),(0,'<I',17),(16,'<i',1000),(4,'<H',9),
               (6,'<H',3),(10,'<H',0),(12,'<H',8),(20,'<I',0),(40,'<I',999999)]
        for offset,fmt,value in cases:
            data=bytearray(flatmap(20,30));struct.pack_into(fmt,data,offset,value)
            with self.subTest(offset=offset,value=value),self.assertRaises(ValueError):maps.dimensions(bytes(data))

    def test_nonpositive_dimensions_fail(self):
        for w,h in [(0,1),(1,0),(-1,2),(2,-1)]:
            with self.assertRaises(ValueError):maps.dimensions(flatmap(w,h))

    def test_invalid_utf8_and_terminator_fail(self):
        for offset,value in [(44,255),(-1,1)]:
            data=bytearray(flatmap(1,2));data[offset]=value
            with self.assertRaises(ValueError):maps.dimensions(bytes(data))


class SelectionTests(unittest.TestCase):
    def inventory(self):
        return [dict(basename=n+'.map23',width=w,height=h) for n,w,h in
                [('train1',10,10),('train2',20,20),('valid1',100,10),('valid2',10,100),
                 ('a',10,30),('b',30,10),('center',50,50)]]
    def choose(self,inventory):return maps.select_maps(inventory,['train1.map23','train2.map23'],['valid1.map23','valid2.map23'])

    def test_full_corpus_range_and_exact_rational_scores(self):
        actual=self.choose(self.inventory())
        self.assertEqual(actual['ranges'],dict(width=90,height=90))
        self.assertEqual(actual['selected'],['center.map23','a.map23'])
        self.assertEqual(actual['minimum_distances'],[dict(numerator=2,denominator=3),dict(numerator=2,denominator=9)])

    def test_permutation_and_exclusion_order_do_not_change_selection(self):
        first=self.choose(self.inventory())
        self.assertEqual(first,self.choose(list(reversed(self.inventory()))))

    def test_zero_ranges_codepoint_case_preserved_ties(self):
        inventory=self.inventory()
        for item in inventory:item.update(width=30,height=40)
        inventory[-1]['basename']='A.map23'
        actual=self.choose(inventory)
        self.assertEqual(actual['selected'],['A.map23','a.map23'])
        self.assertEqual(actual['minimum_distances'],[dict(numerator=0,denominator=1)]*2)

    def test_duplicate_missing_invalid_or_too_few_abort(self):
        for inventory in [self.inventory()+[self.inventory()[0]],self.inventory()[1:],self.inventory()[:5]]:
            with self.assertRaises(ValueError):self.choose(inventory)
        for value in [0,-1,True,2.5]:
            inventory=self.inventory();inventory[-1]['width']=value
            with self.assertRaises(ValueError):self.choose(inventory)

    def test_second_choice_uses_first_choice_as_anchor(self):
        inventory=self.inventory()
        inventory.extend([dict(basename='near.map23',width=51,height=50)])
        actual=self.choose(inventory)
        self.assertEqual(actual['selected'],['near.map23','a.map23'])


class ProofTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.bundle=self.root/'maps';self.bundle.mkdir();self.jar=self.root/'engine.jar'
        for item in SelectionTests().inventory():
            (self.bundle/item['basename']).write_bytes(flatmap(item['width'],item['height'],name=item['basename']))
        self.rebuild()
        self.excluded={k:sorted(str(self.bundle/(n+'.map23')) for n in names) for k,names in
                       [('training',['train1','train2']),('validation',['valid1','valid2'])]}
        patch=mock.patch.object(subprocess,'Popen',side_effect=AssertionError('no processes'))
        patch.start();self.addCleanup(patch.stop)

    def rebuild(self,extra=None):
        with zipfile.ZipFile(self.jar,'w') as archive:
            for path in self.bundle.iterdir():archive.writestr('battlecode/world/resources/'+path.name,path.read_bytes())
            if extra:archive.writestr(extra,(self.bundle/'a.map23').read_bytes())
        self.pin=hashlib.sha256(self.jar.read_bytes()).hexdigest()

    def prepare(self):
        with mock.patch.object(maps,'ENGINE_SHA256',self.pin):return maps.prepare_selection(self.bundle,self.excluded,self.jar)

    def validate(self,proof):
        path=self.root/'proof.json';path.write_bytes(maps.canonical(proof))
        with mock.patch.object(maps,'ENGINE_SHA256',self.pin):
            return maps.validate_selection(path,hashlib.sha256(path.read_bytes()).hexdigest(),self.excluded)

    def test_roundtrip_frozen_hashes_and_full_inventory(self):
        proof=self.prepare();selected=self.validate(proof)
        self.assertEqual(selected,(str(self.bundle/'center.map23'),str(self.bundle/'a.map23')))
        self.assertEqual(len(proof['inventory']),7)
        self.assertEqual(proof['parser_sha256'],hashlib.sha256(Path(maps.__file__).read_bytes()).hexdigest())
        for item in proof['inventory']:
            self.assertEqual(item['sha256'],hashlib.sha256(Path(item['path']).read_bytes()).hexdigest())
            self.assertEqual(item['metadata_sha256'],maps.digest(maps.canonical({k:item[k] for k in ('width','height')})))

    def test_modified_output_or_exclusion_or_parser_pin_rejected(self):
        for field in ('selection','excluded_maps','parser_sha256'):
            proof=self.prepare();proof[field]={}
            with self.subTest(field=field),self.assertRaises(ValueError):self.validate(proof)

    def test_corpus_changed_unreadable_missing_or_invalid_fails(self):
        proof=self.prepare();path=self.bundle/'a.map23';path.write_bytes(b'bad')
        with self.assertRaises(ValueError):self.validate(proof)
        path.unlink()
        with self.assertRaises(ValueError):self.prepare()

    def test_valid_extra_or_changed_map_rejected_by_official_inventory(self):
        extra=self.bundle/'extra.map23';extra.write_bytes(flatmap(20,30))
        with self.assertRaisesRegex(ValueError,'official_corpus_mismatch'):self.prepare()
        extra.unlink();(self.bundle/'a.map23').write_bytes(flatmap(20,30))
        with self.assertRaisesRegex(ValueError,'official_corpus_mismatch'):self.prepare()

    def test_unreadable_entry_fails_instead_of_skip(self):
        original=maps._bytes
        def fail(path,limit):
            if path.name=='a.map23':raise PermissionError('inert unreadable fixture')
            return original(path,limit)
        with mock.patch.object(maps,'_bytes',side_effect=fail),self.assertRaises(PermissionError):self.prepare()

    def test_jar_duplicate_basename_fails(self):
        self.rebuild('battlecode/world/resources/other/a.map23')
        with self.assertRaisesRegex(ValueError,'duplicate_official_basename'):self.prepare()

    def test_proof_hash_wrong_fails(self):
        path=self.root/'proof.json';path.write_bytes(maps.canonical(self.prepare()))
        with self.assertRaisesRegex(ValueError,'proof_hash'):maps.validate_selection(path,'0'*64,self.excluded)

    def test_alias_or_external_exclusion_fails(self):
        self.excluded['training'][0]=str(self.root/'outside.map23')
        Path(self.excluded['training'][0]).write_bytes(flatmap(1,1))
        self.excluded['training'].sort()
        with self.assertRaisesRegex(ValueError,'exclusion_outside_bundle'):self.prepare()

    def test_symlink_map_fails(self):
        target=self.bundle/'a.map23';target.unlink();target.symlink_to(self.bundle/'b.map23')
        with self.assertRaisesRegex(ValueError,'symlink'):self.prepare()

    def test_official_engine_hash_not_replaced_by_proof(self):
        with self.assertRaisesRegex(ValueError,'official_engine_pin'):maps.prepare_selection(self.bundle,self.excluded,self.jar)


if __name__=='__main__':unittest.main()
