"""Trusted versioned capacity profiles, not candidate-controlled request fields."""
from types import MappingProxyType

UNSET = object()
LEGACY = 'legacy-v1'
CONTINUITY = 'continuity-v1'
LEGACY_DEFAULTS = {'max_submissions': 12, 'max_reads': 2000, 'max_replay_queries': 150,
                   'max_notes': 24, 'max_run_seconds': 2700, 'max_mailbox_requests': 2200,
                   'require_experiment_notes': False}
CONTINUITY_VALUES = {'max_submissions': 24, 'max_reads': 2000, 'max_replay_queries': 450,
                     'max_notes': 72, 'max_run_seconds': 21600, 'max_mailbox_requests': 2200,
                     'require_experiment_notes': True}
DERIVED = {'games_per_submission': 8, 'validation_game_budget': 16,
           'max_pending': 2, 'worker_count': 1}


def resolve_profile(profile=UNSET, **values):
    profile = LEGACY if profile is UNSET else profile
    if type(profile) is not str or profile not in (LEGACY, CONTINUITY):
        raise ValueError('invalid_trial_profile')
    defaults = CONTINUITY_VALUES if profile == CONTINUITY else LEGACY_DEFAULTS
    if set(values) - set(defaults):
        raise ValueError('invalid_profile_field')
    resolved = {k: defaults[k] if values.get(k, UNSET) is UNSET else values[k] for k in defaults}
    for key, value in resolved.items():
        if key == 'require_experiment_notes':
            if type(value) is not bool:
                raise ValueError('invalid_note_config')
        elif type(value) is not int or value < 1:
            raise ValueError('invalid_deadline_config' if key == 'max_run_seconds' else
                             'invalid_note_config' if key == 'max_notes' else 'invalid_profile_limit')
    if profile == CONTINUITY and resolved != CONTINUITY_VALUES:
        raise ValueError('profile_limit_mismatch')
    if profile == LEGACY and resolved['max_notes'] > 32:
        raise ValueError('invalid_note_config')
    # Legacy finalist shape/deadline checks remain in FinalistValidation.
    training = 192 if profile == CONTINUITY else 64
    return MappingProxyType(dict(resolved, profile=profile, **DERIVED,
                                 training_game_budget=training, total_game_budget=training + 16,
        service_storage_high_water_bytes=1073741824 if profile == CONTINUITY else None,
        storage_semantics='checkpoint-admission-v1' if profile == CONTINUITY else 'none',
        mailbox_max_seconds=21600 if profile == CONTINUITY else 86400))


def trial_options(config):
    """Resolve before readiness; every capacity field is represented in parity."""
    profile = config.get('profile', UNSET)
    # The old entrypoints did not forward these constructor-only knobs. Preserve
    # that legacy behavior; only the opt-in profile activates full config binding.
    forwarded = set(LEGACY_DEFAULTS)
    if profile is UNSET or profile == LEGACY:
        forwarded -= {'max_reads', 'max_replay_queries', 'max_mailbox_requests'}
    bounds = resolve_profile(profile, **{k: config[k] for k in forwarded if k in config})
    if bounds['profile'] == CONTINUITY:
        allowed = set(LEGACY_DEFAULTS) | set(DERIVED) | {
            'profile', 'command', 'games', 'timeout', 'match_timeout', 'validation_games',
            'training_game_budget', 'total_game_budget'}
        if set(config) - allowed or config.get('validation_games') is None:
            raise ValueError('invalid_continuity_config')
        for key in set(DERIVED) | {'training_game_budget', 'total_game_budget'}:
            if key in config and (type(config[key]) is not int or config[key] != bounds[key]):
                raise ValueError('profile_limit_mismatch')
    return {k: bounds[k] for k in LEGACY_DEFAULTS} | {
        'profile': bounds['profile'], 'validation_games': config.get('validation_games')}
