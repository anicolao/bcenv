"""Public-only deterministic map geometry selection; no engine invocation.

Dimensions follow the pinned official GameMap/Vec FlatBuffer schema and
GameMapIO.Serial.deserialize: maxCorner minus minCorner. This is dimensional
metadata validation, not engine legality, terrain coverage or difficulty scoring.
"""
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import stat
import struct
import zipfile

ENGINE_SHA256 = '2b6db98e312e0e509d599acc46bffbb55c2569de0f12c8214a85ee57625f15f2'
REVISION = '9b890f83672c98b81cc153f618bd56e1ab6134b5'
PARSER_VERSION = 'bc23-flatbuffer-corners-v1'
SOURCE_PINS = {
    'schema/battlecode.fbs':'0b8a63470143447f90133f7585717b73745b353fba6610e29c6875d557f66c91',
    'engine/src/main/battlecode/schema/GameMap.java':'9d077a9e246038be473a87cda40504deb3e66074c53351f049bc300ec41636be',
    'engine/src/main/battlecode/schema/Vec.java':'e9136a95123506a7022f987570375a61dcf9f6c0455d5e9a6ced8fcf8f889df2',
    'engine/src/main/battlecode/world/GameMapIO.java':'ebc28f4a9cfbf900324f803f104b344d9c61b15f9461f918005c680e8e04bcda',
}
MAX_MAP_BYTES = 4 * 1024 * 1024
MAX_MAPS = 1024
MAX_PROOF_BYTES = 1024 * 1024


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def dimensions(data):
    """Bounds-check the ordinary (not size-prefixed) GameMap FlatBuffer."""
    if type(data) is not bytes or not 4 <= len(data) <= MAX_MAP_BYTES:
        raise ValueError('invalid_map_size')
    def read(fmt,offset):
        size=struct.calcsize(fmt)
        if offset<0 or offset+size>len(data):raise ValueError('map_bounds')
        return struct.unpack_from(fmt,data,offset)
    table=read('<I',0)[0]
    if table<4 or table%4:raise ValueError('invalid_map_root')
    vtable=table-read('<i',table)[0]
    size,object_size=read('<HH',vtable)
    if vtable<4 or size<10 or size%2 or vtable+size>len(data) or object_size<4 or table+object_size>len(data):
        raise ValueError('invalid_map_table')
    if not (vtable+size<=table or vtable>=table+object_size):raise ValueError('overlapping_map_table')
    def field(slot,length):
        offset=read('<H',vtable+4+slot*2)[0]
        if offset<4 or offset+length>object_size:raise ValueError('missing_or_invalid_map_field')
        return table+offset
    name_field=field(0,4)
    relative=read('<I',name_field)[0]
    if relative<4:raise ValueError('invalid_map_name_offset')
    name=name_field+relative
    length=read('<I',name)[0]
    if not 0<length<=4096 or name+4+length>=len(data) or data[name+4+length]!=0:
        raise ValueError('invalid_map_name')
    try:data[name+4:name+4+length].decode('utf-8')
    except UnicodeDecodeError as error:raise ValueError('invalid_map_name') from error
    low,high=field(1,8),field(2,8)
    regions=sorted(((name_field,name_field+4),(low,low+8),(high,high+8)))
    if low%4 or high%4 or any(a[1]>b[0] for a,b in zip(regions,regions[1:])):
        raise ValueError('invalid_map_corners')
    minx,miny=read('<ii',low);maxx,maxy=read('<ii',high)
    width,height=maxx-minx,maxy-miny
    if width<=0 or height<=0:raise ValueError('nonpositive_map_dimensions')
    return {'width':width,'height':height}


def select_maps(inventory, training, validation):
    """Pure exact-rational greedy max-min selection; basename codepoint ties."""
    if type(inventory) is not list or not 1<=len(inventory)<=MAX_MAPS:
        raise ValueError('invalid_inventory')
    entries={}
    for item in inventory:
        if (type(item) is not dict or type(item.get('basename')) is not str or
                not item['basename'].endswith('.map23') or Path(item['basename']).name!=item['basename'] or
                any(type(item.get(k)) is not int or item[k]<=0 for k in ('width','height'))):
            raise ValueError('invalid_metadata')
        if item['basename'] in entries:raise ValueError('duplicate_basename')
        entries[item['basename']]=item
    if (type(training) is not list or type(validation) is not list or len(training)!=2 or len(validation)!=2 or
            any(type(n) is not str for n in training+validation) or len(set(training+validation))!=4 or
            any(n not in entries for n in training+validation)):
        raise ValueError('invalid_public_exclusions')
    ranges={key:max(x[key] for x in inventory)-min(x[key] for x in inventory) for key in ('width','height')}
    eligible=set(entries)-set(training+validation)
    if len(eligible)<2:raise ValueError('insufficient_eligible_maps')
    def distance(a,b):
        return sum((Fraction(abs(entries[a][k]-entries[b][k]),ranges[k]) if ranges[k] else Fraction(0)) for k in ranges)
    anchors=list(training);selected=[];scores=[]
    for _ in range(2):
        candidate_scores={name:min(distance(name,anchor) for anchor in anchors) for name in eligible}
        best=min(eligible,key=lambda name:(-candidate_scores[name],name))
        score=candidate_scores[best]
        selected.append(best);scores.append({'numerator':score.numerator,'denominator':score.denominator})
        eligible.remove(best);anchors.append(best)
    return {'selected':selected,'minimum_distances':scores,'ranges':ranges,
            'range_scope':'full pinned inventory before exclusions','tie_break':'Unicode codepoint basename, case preserved'}


