import unittest

from webui.temperature_validation import (
    T_MAX_K,
    T_MIN_K,
    suggest_next_step,
    validate_new_program,
    validate_temperature_steps,
)


class TemperatureValidationTests(unittest.TestCase):
    def test_gap_between_steps(self) -> None:
        steps = [[1, 40, 100, 10], [2, 200, 300, 10]]
        ok, issues = validate_temperature_steps(steps)
        self.assertFalse(ok)
        self.assertTrue(any(i.code == 'gap_after_previous' for i in issues))

    def test_overlap_between_steps(self) -> None:
        steps = [[1, 40, 100, 10], [2, 90, 120, 10]]
        ok, issues = validate_temperature_steps(steps)
        self.assertFalse(ok)
        self.assertTrue(any(i.code == 'overlap_with_previous' for i in issues))

    def test_valid_chain(self) -> None:
        steps = [[1, 40, 100, 10], [2, 100, 200, 10], [3, 200, 300, 5]]
        ok, issues = validate_temperature_steps(steps)
        self.assertTrue(ok)
        self.assertEqual(issues, [])

    def test_out_of_range(self) -> None:
        steps = [[1, 30, 100, 10]]
        ok, issues = validate_temperature_steps(steps)
        self.assertFalse(ok)
        self.assertTrue(any(i.code == 't_start_below_min' for i in issues))

    def test_suggest_first_and_next(self) -> None:
        self.assertEqual(suggest_next_step([]), (T_MIN_K, 100.0, 15.0))
        self.assertEqual(suggest_next_step([[1, 40, 100, 10]]), (100.0, 200.0, 15.0))

    def test_full_program_requires_description(self) -> None:
        result = validate_new_program('', [[1, 40, 100, 10]], 0, ['1000'], 10000)
        self.assertFalse(result.can_create)
        self.assertFalse(result.description_ok)


if __name__ == '__main__':
    unittest.main()
