from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

T_MIN_K = 40.0
T_MAX_K = 1600.0
CONTINUITY_EPS_K = 1e-3


@dataclass
class ValidationIssue:
    step_id: int = 0
    field: Optional[str] = None
    code: str = ''
    message: str = ''
    severity: str = 'error'  # error | warning

    def to_dict(self) -> Dict[str, Any]:
        return {
            'step_id': self.step_id,
            'field': self.field,
            'code': self.code,
            'message': self.message,
            'severity': self.severity,
        }


@dataclass
class ProgramValidationResult:
    ok: bool = False
    description_ok: bool = False
    steps_ok: bool = False
    e720_ok: bool = False
    issues: List[ValidationIssue] = field(default_factory=list)

    @property
    def can_create(self) -> bool:
        return self.description_ok and self.steps_ok and self.e720_ok

    def to_dict(self) -> Dict[str, Any]:
        return {
            'ok': self.ok,
            'can_create': self.can_create,
            'description_ok': self.description_ok,
            'steps_ok': self.steps_ok,
            'e720_ok': self.e720_ok,
            'issues': [i.to_dict() for i in self.issues],
            'limits': {'t_min_k': T_MIN_K, 't_max_k': T_MAX_K},
        }


def _parse_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_step_rows(rows: Sequence[Sequence[Any]]) -> List[Tuple[int, float, float, float]]:
    """Return [(step_id, t_start, t_stop, minutes), ...] sorted by step_id."""
    parsed: List[Tuple[int, float, float, float]] = []
    for row in rows or []:
        if len(row) < 4:
            continue
        step_id = int(_parse_float(row[0]) or 0)
        t_start = _parse_float(row[1])
        t_stop = _parse_float(row[2])
        minutes = _parse_float(row[3])
        if step_id <= 0 or t_start is None or t_stop is None or minutes is None:
            continue
        parsed.append((step_id, t_start, t_stop, minutes))
    parsed.sort(key=lambda r: r[0])
    return parsed


def suggest_next_step(rows: Sequence[Sequence[Any]]) -> Tuple[float, float, float]:
    """Default t_start/t_stop/minutes for the next step given existing draft."""
    steps = normalize_step_rows(rows)
    if not steps:
        return (T_MIN_K, min(T_MIN_K + 60.0, T_MAX_K), 15.0)
    last_stop = steps[-1][2]
    t_start = last_stop
    t_stop = min(t_start + 100.0, T_MAX_K)
    if t_stop <= t_start:
        t_stop = t_start
    return (t_start, t_stop, 15.0)


def validate_description(description: str) -> Tuple[bool, List[ValidationIssue]]:
    text = str(description or '').strip()
    if not text:
        return False, [
            ValidationIssue(
                field='description',
                code='description_required',
                message='Enter a program description.',
            )
        ]
    if len(text) > 500:
        return False, [
            ValidationIssue(
                field='description',
                code='description_too_long',
                message='Description must be at most 500 characters.',
            )
        ]
    return True, []


def validate_temperature_steps(rows: Sequence[Sequence[Any]]) -> Tuple[bool, List[ValidationIssue]]:
    issues: List[ValidationIssue] = []
    steps = normalize_step_rows(rows)

    if not steps:
        issues.append(
            ValidationIssue(
                code='no_steps',
                message='Add at least one temperature step.',
            )
        )
        return False, issues

    prev_stop: Optional[float] = None

    for step_id, t_start, t_stop, minutes in steps:
        if minutes <= 0:
            issues.append(
                ValidationIssue(
                    step_id=step_id,
                    field='minutes',
                    code='minutes_non_positive',
                    message=f'Step {step_id}: duration must be greater than 0 minutes.',
                )
            )

        if t_start < T_MIN_K:
            issues.append(
                ValidationIssue(
                    step_id=step_id,
                    field='t_start',
                    code='t_start_below_min',
                    message=f'Step {step_id}: start temperature must be at least {T_MIN_K:g} K (got {t_start:g} K).',
                )
            )
        if t_stop > T_MAX_K:
            issues.append(
                ValidationIssue(
                    step_id=step_id,
                    field='t_stop',
                    code='t_stop_above_max',
                    message=f'Step {step_id}: stop temperature must be at most {T_MAX_K:g} K (got {t_stop:g} K).',
                )
            )
        if t_start >= t_stop:
            issues.append(
                ValidationIssue(
                    step_id=step_id,
                    field='t_stop',
                    code='t_start_not_below_stop',
                    message=(
                        f'Step {step_id}: stop temperature ({t_stop:g} K) must be greater than '
                        f'start ({t_start:g} K).'
                    ),
                )
            )

        if prev_stop is not None:
            if t_start < prev_stop - CONTINUITY_EPS_K:
                issues.append(
                    ValidationIssue(
                        step_id=step_id,
                        field='t_start',
                        code='overlap_with_previous',
                        message=(
                            f'Step {step_id}: start temperature is {t_start:g} K but step {step_id - 1} '
                            f'ends at {prev_stop:g} K — temperatures overlap. Set start to {prev_stop:g} K.'
                        ),
                    )
                )
            elif t_start > prev_stop + CONTINUITY_EPS_K:
                issues.append(
                    ValidationIssue(
                        step_id=step_id,
                        field='t_start',
                        code='gap_after_previous',
                        message=(
                            f'Step {step_id}: start temperature must be {prev_stop:g} K '
                            f'(previous step ends at {prev_stop:g} K), not {t_start:g} K.'
                        ),
                    )
                )

        prev_stop = t_stop

    return (len(issues) == 0, issues)


