"""Identity checks prevent rescoring with a different method implementation."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from slm_math_evaluation import analysis
from slm_math_evaluation.data import DataError
from slm_math_evaluation.evaluation import (EXTRACTOR_ID, EXTRACTOR_VERSION,
                                            PRIMARY_PIN, SECONDARY_PIN)


class MethodIdentityTests(unittest.TestCase):
    def test_rejects_changed_extractor_or_scorer_pin(self):
        extractor = {"id": EXTRACTOR_ID, "version": EXTRACTOR_VERSION}
        scorers = [{"id": "prm800k-grader", "pin": PRIMARY_PIN},
                   {"id": "math-verify", "pin": SECONDARY_PIN}]
        run = SimpleNamespace(
            protocol={"extraction": extractor,
                      "scorers": {"primary": scorers[0], "secondary": scorers[1]}},
            metadata={"extractor": extractor, "scorers": scorers})
        self.assertEqual(analysis._method_identities(run), (extractor, scorers))
        for altered in ({"extractor": {**extractor, "version": "older"}},
                        {"scorers": [scorers[0], {**scorers[1], "pin": "older"}]}):
            changed = SimpleNamespace(protocol=run.protocol, metadata={**run.metadata, **altered})
            with self.assertRaises(DataError) as caught:
                analysis._method_identities(changed)
            self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
