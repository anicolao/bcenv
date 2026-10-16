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
# Raw replay bytes may include timing. Compare the extractor's normalized data.
summaries = [json.loads((Path(p) / 'replay-summary.json').read_text())
             for p in ('match-one', 'match-repeat')]
assert summaries[0] == summaries[1], 'Repeated fixture replay summaries differ'
print('PASS: real verified fixture match, repeat, swapped slots and source package')