def validate_e720(
    sweep_mode: int,
    enabled_freqs: Sequence[str],
    range_max: float,
) -> Tuple[bool, List[ValidationIssue]]:
    issues: List[ValidationIssue] = []
    freqs: List[int] = []
    for raw in enabled_freqs or []:
        val = _parse_float(raw)
        if val is not None and val > 0:
            freqs.append(int(val))

    if not freqs:
        issues.append(
            ValidationIssue(
                field='enabled_freqs',
                code='e720_no_frequencies',
                message='Select at least one E7-20 frequency.',
            )
        )

    if range_max <= 0:
        issues.append(
            ValidationIssue(
                field='range_max',
                code='e720_range_max',
                message='Range max Hz must be greater than 0.',
            )
        )

    if int(sweep_mode) == 3 and freqs and range_max < max(freqs):
        issues.append(
            ValidationIssue(
                field='range_max',
                code='e720_range_max_below_enabled',
                message=(
                    f'Range max ({range_max:g} Hz) must be at least the highest enabled frequency '
                    f'({max(freqs)} Hz) in Range mode.'
                ),
            )
        )

    return (len(issues) == 0, issues)


def validate_new_program(
    description: str,
    steps: Sequence[Sequence[Any]],
    sweep_mode: int,
    enabled_freqs: Sequence[str],
    range_max: float,
) -> ProgramValidationResult:
    result = ProgramValidationResult()
    desc_ok, desc_issues = validate_description(description)
    steps_ok, step_issues = validate_temperature_steps(steps)
    e720_ok, e720_issues = validate_e720(sweep_mode, enabled_freqs, range_max)

    result.description_ok = desc_ok
    result.steps_ok = steps_ok
    result.e720_ok = e720_ok
    result.issues = desc_issues + step_issues + e720_issues
    result.ok = result.can_create
    return result


def steps_from_form(form: Mapping[str, Any]) -> List[List[Any]]:
    from webui.program_steps import parse_step_field_updates

    updates = parse_step_field_updates(form)
    if not updates:
        return []
    rows: List[List[Any]] = []
    for step_id in sorted(updates):
        fields = updates[step_id]
        if 't_start' not in fields or 't_stop' not in fields or 'minutes' not in fields:
            continue
        rows.append([step_id, fields['t_start'], fields['t_stop'], fields['minutes']])
    return rows


def validate_new_program_form(form: Mapping[str, Any]) -> ProgramValidationResult:
    description = str(form.get('description', '') or '')
    sweep_mode = int(_parse_float(form.get('sweep_mode', 0)) or 0)
    range_max = float(_parse_float(form.get('range_max', 10000)) or 10000)
    enabled = form.getlist('enabled_freqs') if hasattr(form, 'getlist') else []
    if not enabled and 'enabled_freqs' in form:
        raw = form.get('enabled_freqs')
        if isinstance(raw, list):
            enabled = raw
        elif raw:
            enabled = [raw]
    step_rows = steps_from_form(form)
    return validate_new_program(description, step_rows, sweep_mode, enabled, range_max)


def _main() -> int:
    """CLI: verify steps JSON from stdin or file. Example rows: [[1,40,100,15],[2,100,200,10]]"""
    if len(sys.argv) > 1 and sys.argv[1] not in ('-', '--'):
        with open(sys.argv[1], encoding='utf-8') as fh:
            payload = json.load(fh)
    else:
        payload = json.load(sys.stdin)

    description = str(payload.get('description', '') or '')
    steps = payload.get('steps', [])
    e720 = payload.get('e720', {})
    result = validate_new_program(
        description,
        steps,
        int(e720.get('sweep_mode', 0)),
        list(e720.get('enabled_freqs', [])),
        float(e720.get('range_max', 10000)),
    )
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.can_create else 1


if __name__ == '__main__':
    raise SystemExit(_main())
