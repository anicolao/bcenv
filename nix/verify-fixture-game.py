"""Assertions on real headless fixture evidence, deliberately not a skill metric."""
import json
from pathlib import Path

results = [json.loads((Path(p) / 'result.json').read_text())
           for p in ('match-one', 'match-repeat', 'match-swapped')]
for result in results:
    assert result['status'] == 'completed', result
    assert result['winner'] in ('A', 'B'), result
    assert result['process']['status'] == 'succeeded', result
assert results[0]['teams']['A'] == results[2]['teams']['B']
assert results[0]['teams']['B'] == results[2]['teams']['A']
# The inert fixtures tie through every deterministic tiebreak. The official
# engine's GameWorld.setWinnerArbitrary() uses unseeded Math.random(). Require
# the exact declared terminal reason, retain actual winners, and normalize only
# those winner fields before comparing all remaining extracted replay data.
summaries = [json.loads((Path(p) / 'replay-summary.json').read_text())
             for p in ('match-one', 'match-repeat')]
for folder in ('match-one', 'match-repeat'):
    assert 'Reason: The winning team won arbitrarily (coin flip).' in (Path(folder) / 'engine.log').read_text()
observed_winners = [s['winner_slot'] for s in summaries]
for summary in summaries:
    assert len(summary['matches']) == 1
    for record in [summary, summary['matches'][0]]:
        record.pop('winner_slot')
        record.pop('winner_team_id')
if summaries[0] != summaries[1]:
    import difflib
    print(''.join(difflib.unified_diff(json.dumps(summaries[0], indent=2, sort_keys=True).splitlines(True),
                                     json.dumps(summaries[1], indent=2, sort_keys=True).splitlines(True))))
    raise AssertionError('Repeated fixture replay evidence differs beyond coin-flip winners')
Path('repeat-evidence.json').write_text(json.dumps({
    'observed_winners': observed_winners, 'normalized_fields': ['winner_slot', 'winner_team_id'],
    'normalization_reason': 'official final tiebreak uses unseeded Math.random()',
    'other_extracted_fields_equal': True}, indent=2) + '\n')
print('PASS: real verified fixture match, repeat, swapped slots and source package')
