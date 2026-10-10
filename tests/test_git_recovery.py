"""True local-Git composition coverage, separate from fake-provider/model cases."""
from pathlib import Path
import tempfile
import unittest

from tests.git_recovery_fixture import GitRecoveryJourney


class GitRecoveryJourneyTests(unittest.TestCase):
    def test_post_commit_producer_loss_and_fresh_process_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = GitRecoveryJourney(Path(temporary)).run()
        self.assertEqual("passed", result["status"])
        self.assertEqual(["produce", "read", "reconcile", "read", "retry",
                          "conflicting_evidence", "stale_blob", "race"],
                         [record["mode"] for record in result["processes"]])


if __name__ == "__main__":
    unittest.main()