def _path(value, directory=False):
    path=Path(value)
    if not path.is_absolute():raise ValueError('absolute_path_required')
    for p in (path,*path.parents):
        if p.is_symlink():raise ValueError('symlink_not_allowed')
    if path.resolve()!=path:raise ValueError('noncanonical_path')
    mode=path.stat().st_mode
    if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
        raise ValueError('invalid_path_type')
    return path


def _bytes(path,limit):
    before=path.stat()
    if not 0<before.st_size<=limit:raise ValueError('input_size_limit')
    with path.open('rb') as f:data=f.read(limit+1)
    after=path.stat()
    if len(data)!=before.st_size or (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
        raise ValueError('input_changed')
    return data


def prepare_selection(bundle_dir, excludedmaps, engine_jar):
    """Read ONLY declared public bundle and pinned official jar; emit proof data.

    excludedmaps contains exactly training/validation sorted unique absolute
    paths, each identifying two maps from this same flat bundle directory.
    """
    bundle=_path(bundle_dir,directory=True);jar=_path(engine_jar)
    jar_hash=digest(_bytes(jar,64*1024*1024))
    if jar_hash!=ENGINE_SHA256:raise ValueError('official_engine_pin_mismatch')
    names=[];inventory=[]
    paths=sorted(bundle.iterdir(),key=lambda p:p.name)
    if not 1<=len(paths)<=MAX_MAPS:raise ValueError('invalid_inventory_size')
    for path in paths:
        _path(path)
        if path.suffix!='.map23':raise ValueError('unexpected_bundle_entry')
        if path.name in names:raise ValueError('duplicate_basename')
        names.append(path.name);data=_bytes(path,MAX_MAP_BYTES);metadata=dimensions(data)
        inventory.append(dict(basename=path.name,path=str(path),sha256=digest(data),
                              metadata_sha256=digest(canonical(metadata)),**metadata))
    # Corpus membership comes only from the published release, not a private list.
    with zipfile.ZipFile(jar) as archive:
        official={}
        for entry in archive.infolist():
            if not (entry.filename.startswith('battlecode/world/resources/') and entry.filename.endswith('.map23')):continue
            basename=Path(entry.filename).name
            if basename in official:raise ValueError('duplicate_official_basename')
            if not 0<entry.file_size<=MAX_MAP_BYTES:raise ValueError('official_map_size')
            official[basename]=digest(archive.read(entry))
        if official!={x['basename']:x['sha256'] for x in inventory}:raise ValueError('official_corpus_mismatch')
    if type(excludedmaps) is not dict or set(excludedmaps)!={'training','validation'}:
        raise ValueError('invalid_public_exclusions')
    excluded={};excluded_paths={}
    for key in ('training','validation'):
        values=excludedmaps[key]
        if type(values) is not list or any(type(v) is not str for v in values) or values!=sorted(set(values)):
            raise ValueError('invalid_public_exclusion_paths')
        checked=[_path(v) for v in values]
        if any(p.parent!=bundle for p in checked):raise ValueError('exclusion_outside_bundle')
        excluded[key]=[p.name for p in checked];excluded_paths[key]=[str(p) for p in checked]
    selection=select_maps(inventory,excluded['training'],excluded['validation'])
    inputs={'inventory':inventory,'excluded_maps':excluded_paths,'engine_sha256':jar_hash}
    result={'schema':'bcenv.public-transfer-map-selection.v1','source_revision':REVISION,
            'parser_version':PARSER_VERSION,'parser_sha256':digest(Path(__file__).read_bytes()),
            'official_parser_source_sha256':SOURCE_PINS,'bundle_directory':str(bundle),'engine_jar':str(jar),
            **inputs,'input_sha256':digest(canonical(inputs)),'selection':selection,
            'output_sha256':digest(canonical(selection))}
    if len(canonical(result))>MAX_PROOF_BYTES:raise ValueError('proof_limit')
    return result


def validate_selection(path, sha256, excludedmaps):
    """Recompute the complete public proof before trusting selected map paths."""
    path=_path(path);data=_bytes(path,MAX_PROOF_BYTES)
    if type(sha256) is not str or digest(data)!=sha256:raise ValueError('selection_proof_hash_mismatch')
    def unique(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('duplicate_proof_key')
            result[key]=value
        return result
    proof=json.loads(data,object_pairs_hook=unique)
    if type(proof) is not dict:raise ValueError('invalid_selection_proof')
    expected=prepare_selection(proof['bundle_directory'],excludedmaps,proof['engine_jar'])
    if proof!=expected:raise ValueError('selection_proof_changed')
    return tuple(str(Path(proof['bundle_directory'])/name) for name in proof['selection']['selected'])
